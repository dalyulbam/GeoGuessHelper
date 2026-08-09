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

from . import streetview
from .config import Settings


def _ext_of(data: bytes) -> str:
    """매직 바이트로 실제 형식 판정 — 확장자와 내용이 어긋나지 않게."""
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "jpg"


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

    # 제3자(사용자 기여) 파노는 Static API 로도 이미지를 받을 수 없다. metadata 의 copyright
    # 로 미리 걸러 헛된 요청과 검은 캡처를 막는다 — 구글 공식은 "© <연도> Google" 형태다.
    cp = str(meta.get("copyright") or "")
    if cp and "Google" not in cp:
        return {
            "status": "THIRD_PARTY_ONLY",
            "message": f"제3자 파노라마({cp.strip()}) — 이미지를 받을 수 없습니다.",
            "third_party": cp.strip(),
            "pano_id": meta.get("pano_id"),
        }

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
    #    저장 형식은 settings.capture_format. JPEG 가 기본인 이유: 해상도(=토큰 수)는 그대로
    #    두면서 바이트만 ~6배 줄어든다. 이 이미지는 분석 업로드에도, 보고서 base64 임베드에도
    #    쓰이므로 절감이 두 번 먹는다.
    jpeg = settings.capture_format == "jpeg"
    fname = f"capture_{uuid.uuid4().hex[:12]}.{'jpg' if jpeg else 'png'}"
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
        if jpeg:
            canvas.save(out, "JPEG", quality=settings.capture_quality, optimize=True)
        else:
            canvas.save(out, "PNG")
        composited = True
    except ModuleNotFoundError:
        # Pillow 없으면 로드뷰 원본만 — 확장자는 실제 바이트에 맞춘다(잘못된 확장자로
        # 저장하면 나중에 media_type 이 어긋나 분석이 거부된다).
        fname = f"capture_{uuid.uuid4().hex[:12]}.{_ext_of(sv_bytes)}"
        out = settings.captures_dir / fname
        out.write_bytes(sv_bytes)
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

# 브라우저 렌더가 검은 화면/제3자 파노를 보고했을 때 — 이건 static 으로도 해결되지 않는다.
_UNRENDERABLE = {"THIRD_PARTY_ONLY", "BLACK_RENDER"}


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
    if res.get("status") == "THIRD_PARTY_ONLY":
        # 브라우저 렌더는 근처 **공식** 파노로 갈아탈 수 있다 — 그 길이 남아 있다.
        pw = await render_playwright(pose, settings)
        if pw.get("status") == "OK":
            pw["fallback_from"] = "THIRD_PARTY_ONLY"
            return pw
        pw.setdefault("third_party", res.get("third_party"))
        return pw
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
