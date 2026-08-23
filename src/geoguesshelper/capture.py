"""장면 캡처 — 서버측 재렌더.

브라우저 캔버스는 WebGL + 교차출처 타일로 tainted 되어 toDataURL 이 SecurityError 를 던진다.
따라서 캡처는 전량 서버에서 재렌더한다:
  · Approach B (기본):  Street View Static API + Static Maps API → Pillow 로 상하 합성.  (가장 신뢰)
  · Approach A (선택):  Playwright 헤드리스로 StreetViewPanorama 를 렌더 후 스크린샷.
                        render_roadview.py 의 LocalServer + wait + clip 패턴을 구글로 재타깃.
"""
from __future__ import annotations

import asyncio
import io
import math
import re
import uuid

from . import streetview
from .config import Settings


def _fnum(v, d: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def hfov_deg(v_deg: float, aspect: float) -> float:
    """수직 FOV(도) + 종횡비 → 수평 FOV(도). sphere.js 의 hFovFor 와 같은 식."""
    v = max(1.0, min(170.0, v_deg))
    return math.degrees(2 * math.atan(aspect * math.tan(math.radians(v) / 2)))


def _ext_of(data: bytes) -> str:
    """매직 바이트로 실제 형식 판정 — 확장자와 내용이 어긋나지 않게."""
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "jpg"


# lh3 이미지 URL 의 시점/크기 접미사:  =w<너비>-h<높이>-k-no-pi<pitch>-ya<heading>-ro<roll>-fo<fov>
_PHOTO_SUFFIX_RE = re.compile(r"=w\d+-h\d+.*$")


def photo_url_for(photo_url: str, pose: dict, *, w: int, h: int) -> str:
    """공유 링크의 이미지 URL을 **현재 시점**으로 다시 겨눈다.

    사용자가 로드뷰에서 방향을 돌렸으면 그 heading/pitch/fov 로 받아야 한다.
    실측 확인: ya(heading)를 180도 돌리면 실제로 반대편이 나오고, w/h·fo 도 반영된다.
    """
    base = _PHOTO_SUFFIX_RE.sub("", photo_url)

    def num(v, d):
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            return d

    # lh3 의 `-ya` 는 **그 사진구체 자체의 좌표계**다. 지도 URL 의 heading(진북)과 다를 수 있어서
    # (실측: 같은 링크에서 h=36.84 인데 ya=18.8405, 차이 18.0°) 링크에서 뽑아 둔 오프셋을 뺀다.
    off = num(pose.get("ya_offset"), 0.0)
    ya = (num(pose.get("heading"), 0.0) - off) % 360.0
    # lh3 의 `-pi` 는 **양수가 아래**다(실측: pi +60 이 천저 쪽과 일치). pose["pitch"] 는
    # 반대로 양수가 위다(StreetViewPov 규약). 그래서 부호를 뒤집어 보낸다 —
    # 예전엔 그대로 보내서 화면과 캡처가 지평선 기준으로 뒤집혀 있었다.
    pi = -num(pose.get("pitch"), 0.0)
    # pose["fov"] 는 **수직** FOV 다(구글맵 URL 의 `75y` 규약, 뷰어도 이 값을 들고 다닌다).
    # 그런데 lh3 의 `-fo` 는 **수평** FOV 다. 예전엔 수직값을 그대로 넣어서 캡처가 화면보다
    # 좁게 잘렸다. 실측 근거: 그 링크는 `75y` 인데 같은 URL 의 `-fo` 가 100 이었고,
    # 수직 75°·종횡비 1.5 를 수평으로 바꾸면 98.0° 로 맞아떨어진다.
    v = max(20.0, min(120.0, num(pose.get("fov"), 90.0)))
    aspect = (float(w) / float(h)) if h else 1.5
    fo = round(math.degrees(2 * math.atan(aspect * math.tan(math.radians(v) / 2))), 2)
    fo = max(20.0, min(150.0, fo))
    return f"{base}=w{int(w)}-h{int(h)}-k-no-pi{pi}-ya{ya}-ro0-fo{fo}"


async def capture_photo_url(pose: dict, settings: Settings) -> dict:
    """공유 링크에 들어 있던 이미지 URL로 사용자 기여 파노를 직접 받아 캡처한다.

    왜 이 경로가 필요한가 — 사용자 기여 파노는 Maps JS API 가 타일을 키 없는 lh3 CDN 에서
    받는데, 그 CDN 이 IP 단위 제한(HTTP 429)을 걸면 화면이 통째로 검게 남는다.
    반면 구글이 **공유 링크 안에 직접 넣어 준** 이 URL 은 그대로 받아진다(실측 116KB JPEG).
    하단 지도는 기존 경로(Static → 실패 시 브라우저 렌더)를 그대로 쓴다.
    """
    photo = pose.get("photo_url")
    if not photo:
        return {"status": "NO_PHOTO_URL", "message": "링크에 직접 이미지 URL(!6s)이 없습니다."}

    # 화면 패널 종횡비 그대로 받는다 — photo_url_for 가 w/h 로 수평 FOV 를 계산하므로,
    # 비율만 맞으면 수직·수평 시야가 화면 캔버스와 정확히 일치한다.
    aspect = streetview.view_aspect(pose, settings)
    h = settings.viewport_h * 2
    w = round(h * aspect)
    if w > 2048:                      # 와이드 화면에서 폭이 과대해지면 비율 유지한 채 줄인다
        w, h = 2048, max(200, round(2048 / aspect))
    url = photo_url_for(photo, pose, w=w, h=h)
    try:
        sv_bytes = await asyncio.to_thread(streetview.fetch_bytes_sync, url, timeout=25.0)
    except Exception as exc:  # noqa: BLE001
        return {"status": "PHOTO_FETCH_ERROR", "message": f"이미지 URL 다운로드 실패: {exc}"}
    if not sv_bytes or len(sv_bytes) < 2000:
        return {"status": "PHOTO_FETCH_ERROR", "message": "이미지가 비어 있습니다."}

    map_bytes = await _map_panel(pose, settings)
    return _compose(sv_bytes, map_bytes, pose, settings, mode="photo_url",
                    extra={"photo_url": url, "third_party": True})


async def _map_panel(pose: dict, settings: Settings) -> bytes:
    """하단 지도 패널. Static API 우선, 리퍼러 제한으로 막히면 브라우저 렌더로 폴백."""
    key = settings.effective_static_key
    if key and pose.get("lat") is not None:
        try:
            u = streetview.staticmap_url(pose, key, w=settings.viewport_w, h=settings.map_h)
            b = await asyncio.to_thread(streetview.fetch_bytes_sync, u, timeout=20.0)
            if b and len(b) > 1000:
                return b
        except Exception:  # noqa: BLE001
            pass
    js_key = settings.js_api_key or key
    if js_key and pose.get("lat") is not None:
        from . import render_google

        try:
            saved = settings.report_map_levels
            settings.report_map_levels = [{"zoom": 15, "maptype": "hybrid", "label": "map"}]
            try:
                blobs = await asyncio.to_thread(
                    render_google.render_locator_maps, pose["lat"], pose["lng"], settings, js_key
                )
            finally:
                settings.report_map_levels = saved
            if blobs and blobs[0]:
                return blobs[0]
        except Exception:  # noqa: BLE001
            pass
    return b""


def _compose(sv_bytes: bytes, map_bytes: bytes, pose: dict, settings: Settings,
             *, mode: str, extra: dict | None = None) -> dict:
    """로드뷰▲ / 지도▼ 합성 후 저장. capture_static 과 같은 형태의 dict 를 반환."""
    jpeg = settings.capture_format == "jpeg"
    fname = f"capture_{uuid.uuid4().hex[:12]}.{'jpg' if jpeg else 'png'}"
    out = settings.captures_dir / fname
    composited = False
    try:
        from PIL import Image  # type: ignore

        sv = Image.open(io.BytesIO(sv_bytes)).convert("RGB")
        if map_bytes:
            mp = Image.open(io.BytesIO(map_bytes)).convert("RGB")
            if mp.width != sv.width:
                mp = mp.resize((sv.width, round(mp.height * sv.width / mp.width)))
            canvas = Image.new("RGB", (sv.width, sv.height + mp.height), "white")
            canvas.paste(sv, (0, 0))
            canvas.paste(mp, (0, sv.height))
            composited = True
        else:
            canvas = sv
        if jpeg:
            canvas.save(out, "JPEG", quality=settings.capture_quality, optimize=True)
        else:
            canvas.save(out, "PNG")
    except ModuleNotFoundError:
        fname = f"capture_{uuid.uuid4().hex[:12]}.{_ext_of(sv_bytes)}"
        out = settings.captures_dir / fname
        out.write_bytes(sv_bytes)
    except Exception as exc:  # noqa: BLE001
        return {"status": "COMPOSE_ERROR", "message": f"이미지 합성 실패: {exc}"}

    res = {
        "status": "OK",
        "file": fname,
        "url": f"/captures/{fname}",
        "pano_id": pose.get("pano"),
        "lat": pose.get("lat"),
        "lng": pose.get("lng"),
        "heading": pose.get("heading"),
        "pitch": pose.get("pitch"),
        "composited": composited,
        "mode": mode,
    }
    res.update(extra or {})
    return res


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

    # 2) 이미지 2장 fetch — 크기와 시야를 화면 패널에 맞춘다.
    #    Static API 는 640×640 상한이 있으므로 그 안에서 종횡비만 화면과 같게 잡는다.
    aspect = streetview.view_aspect(pose, settings)
    cap = min(640, settings.viewport_w)
    if aspect >= 1.0:
        sv_w, sv_h = cap, max(120, round(cap / aspect))
    else:
        sv_w, sv_h = max(120, round(cap * aspect)), cap
    # Static API 의 fov 파라미터는 **수평** FOV 다(공식 문서). pose["fov"] 는 수직(`75y`
    # 규약)이므로 변환해서 보낸다 — 그대로 넣으면 화면보다 좁게 잘린다(lh3 `-fo` 와 같은
    # 실수였다). 수평 상한 120° 를 넘는 와이드 화면은 여기서는 다 못 담는다 — auto 모드가
    # 그 경우 브라우저 렌더를 먼저 시도하고, 여기 왔다면 결과에 fov_capped 로 표시한다.
    v = max(20.0, min(120.0, _fnum(snapped.get("fov"), float(settings.default_fov))))
    hfov = hfov_deg(v, aspect)
    snapped["fov"] = min(120.0, hfov)
    sv_url = streetview.streetview_url(snapped, key, w=sv_w, h=sv_h)
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

    res = {
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
    if hfov > 120.0:
        # 화면의 수평 시야가 Static 상한을 넘었다 — 캡처 좌우가 화면보다 좁다.
        res["fov_capped"] = round(hfov, 1)
    return res


# static 실패 사유가 "키/프로젝트 설정" 문제일 때만 브라우저 렌더로 폴백한다.
# ZERO_RESULTS/NOT_FOUND(진짜 커버리지 없음)는 브라우저로도 안 되므로 폴백 안 함.
_FALLBACKABLE = {"REQUEST_DENIED", "NO_KEY", "META_ERROR", "FETCH_ERROR", "OVER_QUERY_LIMIT"}


def _playwright_ready(settings: Settings) -> bool:
    """브라우저 렌더를 시도할 수 있는가 — 키가 있고 playwright 가 설치돼 있는가."""
    if not (settings.js_api_key or settings.effective_static_key):
        return False
    try:
        import playwright  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


async def render_playwright(pose: dict, settings: Settings, *, official_only: bool = False) -> dict:
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
    res = await asyncio.to_thread(
        lambda: render_google.render_sync(pose, settings, key, official_only=official_only)
    )
    # 타일이 레이트리밋(429)으로 검게 나왔다면, 좌표가 있는 한 근처 **공식** 파노로 한 번
    # 재시도한다. 공식 파노는 키 기반 엔드포인트라 이 제한을 받지 않는다.
    # 요청한 지점과 다른 곳이므로 결과에 반드시 표시한다.
    # 브라우저가 토큰을 잡아 왔다면 그것으로 직접 이미지를 받는다.
    # 이게 제3자 파노의 **정공법**이다 — 요청한 바로 그 장면을 그대로 얻는다.
    if res.get("status") == "USE_PHOTO_URL" and res.get("photo_base"):
        p2 = dict(pose)
        p2["photo_url"] = res["photo_base"]
        ph = await capture_photo_url(p2, settings)
        if ph.get("status") == "OK":
            ph["recovered_from"] = "tiles_rate_limited"
            ph["copyright"] = res.get("copyright")
            ph["pano_id"] = res.get("rendered_pano_id") or pose.get("pano")
            if res.get("lat") is not None:
                ph["lat"], ph["lng"] = res["lat"], res["lng"]
            return ph
        res = {"status": "TILES_RATE_LIMITED",
               "message": ph.get("message") or "타일 제한 후 직접 이미지도 실패했습니다.",
               "tiles": res.get("tiles")}

    if (not official_only
            and res.get("status") in ("TILES_RATE_LIMITED", "BLACK_RENDER")
            and pose.get("lat") is not None and pose.get("lng") is not None):
        alt = await asyncio.to_thread(
            lambda: render_google.render_sync(pose, settings, key, official_only=True)
        )
        if alt.get("status") == "OK":
            alt["substituted"] = True
            alt["substituted_reason"] = res.get("status")
            alt["requested_pano_id"] = pose.get("pano")
            alt["third_party"] = res.get("third_party")
            return alt
    return res


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

    # auto —
    # 링크가 직접 이미지 URL(!6s)을 줬다면 그 길을 **먼저** 쓴다.
    #
    # 예전엔 여기에 `is_usergenerated(pano)` 조건을 걸었는데 그게 틀렸다.
    # 파노 ID 형식으로는 제3자 여부를 판정할 수 없다 — 실측 반례:
    #   ApCZpYJ184e4gUYfXHl65g  22자·official 형식인데 실제 저작권은 © 2026 Google
    #   (반대로 업체/전문 기여자 파노도 같은 22자 형식을 받는다)
    # 저작권 문자열만이 신뢰할 수 있는 판별자이고, 그건 브라우저를 띄워야 알 수 있다.
    # 그러니 조건을 없앤다: !6s 가 있다는 것 자체가 "구글이 이 장면의 이미지 URL을
    # 링크에 넣어 줬다"는 뜻이고, 그 길은 lh3 429 를 타지 않는다.
    if pose.get("photo_url"):
        ph = await capture_photo_url(pose, settings)
        if ph.get("status") == "OK":
            return ph
        # 실패하면 아래 일반 경로로 계속 — 이 길이 막혀도 캡처를 포기하지 않는다.

    # 화면 패널이 넓어 필요한 수평 시야가 Static API 상한(120°)을 넘으면, Static 으로는
    # 화면 캔버스 전체를 담을 수 없다. 그 경우 화면과 **같은 SDK·같은 종횡비·같은 zoom**
    # 으로 그리는 브라우저 렌더가 유일하게 전체를 보장하므로 순서를 뒤집는다.
    tried_pw = False
    aspect = streetview.view_aspect(pose, settings)
    v = max(20.0, min(120.0, _fnum(pose.get("fov"), float(settings.default_fov))))
    if hfov_deg(v, aspect) > 120.0 and _playwright_ready(settings):
        tried_pw = True
        pw = await render_playwright(pose, settings)
        if pw.get("status") == "OK":
            return pw
        # 브라우저 렌더가 실패해도 캡처를 포기하지 않는다 — Static(120° 상한)으로 계속.

    res = await capture_static(pose, settings)
    if res.get("status") == "OK":
        return res
    if not tried_pw and res.get("status") in _FALLBACKABLE:
        pw = await render_playwright(pose, settings)
        if pw.get("status") == "OK":
            pw["fallback_from"] = res.get("status")
            return pw
        # 둘 다 실패 → static 사유를 함께 실어 더 설명적으로.
        pw.setdefault("static_status", res.get("status"))
        pw.setdefault("static_message", res.get("message"))
        return pw
    return res  # ZERO_RESULTS 등은 그대로(브라우저로도 해결 안 됨)
