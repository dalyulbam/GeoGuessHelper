"""geoguesshelper-server — 로컬 브라우저 UI.

    uv run geoguesshelper-server        # → http://localhost:8765/ 자동 오픈

엔드포인트:
    GET  /                     → webui/index.html (2분할 뷰어 SPA)
    GET  /webui/{path}         → 정적 (app.js, styles.css)
    GET  /api/config           → 프론트 설정 (키 보유 여부 + 브라우저 JS 키)
    POST /api/extract          → {"url"} → 파싱된 포즈 {lat,lng,pano,heading,pitch,fov}
    POST /api/capture          → {"pose", "mode"?} → 캡처 결과 {status,file,url,...}
    POST /api/analyze          → {"files":[...]} → Claude 비전 구조화 분석
    GET  /captures/{name}      → 캡처 이미지 서빙
"""
from __future__ import annotations

import socket
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import capture as capture_mod
from . import i18n
from . import linkresolver
from . import report as report_mod
from . import research as research_mod
from .analyze import analyze_captures
from .config import Settings, load_settings


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


def _multi_reports_sync(
    files: list[str], langs: list[str], settings: Settings, primary: dict | None,
    do_research: bool = True, start_lat=None, start_lng=None,
) -> dict:
    """언어별로 (필요시 분석 → 웹 리서치 →) 리포트 생성. primary = 이미 계산된 기본 분석(재사용).
    언어가 1개면 단일 파일, 2개 이상이면 **한 파일에 언어별 섹션 + 스위처**(통합 리포트).
    do_research=True 면 식별 장소를 웹 검색으로 조사해 도시 프로파일을 리포트에 넣는다.
    start_lat/start_lng = 링크 시작 좌표(파일명에 기록).

    반환 {status, reports:[{file,url,lang,langName,langs,hasResearch,combined}], errors,
          primaryAnalysis, cost_usd}.
    """
    cache: dict[str, dict] = {}
    if isinstance(primary, dict) and (primary.get("analysis") or primary.get("best_guess")):
        plang = i18n.normalize(primary.get("lang") or (langs[0] if langs else i18n.DEFAULT_LANG))
        # 원(raw) analysis(best_guess 만 있는 형태)로 넘어오면 표준 응답 형태로 감싼다.
        if not primary.get("analysis") and primary.get("best_guess"):
            primary = {"status": "OK", "analysis": primary, "lang": plang}
        cache[plang] = primary

    sections: list[dict] = []   # [{lang, result, research}] — 통합/단일 리포트 공통 입력
    errors: list[dict] = []
    research_cache: dict[str, dict] = {}
    primary_used: dict | None = None
    total_cost = 0.0

    for lang in langs:
        res = cache.get(lang)
        if res is None:
            if not settings.has_anthropic:
                errors.append({"lang": lang, "message": "ANTHROPIC_API_KEY 가 필요합니다(.env)."})
                continue
            paths = [settings.captures_dir / Path(f).name for f in files]
            res = analyze_captures(paths, settings, lang)
            cache[lang] = res
            if res.get("status") == "OK":
                total_cost += res.get("cost_usd") or 0.0
        if res.get("status") != "OK" or not res.get("analysis"):
            errors.append({"lang": lang, "message": res.get("message") or res.get("status") or "분석 실패"})
            continue

        # 웹 리서치(도시 프로파일) — 실패해도 리포트는 프로파일 없이 생성(soft).
        rsrch = None
        if do_research and settings.has_anthropic:
            rsrch = research_cache.get(lang)
            if rsrch is None:
                place = (res.get("analysis") or {}).get("best_guess") or {}
                rsrch = research_mod.research_location(place, settings, lang)
                research_cache[lang] = rsrch
                total_cost += rsrch.get("cost_usd") or 0.0
            if rsrch.get("status") != "OK":
                rsrch = None

        sections.append({"lang": lang, "result": res, "research": rsrch})
        if primary_used is None:
            primary_used = res

    reports: list[dict] = []
    if len(sections) == 1:
        s = sections[0]
        rep = report_mod.build_report(s["result"], files, settings, s["lang"],
                                      research=s["research"], start_lat=start_lat, start_lng=start_lng)
        rep["langName"] = i18n.native_name(s["lang"])
        rep["langs"] = [s["lang"]]
        rep["hasResearch"] = bool(s["research"])
        rep["combined"] = False
        reports.append(rep)
    elif len(sections) >= 2:
        rep = report_mod.build_combined_report(sections, files, settings,
                                               start_lat=start_lat, start_lng=start_lng)
        if rep.get("status") == "OK":
            rep["langName"] = " · ".join(i18n.native_name(s["lang"]) for s in sections)
            rep["langs"] = [s["lang"] for s in sections]
            rep["hasResearch"] = any(s["research"] for s in sections)
            rep["combined"] = True
            reports.append(rep)
        else:
            errors.append({"lang": "*", "message": rep.get("message") or "통합 리포트 생성 실패"})

    return {
        "status": "OK" if reports else "FAIL",
        "reports": reports,
        "errors": errors,
        "primaryAnalysis": primary_used,
        "cost_usd": round(total_cost, 4),
        "message": None if reports else (errors[0]["message"] if errors else "리포트 생성 실패"),
    }

WEBUI_DIR = Path(__file__).parent / "webui"


