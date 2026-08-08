"""붙여넣은 구글맵 링크 → 카메라 포즈 {lat,lng,pano,heading,pitch,fov}.

세 가지 입력 형태를 하나의 파서로 받는다:
  ① 데스크톱 /@LAT,LNG,3a,75y,H h,T t/data=…!1s<PANO>!2e0…   (비공식·리버스 엔지니어링)
  ② 공식 api=1: /maps/@?api=1&map_action=pano&viewpoint=LAT,LNG&pano=&heading=&pitch=&fov=
  ③ 단축링크 maps.app.goo.gl / goo.gl/maps → 서버측에서 리다이렉트 해제 후 ①/②로 재파싱

주의: ①의 /@…/data= 형식은 Google 이 공식 문서화하지 않았다. tilt(t) 토큰은 pitch==0 이면
사라지므로 선택 그룹으로 두고, 실제 pitch = tilt - 90 으로 변환한다.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

from .tls import aget

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# /@lat,lng,3a,75y,260.89h,78.32t  — 고도/fov/heading/tilt 모두 선택
_AT_RE = re.compile(
    r"@(?P<lat>-?\d+\.\d+),(?P<lng>-?\d+\.\d+)"
    r"(?:,(?P<alt>[\d.]+)a)?"
    r"(?:,(?P<fov>[\d.]+)y)?"
    r"(?:,(?P<heading>-?[\d.]+)h)?"
    r"(?:,(?P<tilt>-?[\d.]+)t)?"
)
_PANO_RE = re.compile(r"!1s(?P<pano>[^!?/]+)")
_Q_RE = re.compile(r"[?&](?:q|query)=(?P<lat>-?\d+\.\d+),(?P<lng>-?\d+\.\d+)")
_PLACE_RE = re.compile(r"/@(?P<lat>-?\d+\.\d+),(?P<lng>-?\d+\.\d+)")
_SHORT_RE = re.compile(r"(maps\.app\.goo\.gl|goo\.gl/maps|g\.co/kgs)", re.I)


class ParseError(ValueError):
    pass


def is_short_link(url: str) -> bool:
    return bool(_SHORT_RE.search(url))


def classify_pano(pano: str | None) -> str:
    if not pano:
        return "none"
    if re.fullmatch(r"[A-Za-z0-9_-]{21}[gwAQ]", pano):
        return "official"
    if pano.startswith("AF1Qip"):
        return "legacy-usergenerated"
    if pano.startswith(("CAoS", "CIHM")):
        return "modern-usergenerated"
    return "unknown"


async def resolve_short_link(url: str, *, timeout: float = 12.0) -> str:
    """maps.app.goo.gl / goo.gl/maps 를 최종 /@ URL 로 확장. 브라우저는 CORS 로 못 하므로 서버 전용."""
    if not is_short_link(url):
        return url
    resp = await aget(
        url, headers={"User-Agent": _UA}, follow_redirects=True, timeout=timeout
    )
    resp.raise_for_status()
    return str(resp.url)


def parse_pose(url: str) -> dict:
    """URL 문자열에서 포즈를 뽑는다(네트워크 없음). 단축링크는 먼저 resolve 후 호출."""
    pose: dict = {
        "lat": None, "lng": None, "pano": None,
        "heading": None, "pitch": 0.0, "fov": None,
    }

    # ② api=1 쿼리 폼
    q = parse_qs(urlparse(url).query)
    if "viewpoint" in q:
        try:
            lat, lng = q["viewpoint"][0].split(",")
            pose["lat"], pose["lng"] = float(lat), float(lng)
        except ValueError:
            pass
    if "pano" in q:
        pose["pano"] = q["pano"][0]
    for key in ("heading", "pitch", "fov"):
        if key in q:
            try:
                pose[key] = float(q[key][0])
            except ValueError:
                pass

    # ① /@ 경로 폼 (좌표/포즈가 있으면 덮어씀)
    m = _AT_RE.search(url)
    if m:
        pose["lat"], pose["lng"] = float(m["lat"]), float(m["lng"])
        if m["fov"]:
            pose["fov"] = float(m["fov"])
        if m["heading"] is not None:
            pose["heading"] = float(m["heading"])
        if m["tilt"] is not None:
            pose["pitch"] = float(m["tilt"]) - 90.0  # URL tilt 90=수평 → SV pitch 0

    # ?q=lat,lng / /place/…/@lat,lng 폴백
    if pose["lat"] is None:
        qm = _Q_RE.search(url) or _PLACE_RE.search(url)
        if qm:
            pose["lat"], pose["lng"] = float(qm["lat"]), float(qm["lng"])

    # data=…!1s<pano>
    if pose["pano"] is None:
        pm = _PANO_RE.search(url)
        if pm:
            pose["pano"] = unquote(pm["pano"])

    if pose["fov"] is None:
        pose["fov"] = 90.0
    if pose["heading"] is None:
        pose["heading"] = 0.0

    if pose["lat"] is None and pose["pano"] is None:
        raise ParseError("URL에서 좌표(@lat,lng)나 파노라마 ID(!1s…)를 찾지 못했습니다.")

    pose["pano_kind"] = classify_pano(pose["pano"])
    return pose


async def extract(url: str) -> dict:
    """붙여넣은 URL 을 정규화 → 리다이렉트 해제 → 포즈 파싱."""
    url = (url or "").strip()
    if not url:
        raise ParseError("URL이 비어 있습니다.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    resolved = await resolve_short_link(url)
    pose = parse_pose(resolved)
    pose["input_url"] = url
    pose["resolved_url"] = resolved
    pose["is_short"] = is_short_link(url)
    return pose
