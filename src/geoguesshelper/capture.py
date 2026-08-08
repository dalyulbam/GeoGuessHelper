"""장면 캡처 — 서버측 재렌더.

브라우저 캔버스는 WebGL + 교차출처 타일로 tainted 되어 toDataURL 이 SecurityError 를 던진다.
따라서 캡처는 전량 서버에서 재렌더한다:
  · Approach B (기본):  Street View Static API + Static Maps API → Pillow 로 상하 합성.  (가장 신뢰)
  · Approach A (선택):  Playwright 헤드리스로 StreetViewPanorama 를 렌더 후 스크린샷.
                        render_roadview.py 의 LocalServer + wait + clip 패턴을 구글로 재타깃.
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path

from . import streetview
from .config import Settings


async def capture_static(pose: dict, settings: Settings) -> dict:
    """Approach B: 정적 이미지 2장(로드뷰▲/지도▼) 합성. 반환 {status, file?, pano_id?, message?}."""
    key = settings.effective_static_key
    if not key:
        return {
            "status": "NO_KEY",
            "message": "GOOGLE_MAPS_STATIC_KEY(또는 JS 키)가 필요합니다. .env 를 설정하세요.",
        }

    # 1) 무료 metadata 로 커버리지 선확인
    try:
        meta = await streetview.check_coverage(pose, key)
    except Exception as exc:  # noqa: BLE001
        return {"status": "META_ERROR", "message": f"메타데이터 조회 실패: {exc}"}

    status = meta.get("status")
    if status != "OK":
        err = meta.get("error_message")
        if status == "REQUEST_DENIED":
            msg = (
                "Street View Static API 가 거부됐습니다(REQUEST_DENIED). "
                "Google Cloud Console 에서 'Street View Static API' 와 'Maps Static API' 를 "
                "활성화하고, 이 키에 HTTP 리퍼러 제한이 걸려 있으면 서버용 키(IP 제한/무제한)를 "
                "GOOGLE_MAPS_STATIC_KEY 로 따로 지정하세요."
            )
            if err:
                msg += f"  (Google: {err})"
        elif status in ("ZERO_RESULTS", "NOT_FOUND"):
            msg = f"이 위치에는 스트리트뷰 커버리지가 없습니다 (status={status})."
        else:
            msg = err or f"메타데이터 상태={status}."
        return {"status": status or "NO_PANO", "message": msg, "pano_id": meta.get("pano_id")}

    # metadata 가 준 실제 파노 좌표/ID 로 스냅
    loc = meta.get("location") or {}
    snapped = dict(pose)
    if "lat" in loc and "lng" in loc:
        snapped["lat"], snapped["lng"] = loc["lat"], loc["lng"]
    if meta.get("pano_id") and not snapped.get("pano"):
        snapped["pano"] = meta["pano_id"]

    # 2) 이미지 2장 fetch
    sv_url = streetview.streetview_url(
        snapped, key, w=settings.viewport_w, h=settings.viewport_h
    )
    map_url = streetview.staticmap_url(
        snapped, key, w=settings.viewport_w, h=settings.map_h
    )
    try:
        sv_bytes = await streetview.fetch_bytes(sv_url)
        map_bytes = await streetview.fetch_bytes(map_url)
    except Exception as exc:  # noqa: BLE001
        return {"status": "FETCH_ERROR", "message": f"이미지 다운로드 실패: {exc}"}

    # 3) 합성 (Pillow) — 없으면 로드뷰 단독 저장
    fname = f"capture_{uuid.uuid4().hex[:12]}.png"
    out = settings.captures_dir / fname
    try:
        from PIL import Image  # type: ignore

        sv = Image.open(io.BytesIO(sv_bytes)).convert("RGB")
        mp = Image.open(io.BytesIO(map_bytes)).convert("RGB")
        if mp.width != sv.width:
            mp = mp.resize((sv.width, round(mp.height * sv.width / mp.width)))
        canvas = Image.new("RGB", (sv.width, sv.height + mp.height), "white")
        canvas.paste(sv, (0, 0))
        canvas.paste(mp, (0, sv.height))  # 하단 = 지도, 구글 로고 유지
        canvas.save(out, "PNG")
        composited = True
    except ModuleNotFoundError:
        out.write_bytes(sv_bytes)  # Pillow 없으면 로드뷰만
        composited = False
    except Exception as exc:  # noqa: BLE001
        return {"status": "COMPOSE_ERROR", "message": f"이미지 합성 실패: {exc}"}

    return {
        "status": "OK",
        "file": fname,
        "url": f"/captures/{fname}",
        "pano_id": snapped.get("pano") or meta.get("pano_id"),
        "lat": snapped.get("lat"),
        "lng": snapped.get("lng"),
        "heading": snapped.get("heading"),
        "pitch": snapped.get("pitch"),
        "date": meta.get("date"),
        "composited": composited,
        "mode": "static",
    }


# static 실패 사유가 "키/프로젝트 설정" 문제일 때만 브라우저 렌더로 폴백한다.
# ZERO_RESULTS/NOT_FOUND(진짜 커버리지 없음)는 브라우저로도 안 되므로 폴백 안 함.
_FALLBACKABLE = {"REQUEST_DENIED", "NO_KEY", "META_ERROR", "FETCH_ERROR", "OVER_QUERY_LIMIT"}


async def render_playwright(pose: dict, settings: Settings) -> dict:
    """Approach A: 브라우저용 JS 키만으로 헤드리스 Chromium 렌더 → 스크린샷."""
    import asyncio

    from . import render_google

    key = settings.js_api_key or settings.effective_static_key
    if not key:
        return {
            "status": "NO_KEY",
            "message": "브라우저 렌더에는 GOOGLE_MAPS_JS_API_KEY 가 필요합니다. .env 를 설정하세요.",
        }
    try:
        import playwright  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        return {
            "status": "NO_DEP",
            "message": "playwright 미설치 — `uv sync --extra all` 후 `playwright install chromium` 하세요.",
        }
    # 동기 Playwright 를 스레드로 오프로드(이벤트 루프 중첩/Windows Proactor 회피).
    return await asyncio.to_thread(render_google.render_sync, pose, settings, key)


async def capture(pose: dict, settings: Settings, *, mode: str = "auto") -> dict:
    """캡처 진입점.

    mode:
      · "static"     — Street View/Maps Static API 합성 (Approach B).
      · "playwright" — 헤드리스 브라우저 렌더 (Approach A, JS 키만 필요).
      · "auto"(기본) — static 먼저, 키/프로젝트 설정 문제로 막히면 자동으로 브라우저 렌더.
    """
    if mode == "playwright":
        return await render_playwright(pose, settings)
    if mode == "static":
        return await capture_static(pose, settings)

    # auto
    res = await capture_static(pose, settings)
    if res.get("status") == "OK":
        return res
    if res.get("status") in _FALLBACKABLE:
        pw = await render_playwright(pose, settings)
        if pw.get("status") == "OK":
            pw["fallback_from"] = res.get("status")
            return pw
        # 둘 다 실패 → static 사유를 함께 실어 더 설명적으로.
        pw.setdefault("static_status", res.get("status"))
        pw.setdefault("static_message", res.get("message"))
        return pw
    return res  # ZERO_RESULTS 등은 그대로(브라우저로도 해결 안 됨)
