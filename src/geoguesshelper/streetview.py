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


def _loc(pose: dict) -> str:
    return f'{pose["lat"]},{pose["lng"]}'


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


def staticmap_url(pose: dict, key: str, *, w: int = 640, h: int = 260, zoom: int = 15) -> str:
    center = _loc(pose)
    params = {
        "center": center,
        "zoom": zoom,
        "size": f"{w}x{h}",
        "scale": 2,
        "maptype": "hybrid",  # 위성+라벨 → 지형 식별 용이
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


async def fetch_bytes(url: str, *, timeout: float = 25.0) -> bytes:
    resp = await aget(url, follow_redirects=True, timeout=timeout)
    resp.raise_for_status()
    return resp.content
