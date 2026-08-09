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
import socket
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import capture as capture_mod
from . import i18n, jobs, knowledge, linkresolver, translate
from . import report as report_mod
from . import research as research_mod
from .analyze import analyze_captures
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
            job.emit("recall", f"지식 저장소에서 관련 사실 {len(known)}건 회상 — 재조사 생략", 35)

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
    if batch:
        for res in _llm.gather(batch, settings):
            if isinstance(res, Exception):
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
                continue
            need_a = lang != analysis_lang
            need_p = base_profile is not None and lang != research_lang
            total_cost += tb.get("cost_usd") or 0.0
            res_l = dict(base_result)
            res_l["lang"] = lang
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
        # 사용자를 더 기다리게 하는 것보다 낫다.
        job.emit("knowledge", f"예산 초과({elapsed():.0f}초) — 지식 적재 생략", 98)
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
        "script": {"file": script_file, "sections": len((script_doc or {}).get("sections") or []),
                   **((script_doc or {}).get("_meta") or {})} if script_doc else None,
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

    app = FastAPI(title="GeoGuessHelper", version="0.2.0", lifespan=lifespan)

    @app.get("/")
    async def index():
        return FileResponse(WEBUI_DIR / "index.html")

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
