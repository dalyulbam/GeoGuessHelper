"""붙여넣은 구글맵 링크 → 카메라 포즈 {lat,lng,pano,heading,pitch,fov}.

세 가지 입력 형태를 하나의 파서로 받는다:
  ① 데스크톱 /@LAT,LNG,3a,75y,H h,T t/data=…!1s<PANO>!2e0…   (비공식·리버스 엔지니어링)
  ② 공식 api=1: /maps/@?api=1&map_action=pano&viewpoint=LAT,LNG&pano=&heading=&pitch=&fov=
  ③ 단축링크 maps.app.goo.gl / goo.gl/maps → 서버측에서 리다이렉트 해제 후 ①/②로 재파싱

주의: ①의 /@…/data= 형식은 Google 이 공식 문서화하지 않았다. tilt(t) 토큰은 pitch==0 이면
사라지므로 선택 그룹으로 두고, 실제 pitch = tilt - 90 으로 변환한다.
"""
from __future__ import annotations

import base64
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
# data=…!6s<이미지 URL> — 구글이 공유 링크에 **직접 이미지 URL을 넣어 준다**.
# 사용자 기여(제3자) 파노는 Maps JS API 가 타일을 lh3 CDN 에서 받는데 그 CDN 이
# IP 단위 제한(HTTP 429)을 걸어 검은 화면이 되곤 한다. 그런데 이 URL 은 그냥 받아진다.
# 끝의 `=w900-h600-k-no-pi<pitch>-ya<heading>-ro<roll>-fo<fov>` 로 시점·크기도 지정된다.
_PHOTO_RE = re.compile(r"!6s(?P<url>https?[^!]+)")
# 그 이미지 URL 안의 시점 값. **lh3 자체 좌표계**로 적혀 있어서 지도 URL 의 heading 과 다르다
# (실측: 같은 링크에서 지도 h=36.84 인데 lh3 ya=18.8405 — 약 18° 차이).
# pitch 는 크기가 정확히 일치하고 부호만 반대다(지도 tilt 103.37 → 13.37, lh3 pi=-13.3658).
_PHOTO_YA_RE = re.compile(r"-ya(-?[\d.]+)")
_PHOTO_PI_RE = re.compile(r"-pi(-?[\d.]+)")
_Q_RE = re.compile(r"[?&](?:q|query)=(?P<lat>-?\d+\.\d+),(?P<lng>-?\d+\.\d+)")
_PLACE_RE = re.compile(r"/@(?P<lat>-?\d+\.\d+),(?P<lng>-?\d+\.\d+)")
_SHORT_RE = re.compile(r"(maps\.app\.goo\.gl|goo\.gl/maps|g\.co/kgs)", re.I)


class ParseError(ValueError):
    pass


def is_short_link(url: str) -> bool:
    return bool(_SHORT_RE.search(url))


def classify_pano(pano: str | None) -> str:
    """파노 id → 종류. **접두사 검사를 먼저** 한다.

    예전엔 22자 패턴(`[A-Za-z0-9_-]{21}[gwAQ]`)을 먼저 봤는데, 사용자 기여 파노
    `CIHM0ogKEICAgIDaqcr9Gw` 도 정확히 22자에 끝이 'w' 라 그 패턴에 걸려
    **official 로 오분류**됐다. 접두사가 더 강한 신호이므로 순서를 뒤집었다.
    """
    if not pano:
        return "none"
    if pano.startswith("AF1Qip"):
        return "legacy-usergenerated"
    if pano.startswith(("CAoS", "CIHM", "CIAB")):
        return "modern-usergenerated"
    if re.fullmatch(r"[A-Za-z0-9_-]{21}[gwAQ]", pano):
        return "official"
    return "unknown"


