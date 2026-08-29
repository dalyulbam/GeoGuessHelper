"""Street View Static API + Static Maps API + 무료 Metadata 커버리지 확인.

서버측 GET 이므로 리퍼러 제한 키가 아니라 IP 제한/무제한 키(GOOGLE_MAPS_STATIC_KEY)를 쓴다.
Metadata 엔드포인트는 쿼터/과금 없음 → 이미지 요청 전에 커버리지를 선확인해 헛돈을 막는다.
"""
from __future__ import annotations

from urllib.parse import urlencode

from .tls import aget

STATIC_BASE = "https://maps.googleapis.com/maps/api/streetview"
META_BASE = "https://maps.googleapis.com/maps/api/streetview/metadata"
STATICMAP_BASE = "https://maps.googleapis.com/maps/api/staticmap"
ELEVATION_BASE = "https://maps.googleapis.com/maps/api/elevation/json"


def _loc(pose: dict) -> str:
    return f'{pose["lat"]},{pose["lng"]}'


def view_aspect(pose: dict, settings) -> float:
    """화면 로드뷰 패널(#pano)의 종횡비(폭/높이).

    보고서에 들어가는 캡처는 화면 캔버스 **전체**를 담아야 한다. 화면 패널은 창 크기를
    따르는 가변 비율(와이드 모니터에서 2.5:1 안팎)인데, 캡처가 4:3 고정이면 화면
    좌우가 잘려 나간다 — 그래서 클라이언트가 캡처 시점의 실제 패널 크기(viewW/viewH)를
    포즈에 실어 보내고, 서버 재렌더는 전부 이 비율을 따른다.
    값이 없으면(구 클라이언트·API 직접 호출) 설정 기본값(4:3)으로 물러선다.
    """
    try:
        w = float(pose.get("viewW") or 0)
        h = float(pose.get("viewH") or 0)
        if w > 0 and h > 0:
            return max(0.4, min(3.5, w / h))
    except (TypeError, ValueError):
        pass
    return settings.viewport_w / max(1, settings.viewport_h)


def streetview_url(pose: dict, key: str, *, w: int = 640, h: int = 480) -> str:
    params: dict = {
        "size": f"{w}x{h}",
        "fov": min(120, int(round(pose.get("fov") or 90))),
        "pitch": round(pose.get("pitch") or 0.0, 2),
        "source": "outdoor",
        "return_error_code": "true",
        "key": key,
    }
    if pose.get("heading") is not None:
        params["heading"] = round(pose["heading"], 2)
    if pose.get("pano"):
        params["pano"] = pose["pano"]
    else:
        params["location"] = _loc(pose)
        params["radius"] = pose.get("radius") or 50
    return f"{STATIC_BASE}?{urlencode(params)}"


def staticmap_url(pose: dict, key: str, *, w: int = 640, h: int = 260, zoom: int = 15,
                  maptype: str = "hybrid", scale: int = 2) -> str:
    center = _loc(pose)
    params = {
        "center": center,
        "zoom": zoom,
        "size": f"{w}x{h}",
        "scale": scale,
        "maptype": maptype,   # hybrid=위성+라벨(근거리) · roadmap/terrain=구조·지형(원거리)
        "markers": f"color:red|{center}",
        "key": key,
    }
    return f"{STATICMAP_BASE}?{urlencode(params, safe='|,:')}"


async def check_coverage(pose: dict, key: str, *, timeout: float = 15.0) -> dict:
    """{status, pano_id, location:{lat,lng}, date, copyright}. status != OK 면 커버리지 없음."""
    params: dict = {"key": key}
    if pose.get("pano"):
        params["pano"] = pose["pano"]
    else:
        params["location"] = _loc(pose)
        params["radius"] = pose.get("radius") or 50
    resp = await aget(META_BASE, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


async def elevation(lat: float, lng: float, key: str, *, timeout: float = 15.0) -> float | None:
    """보고서 1점의 고도(m). 실패하면 None — 표시용 부가 정보라 보고서를 막지 않는다.

    §terrain 의 조건부 허용 범위: 이 숫자 하나만 표시용으로 쓴다. 지형 배경(격자·타일)을
    만들거나 영구 저장하지 않는다 — 그 정본은 공개 DEM(§terrain, terrain.py)이다.
    """
    try:
        resp = await aget(ELEVATION_BASE, params={"locations": f"{lat},{lng}", "key": key},
                          timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK" and data.get("results"):
            return float(data["results"][0]["elevation"])
    except Exception:  # noqa: BLE001 — 부가 정보 실패가 보고서를 막으면 안 된다
        pass
    return None


async def fetch_bytes(url: str, *, timeout: float = 25.0) -> bytes:
    resp = await aget(url, follow_redirects=True, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def fetch_bytes_sync(url: str, *, timeout: float = 20.0) -> bytes:
    """동기 GET — 리포트 조립(워커 스레드)에서 쓴다.

    aget() 과 같은 TLS 전략을 따른다: 인증서 오류를 만나면 **그 호스트에 한해** verify 를
    끄고 1회 재시도한다(전역 다운그레이드 아님 — tls.mark_insecure 참고).
    """
    import httpx

    from .tls import _forced, _host_of, httpx_verify, is_cert_error, mark_insecure

    host = _host_of(url)
    verifies = [httpx_verify(host)]
    if verifies[0] is not False and _forced() is not False:
        verifies.append(False)
    last: BaseException | None = None
    for verify in verifies:
        try:
            with httpx.Client(verify=verify, follow_redirects=True, timeout=timeout) as c:
                resp = c.get(url)
                resp.raise_for_status()
                return resp.content
        except Exception as exc:  # noqa: BLE001
            last = exc
            if verify is not False and is_cert_error(exc):
                mark_insecure(host)
                continue
            raise
    assert last is not None
    raise last