def build_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="GeoGuessHelper", version="0.1.0")

    @app.get("/")
    async def index():
        return FileResponse(WEBUI_DIR / "index.html")

    @app.get("/api/config")
    async def api_config():
        return JSONResponse(settings.public_config())

    @app.post("/api/extract")
    async def api_extract(payload: dict):
        url = (payload or {}).get("url", "")
        try:
            pose = await linkresolver.extract(url)
        except linkresolver.ParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001  (네트워크/리다이렉트 실패 등)
            raise HTTPException(status_code=502, detail=f"링크 해제 실패: {exc}") from exc
        return pose

    @app.post("/api/capture")
    async def api_capture(payload: dict):
        pose = (payload or {}).get("pose")
        mode = (payload or {}).get("mode", "auto")
        if not pose or pose.get("lat") is None and not pose.get("pano"):
            raise HTTPException(status_code=422, detail="유효한 pose(lat/lng 또는 pano)가 필요합니다.")
        result = await capture_mod.capture(pose, settings, mode=mode)
        return JSONResponse(result)

    @app.post("/api/analyze")
    async def api_analyze(payload: dict):
        payload = payload or {}
        files = payload.get("files") or []
        lang = i18n.normalize(payload.get("lang") or settings.report_lang)
        paths = [settings.captures_dir / Path(f).name for f in files]
        # 동기 SDK 호출 → 스레드로 오프로드
        result = await _run_in_thread(analyze_captures, paths, settings, lang)
        return JSONResponse(result)

    @app.post("/api/report")
    async def api_report(payload: dict):
        """캡처(files)로부터 언어별 리포트 생성. langs 각각에 대해 필요시 분석 후 리포트.
        analysis(기본 분석)를 넘기면 그 언어는 재분석 없이 재사용한다."""
        payload = payload or {}
        files = payload.get("files") or []
        langs = _norm_langs(payload.get("langs") or payload.get("lang") or settings.report_lang)
        primary = payload.get("analysis")
        do_research = payload.get("research", True)
        start = payload.get("start") or {}
        if not files:
            raise HTTPException(status_code=422, detail="리포트 생성에는 캡처(files)가 필요합니다.")
        try:
            result = await _run_in_thread(
                _multi_reports_sync, files, langs, settings, primary, do_research,
                start.get("lat"), start.get("lng"),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"리포트 생성 실패: {exc}") from exc
        return JSONResponse(result)

    @app.post("/api/scene-report")
    async def api_scene_report(payload: dict):
        """한 방에: 현재 포즈 캡처 → 언어별 분석+리포트. '바로 보고서 추출' 버튼용."""
        payload = payload or {}
        pose = payload.get("pose")
        mode = payload.get("mode", "auto")
        langs = _norm_langs(payload.get("langs") or settings.report_lang)
        do_research = payload.get("research", True)
        start = payload.get("start") or {}
        if not pose or (pose.get("lat") is None and not pose.get("pano")):
            raise HTTPException(status_code=422, detail="유효한 pose(lat/lng 또는 pano)가 필요합니다.")
        # 시작 좌표: 명시된 start 우선, 없으면 캡처 대상 pose 의 좌표(파일명 기록용).
        start_lat = start.get("lat") if start.get("lat") is not None else pose.get("lat")
        start_lng = start.get("lng") if start.get("lng") is not None else pose.get("lng")
        cap = await capture_mod.capture(pose, settings, mode=mode)
        if cap.get("status") != "OK":
            return JSONResponse(
                {"status": "CAPTURE_FAIL", "capture": cap, "message": cap.get("message")}
            )
        try:
            result = await _run_in_thread(
                _multi_reports_sync, [cap["file"]], langs, settings, None, do_research,
                start_lat, start_lng,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"리포트 생성 실패: {exc}") from exc
        result["capture"] = cap
        return JSONResponse(result)

    @app.get("/captures/{name}")
    async def get_capture(name: str):
        safe = Path(name).name
        path = settings.captures_dir / safe
        if not path.exists():
            raise HTTPException(status_code=404, detail="캡처를 찾을 수 없습니다.")
        return FileResponse(path)

    @app.get("/reports/{name}")
    async def get_report(name: str):
        safe = Path(name).name
        path = settings.reports_dir / safe
        if not path.exists():
            raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다.")
        return FileResponse(path)

    if WEBUI_DIR.exists():
        app.mount("/webui", StaticFiles(directory=str(WEBUI_DIR)), name="webui")

    return app


async def _run_in_thread(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)


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
    """Windows 한국어(cp949) 콘솔에서 유니코드 기호로 print 가 죽지 않도록 UTF-8 로 재구성(best-effort)."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    import uvicorn

    from .tls import decide_tls

    _safe_stdout()
    tls_mode = decide_tls()  # httpx(캡처)/anthropic(분석)의 인증서 검증 방식 판정(프로브 1회)
    settings = load_settings()
    settings.port = _find_free_port(settings.port, settings.host)
    app = build_app(settings)

    url = f"http://localhost:{settings.port}/"
    # 배너는 cp949 로도 안전한 문자(ASCII + 한글)만 사용 — 박스/체크/em-dash 금지.
    banner = [
        "",
        "  == GeoGuessHelper ===============================",
        f"   브라우저 : {url}",
        f"   JS 지도 키   : {'설정됨' if settings.has_js_key else '없음 (지도/로드뷰 비활성, 추출은 동작)'}",
        f"   Static 캡처  : {'설정됨' if settings.has_static_key else '없음 (캡처 비활성)'}",
        f"   Claude 분석  : {'설정됨' if settings.has_anthropic else '없음 (분석 비활성)'}",
        f"   TLS 검증     : {'끔 (사내 프록시 감지 - verify=False)' if tls_mode == 'insecure' else '켬'}",
        "  =================================================",
        "",
    ]
    try:
        print("\n".join(banner), flush=True)
    except UnicodeEncodeError:
        print("GeoGuessHelper ->", url, flush=True)

    # uvicorn 이 뜬 뒤 브라우저 자동 오픈 (GEOHELPER_NO_BROWSER=1 이면 생략 — 테스트/헤드리스용)
    import os

    if not os.environ.get("GEOHELPER_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
