"""geoguesshelper-server — 로컬 브라우저 UI.

    uv run geoguesshelper-server        # → http://localhost:8799/ 자동 오픈

구조
    보고서 생성은 전부 **작업 큐**를 지난다(jobs.py). 여러 탭에서 눌러도 서버가 한 번에
    하나씩 순차 처리한다. 프론트의 탭별 busy 플래그는 이제 보조 표시일 뿐이고, 동시성의
    진실은 서버에 있다 — 새로고침하거나 브라우저 창을 하나 더 열어도 우회되지 않는다.

    조사는 기준 언어(영어 우선)로 **한 번만** 하고 나머지 언어는 번역한다. 조사 직전에
    지식 저장소를 회상해 이미 아는 사실은 재검색하지 않고, 조사 직후 새 사실을 원자로
    적재한다(knowledge.py).

엔드포인트
    GET  /                          → webui/index.html
    GET  /api/config                → 프론트 설정
    POST /api/extract               → {"url"} → 포즈
    POST /api/capture               → {"pose","mode"?} → 캡처 결과
    POST /api/analyze               → {"files","lang"} → 비전 분석
    GET  /api/pano-photo            → 파노의 lh3 이미지 토큰(제3자 파노 우회용)
    GET  /api/pano-image            → 그 토큰 + 시점 → JPEG (서버 경유 · 확장/백신 차단 회피)
    POST /api/jobs/scene-report     → 큐 등록: 캡처 → 분석 → 리서치 → 보고서
    POST /api/jobs/report           → 큐 등록: 캡처들 → 보고서
    GET  /api/jobs                  → 큐 상태 + 목록
    GET  /api/jobs/{id}             → 단일 작업
    GET  /api/jobs/{id}/stream      → SSE 진행 스트림
    POST /api/jobs/{id}/cancel      → 취소
    GET  /api/knowledge             → 저장소 통계
    GET  /api/knowledge/atoms       → 원자 목록(검색)
    POST /api/knowledge/synthesize  → 종합 보고서(재조사 없음)
    GET  /captures/{name}           → 캡처 이미지
    GET  /reports/{name}            → 생성된 보고서
    GET  /knowledge/{path}          → 지식 .md 파일
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import re
import socket
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import capture as capture_mod
from . import (cleanup, i18n, jobs, knowledge, linkresolver, llm, quota, streetview, tenancy,
               translate)
from . import report as report_mod
from . import research as research_mod
from .analyze import analyze_captures
from . import __version__
from .config import Settings, find_report, load_settings

WEBUI_DIR = Path(__file__).parent / "webui"

# 블로킹 작업 전용 풀. 기본 ThreadPoolExecutor(20슬롯)를 Chromium 과 16분짜리 Claude
# 호출이 나눠 쓰다가 고갈되면 그 뒤 작업이 조용히 영원히 대기하던 문제를 피한다.
_POOL: ThreadPoolExecutor | None = None
_CAPTURE_SEM: asyncio.Semaphore | None = None
_QUEUE: jobs.JobQueue | None = None

# 이 요청의 주인. 라우트 시그니처를 전부 고치지 않고 _submit 이 읽을 수 있게 한다
# (미들웨어가 설정하고, 요청이 끝나면 되돌린다).
_CURRENT_USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar("ggh_user", default="")

# 가입 전 방문자의 지문 (쿠키 id, 소금 친 IP 해시). 같은 이유로 미들웨어가 설정한다 —
# 무료 1건을 누가 썼는지는 잡이 끝난 뒤에 기록되는데, 그때는 request 가 이미 없다.
_CURRENT_ANON: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar(
    "ggh_anon", default=("", ""))

# 브라우저가 자기 키를 실어 보내는 헤더. 값은 **어디에도 저장하지 않는다** —
# 로그에도 jobs.jsonl 에도 남기지 않고, 그 요청/그 잡 동안만 메모리에 든다.
_KEY_HEADER = "x-llm-key"

# 잡은 요청 컨텍스트 밖에서 돈다. 회원 키는 DB 에서 다시 풀면 되지만, 헤더로 온 키는
# 그럴 데가 없다. 그렇다고 payload 에 넣으면 jobs.jsonl 에 평문으로 남는다 —
# 그래서 **메모리에만** 들고 잡이 끝나면 지운다.
_JOB_CREDS: dict[str, object] = {}


def _secret_provider(raw: str) -> str:
    from . import secretbox

    return secretbox.provider_of(raw)


def _norm_langs(value) -> list[str]:
    """문자열/리스트 → 정규화·중복제거된 언어코드 리스트(비면 기본 언어)."""
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for x in value or []:
        n = i18n.normalize(x)
        if n not in out:
            out.append(n)
    return out or [i18n.DEFAULT_LANG]


async def _in_pool(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_POOL, fn, *args)


async def _fetch_elevation(settings: Settings, lat, lng) -> float | None:
    """보고서 헤더의 "지점 고도" 한 줄(§stages 4단계) — 실패해도 보고서를 막지 않는다."""
    if lat is None or lng is None or not settings.has_static_key:
        return None
    try:
        return await streetview.elevation(float(lat), float(lng), settings.effective_static_key)
    except (TypeError, ValueError):
        return None


# ── 보고서 파이프라인 ─────────────────────────────────────────────
def _build_reports_sync(
    job: jobs.Job,
    files: list[str],
    langs: list[str],
    settings: Settings,
    primary: dict | None,
    do_research: bool,
    start_lat,
    start_lng,
    panos: dict | None = None,
    elevation_m: float | None = None,
) -> dict:
    """기준 언어로 1회 조사 → 나머지 언어는 번역 → 리포트 1개.

    예전에는 언어마다 비전 분석과 웹 리서치를 통째로 다시 돌렸다. 비용·지연이 N배였고,
    언어별 결과가 서로 다른 도시를 주장해 한 파일 안에서 대상이 바뀔 수 있었다.
    이제 사실은 한 벌만 만들고, 언어는 그 위의 표현 계층일 뿐이다.
    """
    import time as _time

    base = i18n.pick_base_lang(langs)
    errors: list[dict] = []
    total_cost = 0.0
    t0 = _time.monotonic()

    def over_budget() -> bool:
        """예산을 넘었는가 — 넘으면 **선택적** 단계만 건너뛴다(보고서는 반드시 만든다)."""
        return (_time.monotonic() - t0) > settings.job_budget_s

    def elapsed() -> float:
        return _time.monotonic() - t0

    # ── 1) 분석 ─────────────────────────────────────────────────
    job.raise_if_canceled()
    base_result: dict | None = None
    analysis_lang = base
    pre_known: list = []       # 분석 전 회상(시작 좌표) — 결과 knowledge.pre_recalled 로 남긴다
    if isinstance(primary, dict) and (primary.get("analysis") or primary.get("best_guess")):
        # 클라이언트가 이미 분석 결과를 갖고 있으면 재분석하지 않는다(비전 호출 절약).
        if not primary.get("analysis") and primary.get("best_guess"):
            primary = {"status": "OK", "analysis": primary, "lang": i18n.normalize(primary.get("lang") or base)}
        base_result = primary
        analysis_lang = i18n.normalize(primary.get("lang") or base)
        job.emit("analyze", f"기존 분석 재사용 ({i18n.native_name(analysis_lang)})", 20)
    else:
        job.emit("analyze", f"장면 분석 중… ({len(files)}장 · {i18n.native_name(base)})", 10)
        paths = [settings.captures_dir / Path(f).name for f in files]
        # 분석 **전에** 시작 좌표로 회상해 프롬프트에 넣는다(기획 260906 §prompt "analyze_captures 에 회상 주입" —
        # 장면 분석이 유일하게 원자를 안 보던 단계였다). 정정 루프가 만든 "X 처럼 보이지만 X2" 판별자가
        # 여기서 다음 판단을 유도한다. aided 계층(지도가 있어야만 나온 사실)은 recall 이 기본으로 뺀다.
        # 회상 실패는 분석을 막지 않는다.
        if settings.knowledge_enabled and start_lat is not None and start_lng is not None:
            try:
                pre_known = knowledge.recall(
                    settings, lat=start_lat, lng=start_lng, kinds=None, exclude_tiers=("aided",),
                    limit=settings.knowledge_recall_limit, ctx={"job": job.id, "mode": "aided"},
                )
            except Exception as exc:  # noqa: BLE001
                job.emit("analyze", f"⚠ 사전 회상 실패(무시): {type(exc).__name__}", 10)
                pre_known = []
            if pre_known:
                job.emit("analyze", f"사전 회상 {len(pre_known)}건 — 분석 프롬프트에 주입", 12)
        base_result = analyze_captures(paths, settings, base, known=pre_known)
        if base_result.get("status") == "OK":
            total_cost += base_result.get("cost_usd") or 0.0

    if not base_result or base_result.get("status") != "OK" or not base_result.get("analysis"):
        msg = (base_result or {}).get("message") or (base_result or {}).get("status") or "분석 실패"
        return {"status": "FAIL", "reports": [], "errors": [{"lang": base, "message": msg}],
                "message": msg, "cost_usd": round(total_cost, 4)}

    if panos:
        # 캡처별 pano 좌표·방위 — P1(지점 단서) 적재가 보고서 시작 좌표가 아니라 **그 캡처의
        # 실측 좌표**를 쓸 수 있게 남긴다. images[] 자체는 건드리지 않는다(report._analyzed_files
        # 가 Path(a).name 으로 문자열을 기대한다) — 별도 필드로 붙인다.
        base_result["image_panos"] = {
            Path(k).name: v for k, v in panos.items() if isinstance(v, dict) and Path(k).name
        }
    if elevation_m is not None:
        base_result["elevation_m"] = elevation_m

    analysis = base_result.get("analysis") or {}
    place = analysis.get("best_guess") or {}
    place_label = ", ".join(
        x for x in [place.get("city"), place.get("region_or_state"), place.get("country")] if x
    )
    job.emit("analyzed", f"식별: {place.get('city') or place.get('country') or '미상'}", 30)

    # ── 2) 지식 회상 — 이미 아는 것은 다시 조사하지 않는다 ────────
    job.raise_if_canceled()
    known: list = []
    if settings.knowledge_enabled:
        tags = [c.get("category") for c in (analysis.get("cues") or []) if isinstance(c, dict)]
        tags += [lm.get("type") for lm in (analysis.get("landmarks") or []) if isinstance(lm, dict)]
        ents = [lm.get("name") for lm in (analysis.get("landmarks") or []) if isinstance(lm, dict)]
        known = knowledge.recall(
            settings, lat=start_lat, lng=start_lng, place=place,
            tags=[t for t in tags if t], entities=[e for e in ents if e],
            limit=settings.knowledge_recall_limit, char_budget=settings.knowledge_recall_chars,
        )
        if known:
            # ("재조사 생략" 아님 — 리서치는 그대로 돌고, 아는 사실은 프롬프트로 전달돼
            #  같은 것을 다시 검색하지 않게 한다.)
            job.emit("recall", f"지식 저장소에서 관련 사실 {len(known)}건 회상 — 리서치에 전달", 35)

    # ── 3) 리서치 (기준 언어 1회) ────────────────────────────────
    job.raise_if_canceled()
    base_research = None
    if do_research and settings.has_anthropic:
        job.emit("research", f"웹 리서치 중… ({i18n.native_name(base)})", 40)
        stop = lambda: job.cancel_requested   # noqa: E731 — 긴 호출 중에도 취소가 먹히도록
        if settings.parallel_inside_job:
            r = research_mod.research_parallel(
                place, settings, base, known=known, should_stop=stop,
                on_progress=lambda m: job.emit("research", m, 45),
            )
        else:
            r = research_mod.research_location(place, settings, base, known=known, should_stop=stop)
        total_cost += r.get("cost_usd") or 0.0
        if r.get("status") == "OK":
            base_research = r
            job.emit(
                "researched",
                f"리서치 완료 — 검색 {r.get('searches', 0)}회"
                + (f", 기존 지식 {len(r.get('reused_atom_ids') or [])}건 재사용" if r.get("reused_atom_ids") else ""),
                60,
            )
        else:
            errors.append({"lang": base, "message": r.get("message") or "리서치 실패(프로파일 없이 진행)"})

    # ── 4) 언어별 번역 ──────────────────────────────────────────
    research_lang = base
    base_analysis = base_result.get("analysis")
    base_profile = (base_research or {}).get("profile")
    # 언어들은 서로 독립이므로 **동시에** 번역한다. 예전에는 순차라 언어 수만큼 시간이 쌓였다
    # (실측 55~71초 × N). 이제 임계경로는 가장 느린 언어 1개다.
    from . import llm as _llm

    job.raise_if_canceled()
    todo = []          # (lang, need_a, need_p)
    sections_by_lang: dict[str, dict] = {}
    for lang in langs:
        need_a = lang != analysis_lang
        need_p = base_profile is not None and lang != research_lang
        if not need_a and not need_p:
            sections_by_lang[lang] = {"lang": lang, "result": base_result, "research": base_research}
        else:
            todo.append((lang, need_a, need_p))

    # 스크립트(초안+검증)는 번역과 **서로 독립**이다 — 둘 다 기준 언어 사실만 쓴다.
    # 그래서 같은 배치에 넣어 동시에 돌린다. 임계경로가 둘 중 느린 쪽 하나로 줄어든다.
    def make_script():
        from . import script as script_mod

        return ("__script__", script_mod.build_script(
            settings, analysis=base_analysis, profile=base_profile, atoms=known,
            place=place_label, lat=start_lat, lng=start_lng, lang=base,
            should_stop=lambda: job.cancel_requested,
            on_progress=lambda m: job.emit("script", m, 70),
        ))

    batch = []
    if todo:
        job.emit("translate", f"{len(todo)}개 언어 동시 번역 중…", 65)

        def make_tr(lang, need_a, need_p):
            def run():
                return lang, translate.translate_bundle(
                    base_analysis if need_a else None,
                    base_profile if need_p else None,
                    None, lang, settings,
                    base_lang=analysis_lang if need_a else research_lang,
                )
            return run

        batch = [make_tr(*t) for t in todo]
    if settings.script_first and not over_budget():
        batch.append(make_script)

    script_doc = None
    script_skip = None      # 스크립트 단계가 SKIP 으로 끝났으면 그 이유 — 침묵 폴백 금지
    if batch:
        for res in _llm.gather(batch, settings):
            if isinstance(res, Exception):
                # 예전엔 여기서 조용히 넘겼다. 그래서 번역이 통째로 실패해도
                # 작업 로그에는 errors:[] 로 찍히고 한국어 탭이 영어인 보고서가 나갔다.
                errors.append({"lang": "?", "message": f"병렬 단계 실패: {type(res).__name__}: {res}"})
                job.emit("translate", f"⚠ 병렬 단계 실패: {type(res).__name__}", 66)
                continue
            lang, tb = res
            if lang == "__script__":
                total_cost += tb.get("cost_usd") or 0.0
                if tb.get("status") == "OK":
                    script_doc = tb["script"]
                    job.emit("script",
                             f"스크립트 완료 — 초안 {tb.get('drafted_by')} · 검증 "
                             + (f"{tb.get('verified_by')} (지적 {len(tb.get('unsupported') or [])}건)"
                                if tb.get("checked") else "생략"),
                             78)
                else:
                    # 스크립트는 부가 산출물이라 실패해도 보고서는 나간다 — 하지만 침묵은
                    # 금물이다(260812 오진 기록의 '조용한 폴백' 패턴). 로그와 결과에 남긴다.
                    script_skip = tb.get("reason") or "unknown"
                    errors.append({"lang": "*", "message": f"스크립트 생략: {script_skip}"})
                    job.emit("script", f"⚠ 스크립트 생략 — {script_skip}", 78)
                continue
            need_a = lang != analysis_lang
            need_p = base_profile is not None and lang != research_lang
            total_cost += tb.get("cost_usd") or 0.0
            # **번역이 실제로 일어났는가**를 확인한다. translate_bundle 은 실패해도 원문을
            # 담아 돌려주므로, 반환값이 있다는 것만으로는 성공의 증거가 되지 못한다.
            done_n, total_n = tb.get("translated") or 0, tb.get("total") or 0
            bad = tb.get("failed_chunks") or 0
            untranslated = total_n > 0 and done_n == 0
            if untranslated or bad:
                why = "; ".join(tb.get("errors") or []) or "원인 미상"
                msg = (f"{i18n.native_name(lang)} 번역 "
                       + ("전부 실패" if untranslated else f"일부 실패({bad}/{tb.get('chunks')} 덩이)")
                       + f" — {done_n}/{total_n}개만 번역됨. {why}")
                errors.append({"lang": lang, "message": msg})
                job.emit("translate", "⚠ " + msg, 68)
            res_l = dict(base_result)
            res_l["lang"] = lang
            res_l["translation_stats"] = {"translated": done_n, "total": total_n,
                                          "failed_chunks": bad, "base_lang": analysis_lang}
            if need_a and tb.get("analysis"):
                res_l["analysis"] = tb["analysis"]
            rs_l = None
            if base_research is not None:
                rs_l = dict(base_research)
                rs_l["lang"] = lang
                if need_p and tb.get("profile"):
                    rs_l["profile"] = tb["profile"]
            sections_by_lang[lang] = {"lang": lang, "result": res_l, "research": rs_l}

    # 원래 선택 순서 유지(첫 번째가 통합 리포트의 기본 표시 언어)
    sections: list[dict] = [sections_by_lang[l] for l in langs if l in sections_by_lang]

    if not sections:
        return {"status": "FAIL", "reports": [], "errors": errors or [{"lang": base, "message": "생성할 언어가 없습니다."}],
                "message": "생성할 언어가 없습니다.", "cost_usd": round(total_cost, 4)}

    # ── 4.5) 스크립트를 언어별로 옮긴다 ──────────────────────────
    # 스크립트는 기준 언어로 한 번만 만든다(사실은 언어와 무관하므로). 예전에는 그래서
    # **기준 언어 탭에만** 실렸고, 한국어 독자는 보고서에서 가장 긴 산문(9천~1만 4천 자)을
    # 아예 보지 못했다. 스크립트는 분석보다 훨씬 작아서 번역 한 번이 싸다 — 동시에 돌린다.
    if script_doc and len(sections) > 1 and not over_budget():
        others = [s for s in sections if i18n.normalize(s["lang"]) != i18n.normalize(base)]
        if others:
            job.emit("translate", f"서술 {len(others)}개 언어로 옮기는 중…", 82)

            def make_sc(sec):
                def run():
                    return sec, translate.translate_script(
                        script_doc, sec["lang"], settings, base_lang=base)
                return run

            for res in _llm.gather([make_sc(s) for s in others], settings):
                if isinstance(res, Exception):
                    errors.append({"lang": "?", "message": f"서술 번역 실패: {type(res).__name__}"})
                    continue
                sec, (doc, c, st) = res
                total_cost += c
                if doc and st.get("translated"):
                    sec["script"] = doc
                else:
                    errors.append({"lang": sec["lang"],
                                   "message": f"서술 번역이 적용되지 않았습니다"
                                              f"({st.get('translated', 0)}/{st.get('requested', 0)})."})
    for s in sections:
        if i18n.normalize(s["lang"]) == i18n.normalize(base) and script_doc:
            s["script"] = script_doc

    # ── 5) 리포트 파일 ──────────────────────────────────────────
    job.raise_if_canceled()
    job.emit("render", "보고서 작성 중…", 85)
    reports: list[dict] = []
    if len(sections) == 1:
        s = sections[0]
        rep = report_mod.build_report(s["result"], files, settings, s["lang"],
                                      research=s["research"], start_lat=start_lat,
                                      start_lng=start_lng, knowledge=known, script=script_doc)
        rep["langName"] = i18n.native_name(s["lang"])
        rep["langs"] = [s["lang"]]
        rep["hasResearch"] = bool(s["research"])
        rep["combined"] = False
        reports.append(rep)
    else:
        rep = report_mod.build_combined_report(sections, files, settings,
                                               start_lat=start_lat, start_lng=start_lng,
                                               knowledge=known, script=script_doc)
        if rep.get("status") == "OK":
            rep["langName"] = " · ".join(i18n.native_name(s["lang"]) for s in sections)
            rep["langs"] = [s["lang"] for s in sections]
            rep["hasResearch"] = any(s["research"] for s in sections)
            rep["combined"] = True
            reports.append(rep)
        else:
            errors.append({"lang": "*", "message": rep.get("message") or "통합 리포트 생성 실패"})

    # 스크립트를 보고서 옆에 남긴다 — HTML 을 모델 없이 다시 만들 수 있게.
    script_file = None
    if reports and script_doc:
        from . import script as script_mod

        script_file = script_mod.save(settings, reports[0]["file"], script_doc)

    # ── 6) 지식 적재 ────────────────────────────────────────────
    kb = {}
    if reports and settings.knowledge_enabled and over_budget():
        # 보고서는 이미 나왔다. 예산을 넘었으면 적재는 다음 기회로 미룬다 —
        # 사용자를 더 기다리게 하는 것보다 낫다. 미룬 것은 결과에 표식으로 남겨
        # `geoguesshelper ingest --scan` 이 찾아 사후 적재할 수 있게 한다.
        kb = {"skipped": f"over-budget({elapsed():.0f}s)"}
        job.emit("knowledge",
                 f"예산 초과({elapsed():.0f}초) — 지식 적재 생략 "
                 f"(사후 적재: geoguesshelper ingest --scan --apply)", 98)
    elif reports and settings.knowledge_enabled:
        job.emit("knowledge", "지식 저장소에 적재 중…", 95)
        # P2(장소 사실)만 예산 안에서 동기 호출한다 — P1(지점 단서)·P3(프로파일)는 재료가
        # 이미 result.research/image_panos 에 남았으므로 `geoguesshelper ingest --apply`가
        # 예산 밖에서 소급한다(docs/plan/atom-density-map-detail_260829.html §stages 3단계).
        kb = knowledge.ingest(
            settings, analysis=base_result, research=base_research,
            lat=start_lat, lng=start_lng, report_file=reports[0]["file"], known=known,
            passes=("p2",),
        )
        total_cost += kb.get("cost_usd") or 0.0
        knowledge.write_report_note(
            settings, report_file=reports[0]["file"], place=place_label,
            lat=start_lat, lng=start_lng,
            atoms=kb.get("atoms") or [], langs=[s["lang"] for s in sections],
            summary=(base_profile or {}).get("summary", "") if base_profile else "",
            passes=["p2"],
        )
        job.emit(
            "knowledge",
            f"원자 {kb.get('created', 0)}개 신규 · {kb.get('merged', 0)}개 병합",
            98,
        )

    return {
        "status": "OK" if reports else "FAIL",
        "reports": reports,
        "errors": errors,
        "primaryAnalysis": base_result,
        # 잡 로그에 프로파일(경제·문화/역사·관광·인물 …)을 남긴다 — 지금까지는 실시간 적재
        # (knowledge.ingest 바로 아래)에만 쓰이고 버려져, 예산 초과로 미룬 보고서의 사후 적재는
        # 분석 근거만으로 해야 했다(P3 프로파일 원자를 소급할 수 없었다). 이제 `ingest --apply`
        # 가 이 필드로 P3 를 소급할 수 있다(docs/plan/atom-density-map-detail_260829.html §stages 2단계).
        "research": base_research,
        "baseLang": base,
        "script": ({"file": script_file, "sections": len((script_doc or {}).get("sections") or []),
                    **((script_doc or {}).get("_meta") or {})} if script_doc
                   else ({"status": "SKIP", "reason": script_skip} if script_skip else None)),
        "elapsed_s": round(elapsed(), 1),
        "budget_s": settings.job_budget_s,
        "cost_usd": round(total_cost, 4),
        "models": {
            "vision": settings.model_vision, "fact": settings.model_fact,
            "reason": settings.model_reason, "draft": settings.model_draft,
        },
        "knowledge": {
            "recalled": [a.id for a in known],
            # 분석 프롬프트에 실린 원자와 모델이 실제로 대조해 썼다고 적은 원자 — 관측소 "쓰이고 있다" 의 근거.
            "pre_recalled": [a.id for a in pre_known],
            "relied_on": [i for i in (analysis.get("relied_on_atoms") or []) if isinstance(i, str)],
            "created": kb.get("created", 0),
            "merged": kb.get("merged", 0),
            "skipped": kb.get("skipped"),
            "reused": (base_research or {}).get("reused_atom_ids") or [],
            "searches": (base_research or {}).get("searches", 0),
        },
        "message": None if reports else (errors[0]["message"] if errors else "리포트 생성 실패"),
    }


# ── 작업 핸들러 ──────────────────────────────────────────────────
def _make_handlers(settings: Settings):
    def _checked(result: dict) -> dict:
        """파이프라인이 실패를 '반환'하면 작업도 FAILED 로 표시하되 본문은 보존한다.

        예전처럼 DONE 으로 두면 UI 가 성공과 구분할 수 없다.
        """
        if (result or {}).get("status") != "OK":
            raise jobs.JobFailure((result or {}).get("message") or "작업 실패", result)
        return result

    async def _queue_correction(job: jobs.Job, result: dict) -> None:
        """보고서 잡 성공 직후 — 정정 루프를 **예산 밖 후속 잡**으로 큐에 넣는다(impl-spec §3.5).

        보고서 잡 자체는 그대로 끝난다. 재료(primaryAnalysis.image_panos·images)가 없으면 아무것도
        하지 않고, 등록 실패는 삼키고 로그만 남긴다 — 정정은 부가 학습이지 보고서의 일부가 아니다.
        client_key 로 같은 보고서에 정정이 두 번 걸리지 않게 한다.
        """
        if not settings.correction_enabled or _QUEUE is None:
            job.emit("correct", "정정 생략 — correction_enabled 꺼짐" if not settings.correction_enabled
                     else "정정 생략 — 큐 없음", job.pct)
            return
        pa = (result or {}).get("primaryAnalysis") or {}
        if not isinstance(pa, dict):
            job.emit("correct", "정정 생략 — 분석 결과 없음", job.pct)
            return
        # 클라이언트가 분석 결과를 재사용시키면(payload.analysis) base_result 에 images 가 없다 —
        # 캡처 목록은 payload.files 에, pano 좌표는 payload.panos 에 있으므로 거기서 보충한다.
        # (e2e 실측 260907: images 없음 → 정정이 조용히 건너뛰어졌다.)
        if not pa.get("images") and job.payload.get("files"):
            pa["images"] = [Path(f).name for f in job.payload["files"]]
        if not pa.get("image_panos") and isinstance(job.payload.get("panos"), dict):
            pa["image_panos"] = {Path(k).name: v for k, v in job.payload["panos"].items()
                                 if isinstance(v, dict) and Path(k).name}
        if not (pa.get("image_panos") and pa.get("images")):
            job.emit("correct", "정정 생략 — 캡처별 pano 좌표(image_panos) 없음: 정답을 모르면 정정할 수 없다", job.pct)
            return
        try:
            cj = await _QUEUE.submit(
                "correct",
                {"source_job_id": job.id, "result": result, "label": job.label, "tabId": job.payload.get("tabId")},
                label=f"정정 · {job.label}", client_key=f"correct:{job.id}",
            )
            job.emit("correct", f"정정 잡 등록 {cj.id} (예산 밖 후속)", job.pct)
        except Exception as exc:  # noqa: BLE001 — 정정 등록 실패가 보고서 잡을 실패로 만들면 안 된다
            job.emit("correct", f"⚠ 정정 잡 등록 실패(무시): {type(exc).__name__}: {exc}", job.pct)

    async def report_job(job: jobs.Job) -> dict:
        p = job.payload
        files = p.get("files") or []
        if not files:
            raise ValueError("리포트 생성에는 캡처(files)가 필요합니다.")
        start = p.get("start") or {}
        elev = await _fetch_elevation(settings, start.get("lat"), start.get("lng"))
        result = _checked(await _in_pool(
            _build_reports_sync, job, files, _norm_langs(p.get("langs")), settings,
            p.get("analysis"), bool(p.get("research", True)),
            start.get("lat"), start.get("lng"), p.get("panos") or {}, elev,
        ))
        await _queue_correction(job, result)
        return result

    async def correct_job(job: jobs.Job) -> dict:
        """자동 정정 루프(correction.py) — payload {source_job_id, result(보고서 잡 반환 dict), label}."""
        import functools

        from . import correction

        p = job.payload
        fn = functools.partial(
            correction.run_for_result, settings,
            source_job_id=p.get("source_job_id") or job.id, result=p.get("result") or {},
            label=p.get("label") or "", log=lambda m: job.emit("correct", str(m)),
        )
        return _checked(await _in_pool(fn))

    async def scene_report_job(job: jobs.Job) -> dict:
        p = job.payload
        pose = p.get("pose") or {}
        start = p.get("start") or {}
        start_lat = start.get("lat") if start.get("lat") is not None else pose.get("lat")
        start_lng = start.get("lng") if start.get("lng") is not None else pose.get("lng")

        job.emit("capture", "현재 장면 캡처 중…", 4)
        assert _CAPTURE_SEM is not None
        async with _CAPTURE_SEM:            # Chromium 동시 실행 상한
            job.raise_if_canceled()
            try:
                cap = await capture_mod.capture(pose, settings, mode=p.get("mode", "auto"))
            except Exception as exc:  # noqa: BLE001 — 캡처 예외가 500 text/plain 이 되지 않게
                cap = {"status": "CAPTURE_ERROR", "message": f"{type(exc).__name__}: {exc}"}
        if cap.get("status") != "OK":
            raise jobs.JobFailure(
                cap.get("message") or f"캡처 실패({cap.get('status')})",
                {"status": "CAPTURE_FAIL", "capture": cap, "reports": []},
            )

        job.emit("captured", "캡처 완료", 8)
        cap_panos = {cap["file"]: {
            "pano_id": cap.get("pano_id"), "lat": cap.get("lat"), "lng": cap.get("lng"),
            "heading": cap.get("heading"), "pitch": cap.get("pitch"),
        }}
        elev = await _fetch_elevation(settings, start_lat, start_lng)
        result = await _in_pool(
            _build_reports_sync, job, [cap["file"]], _norm_langs(p.get("langs")), settings,
            None, bool(p.get("research", True)), start_lat, start_lng, cap_panos, elev,
        )
        result["capture"] = cap
        result = _checked(result)
        await _queue_correction(job, result)     # 장면 보고서도 image_panos(cap_panos)가 있다
        return result

    async def atlas_report_job(job: jobs.Job) -> dict:
        """어드바이저 보고서 — 아틀라스 슬라이스(테마 × 범위)를 원자만으로 서술한다.

        지식 적재 단계가 없다(비순환 원칙 — 기존 원자의 재서술을 다시 원자로 만들지 않는다).
        """
        import functools

        from . import atlas_report

        p = job.payload
        fn = functools.partial(
            atlas_report.run, settings,
            theme=p.get("theme") or "all", range_spec=p.get("range") or "world",
            langs=_norm_langs(p.get("langs") or settings.report_lang),
            on_progress=lambda stage, msg, pct: job.emit(stage, msg, pct),
            should_stop=lambda: job.cancel_requested,
        )
        return _checked(await _in_pool(fn))

    return {"report": report_job, "scene-report": scene_report_job, "atlas-report": atlas_report_job,
            "correct": correct_job}


# ── 앱 ───────────────────────────────────────────────────────────
def build_app(settings: Settings) -> FastAPI:
    global _POOL, _CAPTURE_SEM, _QUEUE

    _POOL = ThreadPoolExecutor(
        max_workers=max(2, settings.job_concurrency + settings.capture_concurrency + 2),
        thread_name_prefix="geo",
    )
    _QUEUE = jobs.JobQueue(
        concurrency=settings.job_concurrency,
        max_pending=settings.job_max_pending,
        history=settings.job_history,
        log_path=settings.jobs_dir / "jobs.jsonl",
    )
    def _record_work(settings, owner, kind, job, out, before, plan="free") -> None:
        """잡 하나가 끝났다 — 남길 것은 셋이다.

          ① 이번에 생긴 원자를 **공용** 저장소에 올린다.
          ② 이 회원의 참조를 남긴다(만든 것/가져다 쓴 것을 구분해서).
          ③ 보고서였다면 1건으로 기록한다 — 과금 단위이자 원가 계측의 원천.

        새 원자 수·재사용 수·실비를 같이 남기는 이유: 값을 짐작으로 정하지 않기 위해서다.
        지식이 쌓일수록 재사용 비율이 올라가고 건당 원가가 내려가는데, 그 곡선을 보려면
        건마다 실제로 몇 개가 새로 만들어졌는지가 있어야 한다.
        """
        from . import db as _db

        res = out if isinstance(out, dict) else {}
        files = [str(f) for f in (res.get("reports") or [])]
        stat = tenancy.sync_atoms(settings, owner, plan=plan,
                                  report_file=files[0] if files else "", before=before)
        if kind not in ("report", "scene-report") or res.get("status") != "OK":
            return
        pl = job.payload or {}
        with _db.session_for(settings) as s:
            _db.record_report(
                s, user_id=owner,
                anon_key="" if owner else str(pl.get("_anon") or ""),
                anon_ip="" if owner else str(pl.get("_anonip") or ""),
                report_file=files[0] if files else "",
                label=str(pl.get("label") or ""),
                cost_usd=float(res.get("cost_usd") or 0.0),
                atoms_new=stat.get("created", 0), atoms_reused=stat.get("reused", 0))

    def _tenant_handler(kind: str, base_fn):
        """잡을 **그 회원의 것으로** 돌린다 — 저장소도 키도 그 사람 것이다.

        핸들러 본문은 한 줄도 고치지 않는다. `_make_handlers` 를 그 회원용 Settings 로
        다시 만들어 주면(클로저 생성은 싸다) 안에서 쓰는 settings 가 통째로 갈린다.
        """
        async def run(job):
            owner = str((job.payload or {}).get("_owner") or "")
            if not owner:
                # 가입 전 방문자의 무료 1건 — 운영자 키로, 운영자 저장소에서 돈다.
                # 그래도 만들어진 원자는 공용 지식에 남기고 1건을 기록한다. 이 사람이
                # 나중에 가입하든 안 하든, 그 돈으로 얻은 지식은 남는다.
                before = tenancy.atom_ids_now(settings)
                out = None
                try:
                    out = await _byo_job(base_fn)(job)
                    return out
                finally:
                    try:
                        _record_work(settings, "", kind, job, out, before)
                    except Exception:  # noqa: BLE001 — 기록 실패가 결과를 버리지 않는다
                        pass
            byo = _JOB_CREDS.pop(str((job.payload or {}).get("_credref") or ""), None)
            if byo is not None:      # 방문자가 직접 넣은 키가 회원 키보다 우선한다
                t = llm.set_credentials(byo)
                try:
                    return await base_fn(job)
                finally:
                    llm.reset_credentials(t)
            from . import db as _db
            from . import webapi as _webapi

            us = tenancy.settings_for_user(settings, owner)
            tenancy.hydrate(settings, owner)
            with _db.session_for(settings) as s:
                user = s.get(_db.User, owner)
            plan = getattr(user, "plan", "free")
            creds = _webapi.credentials_for(settings, user) if user is not None else None
            token = llm.set_credentials(creds) if creds is not None else None
            # 이 잡 **전에** 있던 원자. 끝나고 비교해야 새로 만든 것과 가져다 쓴 것이
            # 갈린다 — 새 원자만 돈이 들었고, 그 구분이 곧 원가다.
            before = tenancy.atom_ids_now(settings)
            out = None
            try:
                out = await _make_handlers(us)[kind](job)
                return out
            finally:
                if token is not None:
                    llm.reset_credentials(token)
                # 이 잡이 만진 원자를 공용 저장소로 올리고(write-through) 이 회원의
                # 참조를 남긴다. 저장소가 갈라지는 게 아니라 보는 창이 늘어난다.
                try:
                    _record_work(settings, owner, kind, job, out, before, plan)
                except Exception:  # noqa: BLE001 — 적재 실패가 보고서를 실패로 만들지 않는다
                    pass
        return run

    def _byo_job(base_fn):
        """단독 소유자 모드에서도 헤더로 온 키가 잡까지 가게 한다."""
        async def run(job):
            byo = _JOB_CREDS.pop(str((job.payload or {}).get("_credref") or ""), None)
            if byo is None:
                return await base_fn(job)
            t = llm.set_credentials(byo)
            try:
                return await base_fn(job)
            finally:
                llm.reset_credentials(t)
        return run

    for kind, fn in _make_handlers(settings).items():
        _QUEUE.register(kind, _tenant_handler(kind, fn) if settings.multi_user else _byo_job(fn))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _CAPTURE_SEM
        _CAPTURE_SEM = asyncio.Semaphore(settings.capture_concurrency)
        await _QUEUE.start()
        try:
            yield
        finally:
            await _QUEUE.shutdown()
            if _POOL is not None:
                _POOL.shutdown(wait=False, cancel_futures=True)
            from . import llm

            llm.close_client()

    app = FastAPI(title="GeoGuessHelper", version=__version__, lifespan=lifespan)

    # ── 요청에 실려 온 키 ─────────────────────────────────────────
    # 회원가입도 DB 도 없이, 방문자가 자기 키를 붙여넣고 바로 쓰게 한다.
    # 서버는 이 키를 **저장하지 않는다** — 그 요청 동안만 컨텍스트에 얹고 버린다.
    # (로그인한 회원의 저장된 키는 아래 _tenant 미들웨어가 따로 얹는다.)
    @app.middleware("http")
    async def _byo_key(request: Request, call_next):
        raw = (request.headers.get(_KEY_HEADER, "") or "").strip()
        token = None
        if raw and 20 <= len(raw) <= 300:
            provider = _secret_provider(raw)
            if provider:
                token = llm.set_credentials(llm.Creds(provider, raw, "byo"))
        try:
            return await call_next(request)
        finally:
            if token is not None:
                llm.reset_credentials(token)

    # ── 다중 사용자 ──────────────────────────────────────────────
    # DATABASE_URL 이 없으면 이 블록은 통째로 지나간다 — 로컬 단독 소유자 모드는
    # 이 코드를 한 줄도 실행하지 않는다.
    if settings.multi_user:
        from . import auth, webapi

        app.include_router(webapi.build_router(settings))

        @app.middleware("http")
        async def _tenant(request: Request, call_next):
            """이 요청을 **누구의 것으로** 처리할지 한 곳에서 정한다.

            여기서 하지 않으면 라우트마다 사용자를 꺼내 쓰게 되고, 한 곳만 빠뜨리면
            남의 키로 남의 돈을 쓰거나 남의 원자를 읽는다 — 이 앱에서 가장 나쁜 실패다.
            """
            user = webapi.current_user(settings, request)
            request.state.user = user
            token = None
            uid_token = _CURRENT_USER_ID.set(user.id if user is not None else "")
            # 방문자 지문 — 무료 1건을 누구에게 줬는지 기억하기 위한 최소한.
            vid = quota.visitor_of(request)
            fresh_vid = "" if vid else quota.new_visitor()
            anon_token = _CURRENT_ANON.set((vid or fresh_vid, quota.ip_key(settings, request)))
            if user is not None:
                creds = webapi.credentials_for(settings, user)
                if creds is not None:
                    token = llm.set_credentials(creds)
                try:
                    tenancy.hydrate(settings, user.id)     # DB → 파일 복원(회원마다 1회)
                except Exception:  # noqa: BLE001 — 복원 실패가 요청을 막지 않는다
                    pass
            try:
                resp = await call_next(request)
                if fresh_vid:
                    # 처음 온 브라우저에만 새로 심는다. 이미 있는 값을 덮어쓰면 시크릿
                    # 창을 안 열고도 무료분이 계속 되살아난다.
                    resp.set_cookie(quota.VISITOR_COOKIE, fresh_vid,
                                    **{**auth.cookie_kwargs(settings, request),
                                       "max_age": quota._COOKIE_MAX_AGE})
                return resp
            finally:
                if token is not None:
                    llm.reset_credentials(token)
                _CURRENT_USER_ID.reset(uid_token)
                _CURRENT_ANON.reset(anon_token)

    @app.get("/")
    async def index():
        # 정적 파일 캐시 무효화 파라미터를 앱 버전으로 치환해서 내려보낸다.
        # 손으로 관리하면 반드시 잊어버리고, 그 결과는 "새 서버 + 캐시된 옛 프론트"다.
        html = (WEBUI_DIR / "index.html").read_text(encoding="utf-8")
        # 이 문서만은 **절대 캐시하지 않는다.** 정적 파일은 ?v= 로 무효화되지만,
        # 그 ?v= 를 적어 주는 것이 바로 이 문서다. 이게 캐시되면 낡은 index 가 낡은 js 를
        # 계속 불러 서버를 새로 켜도 화면이 안 바뀐다(진단이 매우 어려운 계열).
        return HTMLResponse(html.replace("__ASSET_V__", __version__),
                            headers={"Cache-Control": "no-store, must-revalidate"})

    @app.get("/api/config")
    async def api_config():
        cfg = settings.public_config()
        cfg["knowledge"] = knowledge.store_for(settings).stats() if settings.knowledge_enabled else {}
        # Static 경로가 지금 쓸 수 있는지 화면이 알아야 한다. 예전에는 서버가 매 캡처마다
        # 거부당하고 조용히 브라우저로 넘어갔고, 그 사실이 화면 어디에도 없었다.
        cfg["staticHealth"] = capture_mod.static_health(settings)
        cfg["captureTimeoutS"] = settings.capture_timeout_s
        return JSONResponse(cfg)

    @app.post("/api/extract")
    async def api_extract(payload: dict):
        url = (payload or {}).get("url", "")
        try:
            pose = await linkresolver.extract(url)
        except linkresolver.ParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"링크 해제 실패: {exc}") from exc
        return pose

    @app.post("/api/capture")
    async def api_capture(payload: dict):
        pose = (payload or {}).get("pose")
        mode = (payload or {}).get("mode", "auto")
        if not pose or pose.get("lat") is None and not pose.get("pano"):
            raise HTTPException(status_code=422, detail="유효한 pose(lat/lng 또는 pano)가 필요합니다.")
        assert _CAPTURE_SEM is not None
        # 대기부터 마감 안에 든다. 세마포어가 1이라 앞 건이 막히면 여기서 먼저 걸리는데,
        # 그 사실을 사용자에게 말해 줘야 "눌렀는데 아무 일도 없다"가 안 된다.
        limit = float(getattr(settings, "capture_timeout_s", 75.0) or 75.0)
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(_CAPTURE_SEM.acquire(), timeout=limit)
        except asyncio.TimeoutError:
            return JSONResponse({
                "status": "CAPTURE_TIMEOUT",
                "message": f"앞선 캡처가 {limit:.0f}초 안에 끝나지 않아 대기를 포기했습니다."
                           " 잠시 후 다시 눌러 주세요.",
                "waited_s": round(time.monotonic() - t0, 1), "phase": "queue",
            })
        try:
            # 절대 마감이다. 예전 판의 max(5.0, …) 은 마감 직전에 큐를 잡으면 5초를
            # 더 줘서 "전체 벽시계 상한"이라는 설명과 어긋났다(75초 상한에 79.9초).
            rest = limit - (time.monotonic() - t0)
            if rest <= 0:
                return JSONResponse({
                    "status": "CAPTURE_TIMEOUT",
                    "message": f"대기만으로 {limit:.0f}초를 써 캡처를 시작하지 못했습니다.",
                    "waited_s": round(time.monotonic() - t0, 1), "phase": "queue",
                })
            try:
                result = await asyncio.wait_for(
                    capture_mod.capture(pose, settings, mode=mode), timeout=rest)
            except asyncio.TimeoutError:
                # 예전에는 상한이 없어 막힌 렌더 하나가 뒤의 모든 캡처를 표시 없이 세웠다
                # (실측 236.78초). 워커 쪽 상한(render_worker_timeout_s)이 브라우저를
                # 버려 큐를 풀고, 이쪽은 사용자에게 사유를 돌려준다.
                result = {
                    "status": "CAPTURE_TIMEOUT",
                    "message": f"캡처가 {limit:.0f}초 안에 끝나지 않아 중단했습니다."
                               " 브라우저 렌더가 막힌 것이며, 다음 캡처를 위해 렌더러를 다시 띄웁니다.",
                    "waited_s": round(time.monotonic() - t0, 1), "phase": "render",
                }
            except Exception as exc:  # noqa: BLE001
                # 예전에는 여기서 예외가 그대로 올라가 클라이언트가 JSON 이 아닌
                # text/plain "Internal Server Error" 를 받아 .json() 파싱에서 터졌다.
                result = {"status": "CAPTURE_ERROR", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            _CAPTURE_SEM.release()
        result.setdefault("elapsed_s", round(time.monotonic() - t0, 1))
        return JSONResponse(result)

    _photo_tokens: dict[str, str | None] = {}
    _photo_lock = asyncio.Lock()

    @app.get("/api/pano-photo")
    async def api_pano_photo(pano: str):
        """제3자 파노의 lh3 이미지 토큰을 돌려준다 — 뷰어의 검은 화면 우회용.

        프런트가 직접 알아낼 수 없는 값이다: Maps JS API 는 파노 타일을 워커에서 받아서
        페이지의 PerformanceObserver 에 한 건도 잡히지 않는다(실측 perfCount=0).
        헤드리스 브라우저는 CDP 로 워커 요청까지 보므로 여기서만 얻을 수 있다.
        파노당 한 번만 알아내고 캐시한다(브라우저 기동이 비싸다).
        """
        pano = (pano or "").strip()
        if not pano:
            raise HTTPException(status_code=422, detail="pano 가 필요합니다.")
        key = settings.js_api_key or settings.effective_static_key
        if not key:
            return JSONResponse({"base": None, "reason": "no_key"})
        from . import render_google

        async with _photo_lock:
            if pano not in _photo_tokens:
                try:
                    _photo_tokens[pano] = await asyncio.to_thread(
                        render_google.photo_token_for, pano, settings, key
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[pano-photo] 토큰 조회 실패 {pano[:16]}: {type(exc).__name__} {exc}", flush=True)
                    _photo_tokens[pano] = None
        base = _photo_tokens.get(pano)
        return JSONResponse({"base": base, "reason": None if base else "not_found"})

    # 사진구체/제3자 파노 이미지를 **서버가 대신 받아** 넘긴다.
    #
    # 왜 프록시가 필요한가 — URL 자체는 멀쩡하다(실측: 서버 217,584B, 헤드리스 브라우저 HTTP 200).
    # 그런데 사용자의 실제 브라우저에서는 검게 남았다. 원인은 브라우저 쪽 환경이다:
    # 광고/추적 차단 확장이나 백신 웹실드가 googleusercontent.com 을 막으면 우리 코드가
    # 손쓸 방법이 없다(이 PC 는 이미 Avast 가 TLS 에 개입한 전력이 있다).
    # 같은 출처로 바꾸면 그 층이 통째로 사라지고, 덤으로 캔버스도 오염되지 않는다.
    _IMG_HOST_RE = re.compile(r"^https://lh3\.googleusercontent\.com/gpms-cs-s/[A-Za-z0-9_\-]+$")
    _img_cache: dict[str, bytes] = {}
    _img_order: list[str] = []
    _img_bytes = 0
    # 등장방형 원본은 4096x2048 이 3.6MB 다. 개수로 제한하면 64장 × 3.6MB = 230MB 가 되므로
    # **바이트 예산**으로 제한한다. 개수 상한은 그 위에 얹는 보조 장치일 뿐이다.
    _IMG_BUDGET = 96 * 1024 * 1024

    def _cache_put(url: str, data: bytes) -> None:
        nonlocal _img_bytes
        if url in _img_cache:
            return
        _img_cache[url] = data
        _img_order.append(url)
        _img_bytes += len(data)
        while _img_order and (_img_bytes > _IMG_BUDGET or len(_img_order) > 64):
            old = _img_order.pop(0)
            _img_bytes -= len(_img_cache.pop(old, b""))

    def _num(v, default: float, lo: float, hi: float) -> float:
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return default

    @app.get("/api/pano-image")
    async def api_pano_image(base: str, w: int = 1056, h: int = 450,
                             pi: float = 0.0, ya: float = 0.0, fo: float = 90.0,
                             mode: str = "rect"):
        """gpms 토큰 → JPEG. base 는 **토큰 URL 만** 허용한다(SSRF 방지).

        mode=rect  직선투영 crop — 시점(pi/ya/fo)을 서버가 잘라 준다. 정지 이미지용.
        mode=equi  **등장방형 원본** — 구형 텍스처. 이걸 받아 브라우저에서 3D 로 띄우면
                   구글 지도처럼 마우스로 자유롭게 돌릴 수 있다(실측: 2048x1024=992KB,
                   4096x2048=3.64MB, 최대 8704x4352). 접미사에 시점 파라미터를 붙이지
                   않는 것이 핵심 — 붙이는 순간 잘린 평면 이미지가 된다.
        """
        base = (base or "").strip()
        if not _IMG_HOST_RE.match(base):
            raise HTTPException(status_code=400, detail="허용되지 않은 이미지 주소입니다.")

        if mode == "equi":
            # 2:1 을 강제한다. 폭만 클램프하고 높이는 파생시켜 비율이 깨지지 않게 한다.
            ew = int(_num(w, 2048, 256, 8704))
            url = f"{base}=w{ew}-h{ew // 2}"
            data = _img_cache.get(url)
            if data is None:
                try:
                    data = await asyncio.to_thread(
                        lambda: streetview.fetch_bytes_sync(url, timeout=45.0)
                    )
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(status_code=502,
                                        detail=f"파노라마를 받지 못했습니다: {type(exc).__name__}") from exc
                if not data or len(data) < 1000:
                    raise HTTPException(status_code=502, detail="파노라마가 비어 있습니다.")
                _cache_put(url, data)
            from fastapi.responses import Response

            return Response(content=data, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})

        w = int(_num(w, 1056, 64, 2048))
        h = int(_num(h, 450, 64, 2048))
        url = (f"{base}=w{w}-h{h}-k-no-pi{round(_num(pi, 0, -90, 90))}"
               f"-ya{round(_num(ya, 0, 0, 360))}-ro0-fo{round(_num(fo, 90, 20, 120))}")

        data = _img_cache.get(url)
        if data is None:
            try:
                data = await asyncio.to_thread(lambda: streetview.fetch_bytes_sync(url, timeout=25.0))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502,
                                    detail=f"이미지를 받지 못했습니다: {type(exc).__name__}") from exc
            if not data or len(data) < 1000:
                raise HTTPException(status_code=502, detail="이미지가 비어 있습니다.")
            _cache_put(url, data)

        from fastapi.responses import Response

        media = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return Response(content=data, media_type=media,
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.post("/api/analyze")
    async def api_analyze(payload: dict):
        payload = payload or {}
        files = payload.get("files") or []
        lang = i18n.normalize(payload.get("lang") or settings.report_lang)
        paths = [settings.captures_dir / Path(f).name for f in files]
        result = await _in_pool(analyze_captures, paths, settings, lang)
        return JSONResponse(result)

    # ── 작업 큐 ─────────────────────────────────────────────────
    async def _submit(kind: str, payload: dict, label: str):
        assert _QUEUE is not None
        # 이 잡이 누구의 것인지 **id 만** 새긴다. 키를 payload 에 넣으면 jobs.jsonl 에
        # 평문으로 남는다 — 잡 시작 시 DB 에서 다시 푼다(_tenant_handler).
        owner = _CURRENT_USER_ID.get()
        if owner:
            payload = {**payload, "_owner": owner}
        else:
            # 비회원의 무료 1건 — 누구의 1건이었는지 잡이 끝난 뒤에도 알아야 한다.
            # 둘 다 값이 아니라 지문이므로(쿠키는 난수, IP 는 소금 친 해시) 잡 기록에
            # 남아도 되돌릴 것이 없다.
            vid, ipk = _CURRENT_ANON.get()
            if vid or ipk:
                payload = {**payload, "_anon": vid, "_anonip": ipk}
        # 헤더로 온 키는 DB 에 없다 — 잡이 쓸 수 있게 **메모리로만** 넘긴다.
        #
        # job.id 로 넣으면 경합이 난다: submit 을 await 하는 순간 워커가 그 잡을 집어
        # 실행할 수 있고, 그러면 우리가 사전에 넣기 **전에** 핸들러가 키를 찾는다.
        # 그래서 난수 핸들을 먼저 등록하고 payload 에는 **그 핸들만** 싣는다 —
        # 핸들은 의미 없는 난수라 jobs.jsonl 에 남아도 무해하다(키는 메모리에만 있다).
        byo = llm.current_credentials_or_none()
        if byo is not None and byo.label == "byo":
            import secrets as _secrets

            ref = _secrets.token_hex(8)
            if len(_JOB_CREDS) > 256:       # 취소된 잡의 핸들이 무한히 쌓이지 않게
                _JOB_CREDS.pop(next(iter(_JOB_CREDS)), None)
            _JOB_CREDS[ref] = byo
            payload = {**payload, "_credref": ref}
        return await _QUEUE.submit(kind, payload, label=label,
                                   client_key=str(payload.get("clientKey") or ""))

    def _gate(request: Request) -> None:
        """무료 한도 검사 — 보고서를 만드는 경로에서만. 402 는 '돈이 필요하다'는 뜻이고,
        화면은 reason 으로 가입 창을 띄울지 결제 창을 띄울지 고른다."""
        if not settings.multi_user:
            return
        d = quota.decide(settings, request, getattr(request.state, "user", None))
        if not d.allowed:
            raise HTTPException(status_code=402, detail=d.message,
                                headers={"X-Quota-Reason": d.reason})

    @app.get("/api/quota")
    async def api_quota(request: Request):
        """지금 몇 건 남았는가 — 화면이 버튼을 누르기 **전에** 알 수 있게."""
        if not settings.multi_user:
            return JSONResponse(quota.Decision(True, plan="owner").as_dict())
        return JSONResponse(
            quota.decide(settings, request, getattr(request.state, "user", None)).as_dict())

    @app.post("/api/jobs/report")
    async def api_job_report(payload: dict, request: Request):
        payload = payload or {}
        if not (payload.get("files") or []):
            raise HTTPException(status_code=422, detail="리포트 생성에는 캡처(files)가 필요합니다.")
        _gate(request)
        try:
            job = await _submit("report", payload, payload.get("label") or "보고서")
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return JSONResponse({"job": job.public(), "position": _QUEUE.position(job.id),
                             "queue": _QUEUE.stats()})

    @app.post("/api/jobs/scene-report")
    async def api_job_scene_report(payload: dict, request: Request):
        payload = payload or {}
        pose = payload.get("pose")
        if not pose or (pose.get("lat") is None and not pose.get("pano")):
            raise HTTPException(status_code=422, detail="유효한 pose(lat/lng 또는 pano)가 필요합니다.")
        _gate(request)
        try:
            job = await _submit("scene-report", payload, payload.get("label") or "바로 보고서")
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return JSONResponse({"job": job.public(), "position": _QUEUE.position(job.id),
                             "queue": _QUEUE.stats()})

    @app.post("/api/jobs/atlas-report")
    async def api_job_atlas_report(payload: dict):
        """어드바이저 보고서 잡 — {theme, range: world|sphere:<key>|actor:<slug>, langs}."""
        payload = payload or {}
        theme = str(payload.get("theme") or "all").lower()
        try:
            job = await _submit("atlas-report", payload,
                                payload.get("label") or f"아틀라스 {theme} × {payload.get('range') or 'world'}")
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return JSONResponse({"job": job.public(), "position": _QUEUE.position(job.id),
                             "queue": _QUEUE.stats()})

    # ── 구 동기 API (호환) ──────────────────────────────────────
    # 큐 도입 전의 클라이언트(캐시된 예전 app.js, 스크립트, CLI)가 이 경로로 POST 한다.
    # 없애버렸더니 그쪽에서는 그냥 404 "Not Found" 로만 보였다. 이제는 같은 큐를 지나되
    # 완료까지 기다렸다가 예전과 같은 모양의 JSON 을 돌려준다 — 동시성 보장은 그대로다.
    async def _run_sync(kind: str, payload: dict, label: str):
        try:
            job = await _submit(kind, payload, label)
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        assert _QUEUE is not None
        done = await _QUEUE.wait(job.id)
        if done is None:
            raise HTTPException(status_code=500, detail="작업이 사라졌습니다.")
        if done.status == jobs.DONE:
            return JSONResponse(done.result or {})
        # 실패/취소도 예전처럼 200 + status 필드로 돌려준다(옛 프론트가 그렇게 읽는다).
        body = dict(done.result or {})
        body.setdefault("status", "FAIL" if done.status == jobs.FAILED else "CANCELED")
        body.setdefault("reports", [])
        body["message"] = done.error or body.get("message") or "작업 실패"
        body["jobId"] = done.id
        return JSONResponse(body)

    @app.post("/api/report")
    async def api_report_compat(payload: dict):
        payload = payload or {}
        if not (payload.get("files") or []):
            raise HTTPException(status_code=422, detail="리포트 생성에는 캡처(files)가 필요합니다.")
        return await _run_sync("report", payload, "보고서")

    @app.post("/api/scene-report")
    async def api_scene_report_compat(payload: dict):
        payload = payload or {}
        pose = payload.get("pose")
        if not pose or (pose.get("lat") is None and not pose.get("pano")):
            raise HTTPException(status_code=422, detail="유효한 pose(lat/lng 또는 pano)가 필요합니다.")
        return await _run_sync("scene-report", payload, "바로 보고서")

    @app.get("/api/jobs")
    async def api_jobs():
        assert _QUEUE is not None
        return JSONResponse({"jobs": _QUEUE.list(), "queue": _QUEUE.stats()})

    @app.get("/api/jobs/{job_id}")
    async def api_job(job_id: str):
        assert _QUEUE is not None
        job = _QUEUE.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return JSONResponse({"job": job.public(with_result=True), "position": _QUEUE.position(job_id),
                             "queue": _QUEUE.stats()})

    @app.post("/api/jobs/{job_id}/cancel")
    async def api_job_cancel(job_id: str):
        assert _QUEUE is not None
        ok = await _QUEUE.cancel(job_id)
        if not ok:
            raise HTTPException(status_code=409, detail="이미 끝난 작업입니다.")
        return JSONResponse({"ok": True, "job": (_QUEUE.get(job_id) or jobs.Job("", "", {})).public()})

    @app.get("/api/jobs/{job_id}/stream")
    async def api_job_stream(job_id: str, request: Request):
        assert _QUEUE is not None
        q = _QUEUE.subscribe(job_id)
        if q is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")

        async def gen():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"      # 프록시/브라우저 연결 유지
                        continue
                    yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                    if ev.get("type") == "done":
                        break
            finally:
                _QUEUE.unsubscribe(job_id, q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── 지식 저장소 ─────────────────────────────────────────────
    @app.get("/api/knowledge")
    async def api_knowledge():
        return JSONResponse(knowledge.store_for(settings).stats())

    @app.get("/api/knowledge/atoms")
    async def api_knowledge_atoms(q: str = "", limit: int = 50):
        st = knowledge.store_for(settings)
        idx = st.index()
        needle = (q or "").strip().lower()
        out = []
        for aid, meta in idx.get("atoms", {}).items():
            hay = " ".join([
                str(meta.get("title", "")), " ".join(meta.get("tags") or []),
                " ".join(meta.get("entities") or []), str(meta.get("layer", "")),
            ]).lower()
            if needle and needle not in hay:
                continue
            out.append({"id": aid, **{k: meta.get(k) for k in
                                      ("title", "layer", "scope", "tags", "entities", "uses", "reports")}})
        out.sort(key=lambda x: -(x.get("uses") or 0))
        return JSONResponse({"atoms": out[:limit], "total": len(out)})

    @app.post("/api/knowledge/synthesize")
    async def api_synthesize(payload: dict):
        payload = payload or {}
        selector = (payload.get("selector") or "").strip()
        if not selector:
            raise HTTPException(status_code=422, detail="엔티티 또는 태그(selector)가 필요합니다.")
        langs = _norm_langs(payload.get("langs") or settings.report_lang)
        result = await _in_pool(_synthesize_sync, settings, selector, langs)
        return JSONResponse(result)

    # ── 원자 대화 (docs/plan/atom-dialogue_260906.html · impl-spec §4) ──────────
    # altaiya 의 관측소가 같은 계약(dialogue_api.py)을 낸다 — 본체는 로컬 개발·in-app 진입용.
    # 사람 승인 없음: 제안은 인용 관문(문맥 원자·이미지 ≥1)만 통과하면 kind=claim 원자가 된다.
    @app.post("/api/dialogue")
    async def api_dialogue(body: dict):
        from . import dialogue, llm

        body = body or {}
        try:
            return JSONResponse(await _in_pool(
                lambda: dialogue.chat(
                    settings, atom_ids=body.get("atoms") or [], molecule_id=body.get("molecule"),
                    images=body.get("images") or [], messages=body.get("messages") or [],
                    effort=body.get("effort"), lang=body.get("lang") or settings.report_lang,
                    session=body.get("session"),
                ),
            ))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except llm.LLMUnavailable as exc:
            return JSONResponse({"error": str(exc), "available": False}, status_code=503)
        except llm.LLMTimeout as exc:
            return JSONResponse({"error": str(exc)}, status_code=504)

    # ── 저장소 정리 ─────────────────────────────────────────────
    @app.get("/api/cleanup")
    async def api_cleanup_scan(older_than: float = 0.0, keep_last: int = 0):
        """지울 수 있는 것을 범주별로 보여준다. 아무것도 지우지 않는다."""
        report = await _in_pool(
            cleanup.scan_pos, settings, older_than, keep_last, _QUEUE
        )
        report.pop("_cats", None)
        return JSONResponse(report)

    @app.post("/api/cleanup")
    async def api_cleanup_run(payload: dict | None = None):
        """실제 정리. `apply` 가 참일 때만 지운다(기본은 모의 실행)."""
        p = payload or {}
        try:
            result = await _in_pool(
                cleanup.sweep_pos, settings,
                [str(c) for c in (p.get("categories") or [])],
                float(p.get("olderThan") or 0.0),
                int(p.get("keepLast") or 0),
                bool(p.get("apply")),
                _QUEUE,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result["memory"] = cleanup.prune_memory(_QUEUE)
        return JSONResponse(result)

    # ── 정적 파일 ───────────────────────────────────────────────
    @app.get("/captures/{name}")
    async def get_capture(name: str):
        path = settings.captures_dir / Path(name).name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="캡처를 찾을 수 없습니다.")
        return FileResponse(path)

    @app.get("/reports/{name}")
    async def get_report(name: str):
        # 보고서는 docs/report/country/{iso2}/ 에, 어드바이저 보고서는 docs/atlas/ 에 놓인다 —
        # 이름으로 찾는다(find_report 가 두 곳을 다 본다).
        # is_file() — 예전 exists() 는 디렉터리에도 True 라 FileResponse 가 500 을 냈다.
        path = find_report(settings, name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다.")
        return FileResponse(path)

    @app.get("/knowledge/{sub:path}")
    async def get_knowledge_file(sub: str):
        base = settings.knowledge_dir.resolve()
        target = (base / sub).resolve()
        if base not in target.parents and target != base:
            raise HTTPException(status_code=404, detail="찾을 수 없습니다.")
        if not target.is_file() or target.suffix.lower() not in (".md", ".json"):
            raise HTTPException(status_code=404, detail="찾을 수 없습니다.")
        return FileResponse(target, media_type="text/plain; charset=utf-8")

    if WEBUI_DIR.exists():
        app.mount("/webui", StaticFiles(directory=str(WEBUI_DIR)), name="webui")

    return app


def _synthesize_sync(settings: Settings, selector: str, langs: list[str]) -> dict:
    """종합 보고서 — 저장된 원자만으로 재조립한다(새 웹 검색 없음)."""
    res = knowledge.synthesize(settings, selector=selector, lang="en")
    if res.get("status") != "OK":
        return res
    syn = res["synthesis"]
    out_langs = langs or ["en"]
    files = []
    for lang in out_langs:
        body = syn
        cost = 0.0
        if lang != "en":
            tb = translate.translate_bundle(None, syn, None, lang, settings, base_lang="en")
            body = tb.get("profile") or syn
            cost = tb.get("cost_usd") or 0.0
            res["cost_usd"] = round((res.get("cost_usd") or 0.0) + cost, 4)
        files.append(report_mod.build_synthesis_report(body, res, settings, lang))
    res["reports"] = files
    return res


def _find_free_port(preferred: int, host: str = "127.0.0.1") -> int:
    for port in (preferred, preferred + 1, preferred + 2, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return s.getsockname()[1]
        except OSError:
            continue
    return preferred


def _safe_stdout() -> None:
    """Windows 한국어(cp949) 콘솔에서 유니코드 기호로 print 가 죽지 않도록 UTF-8 로 재구성."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    import uvicorn

    from . import tls as _tls
    from .tls import decide_tls, keylog_removed, neutralize_keylog
    from .winquirks import neutralize_wmi, wmi_neutralized

    def _static_banner(s) -> str:
        """Static 경로의 **실제** 상태. '키 설정됨'만으로는 쓸 수 있다는 뜻이 아니다."""
        h = capture_mod.static_health(s)
        if not h["key"]:
            return "없음 (모든 캡처가 브라우저 렌더)"
        if h["denied"] == "REQUEST_DENIED":
            return ("거부됨 - Cloud Console 에서 'Street View Static API'·'Maps Static API' 활성화 필요"
                    " (그전까지 브라우저 렌더로 폴백)")
        if h["denied"]:
            return f"막힘({h['denied']}) - 브라우저 렌더로 폴백"
        if not h["dedicated_key"]:
            return "설정됨 (JS 키 폴백 - 리퍼러 제한이면 서버측 GET 은 403)"
        return "설정됨"

    # 세 모드를 구분해 말한다. 예전 배너는 relaxed 를 표현할 방법이 없어서, 검증이
    # 살아 있는 상태와 꺼진 상태가 똑같이 "켬"/"끔" 둘로만 보였다.
    _TLS_BANNER = {
        "secure": "켬 (엄격)",
        "relaxed": "켬 (규격 완화 - 가로채기 CA 의 Basic Constraints 비-critical)",
        "insecure": "끔 (verify=False) - GEOHELPER_INSECURE_TLS 로 명시 해제됨",
    }

    _safe_stdout()
    # platform.uname() 캐시가 차기 전에. 이 PC 의 WMI 조회는 멈추고, SDK 는 요청 헤더를
    # 만들려고 그것을 부른다(winquirks.py 주석 참고).
    neutralize_wmi()
    # ssl 을 건드리기 전에 먼저. 이 값이 남아 있으면 create_default_context() 가
    # OPENSSL_Uplink 를 타고 ExitProcess(1) 로 프로세스를 끝내버린다(tls.py 주석 참고).
    neutralize_keylog()
    tls_mode = decide_tls()
    settings = load_settings()
    # PaaS 가 준 포트는 **협상 대상이 아니다** — 비었는지 확인하고 옮기는 것은 로컬에서
    # 8799 가 이미 물려 있을 때의 편의일 뿐이고, 컨테이너에서 포트를 옮기면 라우터가
    # 우리를 영영 못 찾는다.
    import os as _os

    if not _os.environ.get("PORT", "").strip():
        settings.port = _find_free_port(settings.port, settings.host)
    app = build_app(settings)

    url = f"http://localhost:{settings.port}/"
    kstats = knowledge.store_for(settings).stats() if settings.knowledge_enabled else {}
    banner = [
        "",
        "  == GeoGuessHelper ===============================",
        f"   브라우저 : {url}",
        f"   JS 지도 키   : {'설정됨' if settings.has_js_key else '없음 (지도/로드뷰 비활성, 추출은 동작)'}",
        f"   Static 캡처  : {_static_banner(settings)}",
        f"   Claude 분석  : {'설정됨' if settings.has_anthropic else '없음 (분석 비활성)'}",
        f"   TLS 검증     : {_TLS_BANNER.get(tls_mode, '켬')}"
        + (" · 시작 프로브는 실패(호스트별로 판정한다)" if _tls.probe_unverified() else ""),
        *([f"   SSLKEYLOGFILE: 제거함 ({keylog_removed()}) - 백신 TLS 감청 지시"] if keylog_removed() else []),
        *(["   WMI 조회     : 끔 - 이 PC 에서 멈춤 (platform.uname 폴백 사용)"] if wmi_neutralized() else []),
        f"   작업 큐      : 동시 {settings.job_concurrency}건 (대기 최대 {settings.job_max_pending})",
        f"   캡처 형식    : {settings.capture_format.upper()} q{settings.capture_quality}",
        f"   지식 저장소  : {'원자 ' + str(kstats.get('atoms', 0)) + '개' if settings.knowledge_enabled else '꺼짐'}",
        "  =================================================",
        "",
    ]
    try:
        print("\n".join(banner), flush=True)
    except UnicodeEncodeError:
        print("GeoGuessHelper ->", url, flush=True)

    import os

    # 서버에서는 열 브라우저가 없다. $PORT 가 있으면 PaaS 로 보고 건너뛴다.
    if not os.environ.get("GEOHELPER_NO_BROWSER") and not os.environ.get("PORT", "").strip():
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
