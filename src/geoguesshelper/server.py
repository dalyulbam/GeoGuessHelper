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
import json
import re
import socket
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import capture as capture_mod
from . import cleanup, i18n, jobs, knowledge, linkresolver, streetview, translate
from . import report as report_mod
from . import research as research_mod
from .analyze import analyze_captures
from . import __version__
from .config import Settings, load_settings

WEBUI_DIR = Path(__file__).parent / "webui"

# 블로킹 작업 전용 풀. 기본 ThreadPoolExecutor(20슬롯)를 Chromium 과 16분짜리 Claude
# 호출이 나눠 쓰다가 고갈되면 그 뒤 작업이 조용히 영원히 대기하던 문제를 피한다.
_POOL: ThreadPoolExecutor | None = None
_CAPTURE_SEM: asyncio.Semaphore | None = None
_QUEUE: jobs.JobQueue | None = None


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
        base_result = analyze_captures(paths, settings, base)
        if base_result.get("status") == "OK":
            total_cost += base_result.get("cost_usd") or 0.0

    if not base_result or base_result.get("status") != "OK" or not base_result.get("analysis"):
        msg = (base_result or {}).get("message") or (base_result or {}).get("status") or "분석 실패"
        return {"status": "FAIL", "reports": [], "errors": [{"lang": base, "message": msg}],
                "message": msg, "cost_usd": round(total_cost, 4)}

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
        kb = knowledge.ingest(
            settings, analysis=base_result, research=base_research,
            lat=start_lat, lng=start_lng, report_file=reports[0]["file"], known=known,
        )
        total_cost += kb.get("cost_usd") or 0.0
        knowledge.write_report_note(
            settings, report_file=reports[0]["file"], place=place_label,
            lat=start_lat, lng=start_lng,
            atoms=kb.get("atoms") or [], langs=[s["lang"] for s in sections],
            summary=(base_profile or {}).get("summary", "") if base_profile else "",
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

    async def report_job(job: jobs.Job) -> dict:
        p = job.payload
        files = p.get("files") or []
        if not files:
            raise ValueError("리포트 생성에는 캡처(files)가 필요합니다.")
        start = p.get("start") or {}
        return _checked(await _in_pool(
            _build_reports_sync, job, files, _norm_langs(p.get("langs")), settings,
            p.get("analysis"), bool(p.get("research", True)),
            start.get("lat"), start.get("lng"),
        ))

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
        result = await _in_pool(
            _build_reports_sync, job, [cap["file"]], _norm_langs(p.get("langs")), settings,
            None, bool(p.get("research", True)), start_lat, start_lng,
        )
        result["capture"] = cap
        return _checked(result)

    return {"report": report_job, "scene-report": scene_report_job}


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
    for kind, fn in _make_handlers(settings).items():
        _QUEUE.register(kind, fn)

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
        async with _CAPTURE_SEM:
            try:
                result = await capture_mod.capture(pose, settings, mode=mode)
            except Exception as exc:  # noqa: BLE001
                # 예전에는 여기서 예외가 그대로 올라가 클라이언트가 JSON 이 아닌
                # text/plain "Internal Server Error" 를 받아 .json() 파싱에서 터졌다.
                result = {"status": "CAPTURE_ERROR", "message": f"{type(exc).__name__}: {exc}"}
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
    def _submit(kind: str, payload: dict, label: str):
        assert _QUEUE is not None
        return _QUEUE.submit(kind, payload, label=label, client_key=str(payload.get("clientKey") or ""))

    @app.post("/api/jobs/report")
    async def api_job_report(payload: dict):
        payload = payload or {}
        if not (payload.get("files") or []):
            raise HTTPException(status_code=422, detail="리포트 생성에는 캡처(files)가 필요합니다.")
        try:
            job = await _submit("report", payload, payload.get("label") or "보고서")
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return JSONResponse({"job": job.public(), "position": _QUEUE.position(job.id),
                             "queue": _QUEUE.stats()})

    @app.post("/api/jobs/scene-report")
    async def api_job_scene_report(payload: dict):
        payload = payload or {}
        pose = payload.get("pose")
        if not pose or (pose.get("lat") is None and not pose.get("pano")):
            raise HTTPException(status_code=422, detail="유효한 pose(lat/lng 또는 pano)가 필요합니다.")
        try:
            job = await _submit("scene-report", payload, payload.get("label") or "바로 보고서")
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
        path = settings.reports_dir / Path(name).name
        # is_file() — 예전 exists() 는 디렉터리에도 True 라 FileResponse 가 500 을 냈다.
        if not path.is_file():
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

    from .tls import decide_tls, keylog_removed, neutralize_keylog

    _safe_stdout()
    # ssl 을 건드리기 전에 먼저. 이 값이 남아 있으면 create_default_context() 가
    # OPENSSL_Uplink 를 타고 ExitProcess(1) 로 프로세스를 끝내버린다(tls.py 주석 참고).
    neutralize_keylog()
    tls_mode = decide_tls()
    settings = load_settings()
    settings.port = _find_free_port(settings.port, settings.host)
    app = build_app(settings)

    url = f"http://localhost:{settings.port}/"
    kstats = knowledge.store_for(settings).stats() if settings.knowledge_enabled else {}
    banner = [
        "",
        "  == GeoGuessHelper ===============================",
        f"   브라우저 : {url}",
        f"   JS 지도 키   : {'설정됨' if settings.has_js_key else '없음 (지도/로드뷰 비활성, 추출은 동작)'}",
        f"   Static 캡처  : {'설정됨' if settings.has_static_key else '없음 (캡처 비활성)'}",
        f"   Claude 분석  : {'설정됨' if settings.has_anthropic else '없음 (분석 비활성)'}",
        f"   TLS 검증     : {'끔 (사내 프록시 감지 - verify=False)' if tls_mode == 'insecure' else '켬'}",
        *([f"   SSLKEYLOGFILE: 제거함 ({keylog_removed()}) - 백신 TLS 감청 지시"] if keylog_removed() else []),
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

    if not os.environ.get("GEOHELPER_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