def wrap_pano_id(pano: str | None) -> str | None:
    """사진구체 id 를 Maps JS 가 아는 형식으로 감싼다.

    공유 링크의 `!1s` 에는 `CIHM0ogK…` / `CIABIhC…` 같은 **날것의** 사진구체 id 가 들어 있는데,
    StreetViewService 는 그 형식을 모른다(ID·좌표 조회 모두 ZERO_RESULTS).
    한동안 "지도 SDK 가 제공하지 않는 파노"로 취급했는데, 그게 아니었다 —
    **인코딩만 다른 같은 파노**다.

    실측으로 밝힌 관계:
        base64decode("CAoSFkNJSE0wb2dLRUlDQWdJRHF2YkRmWnc.")
            == bytes [0x08, 0x0A, 0x12, 0x16] + b"CIHM0ogKEICAgIDqvbDfZw"
        (0x16 == 22 == 원래 id 의 길이)
    즉 protobuf 봉투 (필드1=10, 필드2=길이지정 문자열) 안에 원래 id 가 들어 있다.
    그래서 우리가 직접 감싸면 된다 — 검증: CIHM 두 건과 CIAB 한 건 모두 OK 로 해석됐고
    저작권 문자열(© Gerd Rammelsberger / © Radovan Findra / © xbrchx)까지 나왔다.
    """
    if not pano:
        return None
    pid = pano.strip()
    if not pid or len(pid) > 200 or not re.fullmatch(r"[A-Za-z0-9_\-]+", pid):
        return None
    if pid.startswith("CAoS"):      # 이미 감싸인 형식
        return None
    raw = bytes([0x08, 0x0A, 0x12, len(pid)]) + pid.encode("ascii")
    return (base64.b64encode(raw).decode("ascii")
            .replace("+", "-").replace("/", "_").replace("=", "."))


def is_usergenerated(pano: str | None) -> bool:
    return classify_pano(pano).endswith("usergenerated")


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
        "pano_alt": None,    # SDK 가 아는 형식으로 감싼 pano id
        "photo_url": None,   # !6s — 사용자 기여 파노의 직접 이미지 URL(있으면)
        "photo_ya": None,    # 그 URL 의 heading (lh3 좌표계)
        "photo_pi": None,    # 그 URL 의 pitch  (lh3 규약: 양수가 아래)
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

    # data=…!6s<이미지 URL> — 사용자 기여 파노를 직접 받을 수 있는 유일한 실마리다.
    pm = _PHOTO_RE.search(url)
    if pm:
        cand = unquote(pm["url"])
        if re.match(r"^https?://[A-Za-z0-9.\-]*googleusercontent\.com/", cand):
            pose["photo_url"] = cand
            ym = _PHOTO_YA_RE.search(cand)
            pm2 = _PHOTO_PI_RE.search(cand)
            if ym:
                pose["photo_ya"] = float(ym.group(1))
            if pm2:
                pose["photo_pi"] = float(pm2.group(1))

    if pose["fov"] is None:
        pose["fov"] = 90.0
    if pose["heading"] is None:
        pose["heading"] = 0.0

    if pose["lat"] is None and pose["pano"] is None:
        raise ParseError("URL에서 좌표(@lat,lng)나 파노라마 ID(!1s…)를 찾지 못했습니다.")

    # lh3 좌표계 ↔ 진북 heading 의 차이. 이 값이 있어야 사진구체를 **사용자가 보던 방향**으로
    # 띄우고, 캡처도 같은 방향을 찍는다. 없으면 0 으로 두어 예전과 같이 동작한다.
    if pose.get("photo_ya") is not None and pose.get("heading") is not None:
        # (-180, 180] 로 접는다. 0 근처인데 359.998 로 적히면 사람도 코드도 헷갈린다.
        _d = (float(pose["heading"]) - float(pose["photo_ya"])) % 360.0
        pose["ya_offset"] = round(_d - 360.0 if _d > 180.0 else _d, 4)
    else:
        pose["ya_offset"] = 0.0

    pose["pano_kind"] = classify_pano(pose["pano"])
    # 날것의 사진구체 id 는 SDK 가 모른다. 감싼 형식을 함께 넘겨 두면
    # 뷰어가 원본 실패 시 이것으로 재시도할 수 있다.
    pose["pano_alt"] = wrap_pano_id(pose["pano"])
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
