"""분석 결과 + 캡처 이미지 → docs/report/*.html 리포트(장면 앨범).

캡처 이미지는 base64 로 임베드해 자기완결형(single-file) HTML 로 저장한다 —
서버가 꺼져도 열리고, 개인 복기용으로 로컬 보관. (Google 로고/귀속은 캡처에 그대로 유지)

언어 여러 개를 고르면 build_combined_report() 로 **한 파일 안에** 언어별 섹션 + 상단 언어
스위처를 담는다. 이미지 base64 는 공유 CSS 클래스로 **한 번만** 임베드(파일 비대화 방지),
JS 없이도 섹션이 세로로 쌓여 읽히도록 점진적 향상(progressive enhancement)으로 만든다.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import html
import os
import re
import uuid
from pathlib import Path

from .config import Settings

_MAX_NAME_TRIES = 200


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _wt(v) -> str:
    """cues[].weight 를 표시용 문자열로 정규화한다.

    스키마는 "high|medium|low" 문자열을 요구하지만 값을 만드는 것은 모델이라
    0.7 같은 숫자가 오기도 한다. 예전엔 `(v or "").lower()` 라서 그 순간
    AttributeError 로 **보고서 생성이 통째로 실패**했다. 한 칸의 표기 문제로
    문서 전체를 잃는 것은 균형이 맞지 않는다.
    """
    if isinstance(v, str):
        return v.strip().lower()
    if isinstance(v, (int, float)):
        return "high" if v >= 0.66 else ("medium" if v >= 0.33 else "low")
    return ""


# ISO 3166-1 alpha-3 → alpha-2. 스키마로 alpha-2 를 요구하지만 값을 만드는 것은 모델이라
# 섞여 들어온다 — 실제로 같은 크로아티아 보고서가 `report_hr_…` 과 `report_hrv_…` 로 갈려
# 파일 목록에서 서로 떨어져 정렬됐다. 앞 두 글자를 자르는 방법은 쓸 수 없다
# (CHN→CH 가 되어 스위스 CHE→CH 와 충돌한다). 그래서 표로 옮긴다.
_A3 = {
    "AFG": "AF", "ALB": "AL", "DZA": "DZ", "AND": "AD", "AGO": "AO", "ARG": "AR", "ARM": "AM",
    "AUS": "AU", "AUT": "AT", "AZE": "AZ", "BHS": "BS", "BHR": "BH", "BGD": "BD", "BLR": "BY",
    "BEL": "BE", "BEN": "BJ", "BTN": "BT", "BOL": "BO", "BIH": "BA", "BWA": "BW", "BRA": "BR",
    "BGR": "BG", "BFA": "BF", "KHM": "KH", "CMR": "CM", "CAN": "CA", "CHL": "CL", "CHN": "CN",
    "COL": "CO", "CRI": "CR", "HRV": "HR", "CUB": "CU", "CYP": "CY", "CZE": "CZ", "DNK": "DK",
    "DOM": "DO", "ECU": "EC", "EGY": "EG", "SLV": "SV", "EST": "EE", "ETH": "ET", "FIN": "FI",
    "FRA": "FR", "GEO": "GE", "DEU": "DE", "GHA": "GH", "GRC": "GR", "GTM": "GT", "HND": "HN",
    "HUN": "HU", "ISL": "IS", "IND": "IN", "IDN": "ID", "IRN": "IR", "IRQ": "IQ", "IRL": "IE",
    "ISR": "IL", "ITA": "IT", "JAM": "JM", "JPN": "JP", "JOR": "JO", "KAZ": "KZ", "KEN": "KE",
    "KOR": "KR", "PRK": "KP", "KWT": "KW", "KGZ": "KG", "LAO": "LA", "LVA": "LV", "LBN": "LB",
    "LBY": "LY", "LTU": "LT", "LUX": "LU", "MYS": "MY", "MLT": "MT", "MEX": "MX", "MDA": "MD",
    "MNG": "MN", "MNE": "ME", "MAR": "MA", "MOZ": "MZ", "MMR": "MM", "NAM": "NA", "NPL": "NP",
    "NLD": "NL", "NZL": "NZ", "NIC": "NI", "NGA": "NG", "MKD": "MK", "NOR": "NO", "OMN": "OM",
    "PAK": "PK", "PAN": "PA", "PRY": "PY", "PER": "PE", "PHL": "PH", "POL": "PL", "PRT": "PT",
    "QAT": "QA", "ROU": "RO", "RUS": "RU", "SAU": "SA", "SEN": "SN", "SRB": "RS", "SGP": "SG",
    "SVK": "SK", "SVN": "SI", "ZAF": "ZA", "ESP": "ES", "LKA": "LK", "SWE": "SE", "CHE": "CH",
    "SYR": "SY", "TWN": "TW", "TZA": "TZ", "THA": "TH", "TUN": "TN", "TUR": "TR", "UGA": "UG",
    "UKR": "UA", "ARE": "AE", "GBR": "GB", "USA": "US", "URY": "UY", "UZB": "UZ", "VEN": "VE",
    "VNM": "VN", "YEM": "YE", "ZMB": "ZM", "ZWE": "ZW",
}


def _country_tag(iso: str, country: str) -> str:
    """파일명의 국가 조각.

    예전에는 `_slug(iso or country)` 였는데, country_iso 가 비고 국가명이 비ASCII 면
    (예: "대한민국") 슬러그가 통째로 날아가 'location' 이 되었다 — 파일명이 대상을 전혀
    식별하지 못했고, 충돌 조건이 '같은 장소'에서 '같은 초'로 넓어졌다.
    슬러그가 무의미해지면 국가명 해시로라도 서로 다른 나라를 구분한다.
    """
    code = (iso or "").strip().upper()
    code = _A3.get(code, code)          # alpha-3 이면 alpha-2 로 접는다
    s = _slug(code or country)
    if s and s != "location":
        return s
    if country:
        return "c" + hashlib.sha1(country.encode("utf-8")).hexdigest()[:6]
    return "unknown"


def _write_report_atomic(reports_dir: Path, fname: str, text: str) -> str:
    """이름을 원자적으로 선점하고 내용을 원자적으로 채운다. 최종 파일명을 반환.

    고치는 것 두 가지:
      1. 덮어쓰기 — `out.write_text()` 는 존재 확인이 없어서, 같은 초에 끝난 두 보고서가
         같은 이름을 만들면 먼저 것이 조용히 사라졌다(UI 에는 2건이 남아 같은 URL 을 가리킴).
         → O_CREAT|O_EXCL 로 이름을 선점하고, 이미 있으면 -2, -3 … 을 붙인다.
      2. 잘린 파일 — write_text 는 truncate 후 기록이라, 그 사이에 GET /reports/{name} 이
         들어오면 빈/잘린 HTML 이 200 으로 나갔다.
         → 임시 파일에 다 쓴 뒤 os.replace 로 교체한다(교체는 원자적).
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem, suffix = fname[: -len(".html")], ".html"
    tmp = reports_dir / f".{stem}.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(text, encoding="utf-8")
    try:
        for i in range(1, _MAX_NAME_TRIES + 1):
            candidate = f"{stem}{suffix}" if i == 1 else f"{stem}-{i}{suffix}"
            target = reports_dir / candidate
            try:
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            os.close(fd)
            os.replace(tmp, target)          # 원자적 — 독자는 빈 파일이나 완성본만 본다
            return candidate
        # 200개까지 같은 이름이면 uuid 로 확실히 유일하게
        candidate = f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
        os.replace(tmp, reports_dir / candidate)
        return candidate
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").lower()).strip("-")
    return s or "location"


def _safe_url(u) -> str:
    """http(s) URL 만 허용(javascript:/data: 등 실행 스킴 차단). 아니면 빈 문자열."""
    u = str(u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else ""


def _data_uri(path: Path) -> str | None:
    if not path.exists():
        return None
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    b64 = base64.standard_b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _img_ratio(path: Path) -> float | None:
    """이미지 가로/세로 비율(Pillow 있으면). combined 리포트의 CSS aspect-ratio 용."""
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            w, h = im.size
            if w and h:
                return round(w / h, 4)
    except Exception:  # noqa: BLE001  (Pillow 미설치/손상 이미지 등)
        pass
    return None


def _pct(x) -> int:
    try:
        return max(0, min(100, round(float(x) * 100)))
    except (TypeError, ValueError):
        return 0


def _num(x, nd: int = 5):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return x


def _coord_tag(lat, lng) -> str:
    """시작 좌표 → 파일명 조각. 숫자 아니면 빈 문자열. (점·마이너스는 Windows 파일명 허용)"""
    try:
        la = round(float(lat), 4)
        lo = round(float(lng), 4)
    except (TypeError, ValueError):
        return ""
    return f"{la}_{lo}"


def _report_filename(iso: str, country: str, place_slug: str,
                     start_lat, start_lng, lang_tag: str, ymd: str, hms: str) -> str:
    """report_{iso}_{도시/랜드마크}_{시작위도}_{시작경도}_{yymmdd}_{hhmmss}_{언어}.html"""
    parts = ["report", _country_tag(iso, country)]
    ps = _slug(place_slug) if place_slug else ""
    if ps and ps != "location":
        parts.append(ps)
    coord = _coord_tag(start_lat, start_lng)
    if coord:
        parts.append(coord)
    parts += [ymd, hms]
    if lang_tag:
        parts.append(lang_tag)
    return "_".join(parts) + ".html"


# ── 이미지 임베드 ────────────────────────────────────────────────
def _embed_images(files: list[str], settings: Settings) -> list[dict]:
    """[{name, uri, ratio}] — 존재하는 캡처만. uri = data:...;base64,..."""
    out: list[dict] = []
    for fn in files or []:
        path = settings.captures_dir / Path(fn).name
        uri = _data_uri(path)
        if uri:
            out.append({"name": path.name, "uri": uri, "ratio": _img_ratio(path)})
    return out


def _gallery_html(imgs: list[dict], S: dict, classes: list[str] | None = None) -> str:
    """캡처 갤러리. classes=None → <img src>(단일 리포트); classes 주면 CSS 클래스 참조
    (combined 리포트 — base64 는 <style> 에 한 번만, 여기선 클래스만 참조)."""
    if not imgs:
        return f'<p class="muted">{_esc(S["no_embed"])}</p>'
    cells = []
    for i, im in enumerate(imgs):
        name = im["name"]
        if classes is None:
            cells.append(
                f'<figure><img src="{im["uri"]}" alt="{_esc(name)}">'
                f'<figcaption>{_esc(name)}</figcaption></figure>'
            )
        else:
            cells.append(
                f'<figure><div class="capimg {classes[i]}" role="img" aria-label="{_esc(name)}"></div>'
                f'<figcaption>{_esc(name)}</figcaption></figure>'
            )
    return f'<div class="gallery">{"".join(cells)}</div>'


def _locator_maps(lat, lng, settings: Settings) -> list[dict]:
    """좌표 주변을 줌 단계별로 담은 정적 지도 — [{label, uri, zoom, maptype}].

    "이 보고서가 어디 이야기인가"를 한눈에 잡아주는 축척 사다리다:
    광역 행정구역 → 도시권 → 시가 → 블록. 캡처와 같은 방식으로 base64 임베드하므로
    서버가 꺼져도, 인터넷이 없어도 열린다.

    실패(키 없음·네트워크·쿼터)는 전부 soft — 지도 없이 보고서는 그대로 나간다.
    """
    if not settings.report_maps:
        return []
    key = settings.effective_static_key
    if not key:
        return []
    try:
        pose = {"lat": float(lat), "lng": float(lng)}
    except (TypeError, ValueError):
        return []

    from . import streetview

    levels = settings.report_map_levels
    blobs: list[bytes] = []
    # 1) Static Maps API — 빠르고 싸다. 단, 서버측 GET 이라 **리퍼러 제한이 걸린 키로는 403**.
    for lv in levels:
        try:
            url = streetview.staticmap_url(
                pose, key,
                w=settings.report_map_w, h=settings.report_map_h,
                zoom=int(lv["zoom"]), maptype=str(lv.get("maptype") or "hybrid"),
            )
            blobs.append(streetview.fetch_bytes_sync(url))
        except Exception:  # noqa: BLE001
            blobs.append(b"")
    # 2) 전부 실패했으면 브라우저 렌더로 폴백 — 캡처가 이미 쓰는 경로와 같다.
    #    (GOOGLE_MAPS_STATIC_KEY 를 따로 두면 이 폴백은 타지 않는다)
    if settings.report_maps_fallback and not any(b for b in blobs):
        js_key = settings.js_api_key or key
        if js_key:
            from . import render_google

            try:
                blobs = render_google.render_locator_maps(pose["lat"], pose["lng"], settings, js_key)
            except Exception:  # noqa: BLE001
                blobs = []

    out: list[dict] = []
    for lv, data in zip(levels, blobs or []):
        if not data or len(data) < 1000:
            continue
        mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        out.append({
            "label": lv.get("label") or f"z{lv['zoom']}",
            "zoom": int(lv["zoom"]),
            "maptype": lv.get("maptype"),
            "uri": f"data:{mime};base64,{base64.standard_b64encode(data).decode()}",
        })
    return out


def _maps_html(maps: list[dict], S: dict, lat, lng, classes: list[str] | None = None) -> str:
    """위치 지도 컨테이너. classes 를 주면 base64 를 CSS 로 빼서 한 번만 싣는다(통합 리포트)."""
    if not maps:
        return ""
    cells = []
    for i, m in enumerate(maps):
        cap = _esc(S.get(m["label"], m["label"]))
        z = f'<span class="mz">z{m["zoom"]}</span>'
        inner = (
            f'<img src="{m["uri"]}" alt="{cap}" loading="lazy">'
            if classes is None
            else f'<div class="mimg {classes[i]}" role="img" aria-label="{cap}"></div>'
        )
        cells.append(f'<figure class="mcell">{inner}<figcaption>{cap} {z}</figcaption></figure>')
    link = ""
    try:
        la, lo = round(float(lat), 6), round(float(lng), 6)
        link = (f'<a class="mlink" href="https://www.google.com/maps/@{la},{lo},14z" '
                f'target="_blank" rel="noopener">{_esc(S["maps_open"])} ↗</a>')
    except (TypeError, ValueError):
        pass
    return (
        f'<div class="maps-wrap">'
        f'<div class="maps-h">{_esc(S["maps_h"])} '
        f'<span class="muted">{_esc(S["maps_sub"])}</span>{link}</div>'
        f'<div class="maps">{"".join(cells)}</div>'
        f'</div>'
    )


def _script_html(script: dict | None, S: dict) -> str:
    """스크립트 서술 — 표와 사슬 위에 얹는 '읽는 글'.

    수치·enum 은 여전히 아래 구조화 섹션이 담당한다. 여기는 그것을 사람이 읽는 순서로
    풀어 쓴 것이고, 재료에 없는 주장은 검증 단계에서 걸러진 뒤다.
    """
    if not script or not script.get("sections"):
        return ""
    lede = (script.get("lede") or "").strip()
    note = (script.get("confidence_note") or "").strip()
    meta = script.get("_meta") or {}

    def linkify(t: str) -> str:
        return re.sub(
            r"\[\[(atm_[0-9a-f]{6,})\]\]",
            lambda m: f'<a class="katom" href="../knowledge/atoms/{m.group(1)}.md"><code>{m.group(1)}</code></a>',
            _esc(t),
        )

    secs = "".join(
        f'<section class="sx"><h3>{_esc(s.get("heading") or "")}</h3>'
        f'<div class="sxb">{linkify(s.get("body") or "")}</div></section>'
        for s in script["sections"] if isinstance(s, dict) and (s.get("body") or "").strip()
    )
    if not secs:
        return ""
    by = ""
    if meta.get("draftedBy"):
        by = (f'<div class="sxby">{_esc(S["script_by"])}: {_esc(meta.get("draftedBy"))}'
              + (f' → {_esc(meta.get("verifiedBy"))}' if meta.get("verifiedBy") else "")
              + "</div>")
    return (
        f'<h2>{_esc(S["script_h"])} <span class="muted">{_esc(S["script_sub"])}</span></h2>'
        + (f'<div class="pf-lead">{linkify(lede)}</div>' if lede else "")
        + f'<div class="sxwrap">{secs}</div>'
        + (f'<div class="pf-note">{_esc(note)}</div>' if note else "")
        + by
    )


def _knowledge_html(atoms, S: dict) -> str:
    """재사용한 지식 원자 — 본문을 복제하지 않고 저장소의 .md 로 **참조**한다.

    보고서가 쌓일수록 같은 문장을 반복해 싣는 대신 같은 주소를 가리키게 되고, 원자는
    한 곳에서만 갱신된다.
    """
    if not atoms:
        return ""
    rows = []
    for a in atoms:
        aid = a.get("id") if isinstance(a, dict) else getattr(a, "id", "")
        title = a.get("title") if isinstance(a, dict) else getattr(a, "title", "")
        layer = a.get("layer") if isinstance(a, dict) else getattr(a, "layer", "")
        scope = a.get("scope") if isinstance(a, dict) else getattr(a, "scope", "")
        tags = a.get("tags") if isinstance(a, dict) else getattr(a, "tags", [])
        if not aid:
            continue
        chips = "".join(f'<span class="ktag">#{_esc(t)}</span>' for t in (tags or [])[:6])
        rows.append(
            f'<li><a class="katom" href="../knowledge/atoms/{_esc(aid)}.md">'
            f'<code>{_esc(aid)}</code></a> <b>{_esc(title)}</b>'
            f' <span class="muted">{_esc(layer)}/{_esc(scope)}</span><br>{chips}</li>'
        )
    if not rows:
        return ""
    return (
        f'<h2>{_esc(S["k_h"])} <span class="muted">{_esc(S["k_sub"])}</span></h2>'
        f'<div class="pf-note">{_esc(S["k_note"])}</div>'
        f'<ul class="klist">{"".join(rows)}</ul>'
    )


# ── 언어별 본문(내부 HTML) 조립 ──────────────────────────────────
def _compute_body(result: dict, lang: str, research: dict | None,
                  gallery_html: str, n_imgs: int, stamp: str,
                  knowledge: list | None = None, dropped: int = 0,
                  maps_html: str = "", script: dict | None = None) -> dict:
    """한 언어의 리포트 본문(.wrap 내부 HTML) + 파일명 메타를 만든다.

    반환 {inner, iso, country, city, place_slug, title}.
    """
    from . import i18n

    S = i18n.report_strings(lang)
    result = result or {}
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else result
    analysis = analysis or {}

    g = analysis.get("best_guess") or {}
    cf = analysis.get("coarse_filters") or {}
    cues = analysis.get("cues") or []
    langs = analysis.get("languages_detected") or []
    landmarks = [lm for lm in (analysis.get("landmarks") or []) if lm.get("name")]
    alts = analysis.get("alternatives") or []
    read = analysis.get("cultural_economic_read") or ""

    conf = _pct(g.get("confidence"))
    country = g.get("country") or S["unknown"]
    iso = g.get("country_iso") or ""
    city = g.get("city") or ""
    place_slug = analysis.get("place_slug") or ""
    region = " · ".join([x for x in [g.get("region_or_state"), g.get("city")] if x])
    ce = g.get("coordinate_estimate") or {}
    title = f"{country}{f' ({iso})' if iso else ''}"

    model = result.get("model") or "-"
    cost = result.get("cost_usd")

    def chips():
        vals = [
            cf.get("driving_side")
            and S["driving_tpl"].format(side=i18n.enum_label(lang, "driving_side", cf.get("driving_side"))),
            cf.get("hemisphere")
            and S["hemi_tpl"].format(hemi=i18n.enum_label(lang, "hemisphere", cf.get("hemisphere"))),
            cf.get("approx_climate_biome"),
        ]
        vals = [v for v in vals if v]
        if not vals:
            return ""
        return '<div class="chips">' + "".join(f'<span class="c">{_esc(v)}</span>' for v in vals) + "</div>"

    def narrowing_block():
        """판별 사슬 — '왜 이 나라인가'가 아니라 '거기서 어떻게 더 좁혔는가'."""
        steps = [s for s in (analysis.get("narrowing") or []) if isinstance(s, dict)]
        if not steps:
            return ""
        rows = []
        for i, st in enumerate(steps):
            lvl = str(st.get("level") or "")
            lvl_label = i18n.enum_label(lang, "level", lvl)
            conf = st.get("confidence_after")
            conf_pct = _pct(conf) if isinstance(conf, (int, float)) else None
            cands = [c for c in (st.get("candidates") or []) if c]
            out = [c for c in (st.get("ruled_out") or []) if c]
            wt = _wt(st.get("weight"))

            cand_html = ""
            if cands:
                cand_html = (f'<div class="nrow"><span class="nk">{_esc(S["narrow_cand"])}</span>'
                             f'<span class="nv">'
                             + " ".join(f'<span class="ncand">{_esc(c)}</span>' for c in cands[:8])
                             + "</span></div>")
            out_html = (
                f'<div class="nrow"><span class="nk">{_esc(S["narrow_out"])}</span><span class="nv">'
                + "".join(f'<span class="nout">{_esc(c)}</span>' for c in out[:8]) + "</span></div>"
                if out else
                f'<div class="nrow nocut"><span class="nk">{_esc(S["narrow_out"])}</span>'
                f'<span class="nv">{_esc(S["narrow_nocut"])}</span></div>'
            )
            rows.append(
                f'<li class="nstep{" weak" if not out else ""}">'
                f'<div class="nhead">'
                f'<span class="nlvl l-{_esc(lvl)}">{_esc(lvl_label)}</span>'
                f'<span class="nq">{_esc(st.get("question") or "")}</span>'
                + (f'<span class="nconf">{conf_pct}%</span>' if conf_pct is not None else "")
                + (f'<span class="nw w-{_esc(wt)}">{_esc(i18n.enum_label(lang, "weight", wt))}</span>' if wt else "")
                + "</div>"
                + cand_html
                + f'<div class="nrow"><span class="nk">{_esc(S["narrow_obs"])}</span>'
                  f'<span class="nv">{_esc(st.get("observation") or "")}</span></div>'
                + (f'<div class="nrow"><span class="nk">{_esc(S["narrow_disc"])}</span>'
                   f'<span class="nv em">{_esc(st.get("discriminator") or "")}</span></div>'
                   if st.get("discriminator") else "")
                + out_html
                + f'<div class="nrow in"><span class="nk">{_esc(S["narrow_in"])}</span>'
                  f'<span class="nv"><b>{_esc(st.get("conclusion") or "")}</b></span></div>'
                + "</li>"
            )
        return (
            f'<h2>{_esc(S["narrow_h"])} <span class="muted">{_esc(S["narrow_sub"])}</span></h2>'
            f'<div class="pf-note">{_esc(S["narrow_note"])}</div>'
            f'<ol class="narrow">{"".join(rows)}</ol>'
        )

    def cues_table():
        if not cues:
            return ""
        rows = "".join(
            f"<tr><td>{_esc(c.get('category'))}</td>"
            f"<td>{_esc(c.get('observation'))}"
            + (f' <span class="muted">[{_esc(",".join(c.get("supports") or []))}]</span>' if c.get("supports") else "")
            + f'</td><td class="w-{_esc(_wt(c.get("weight")))}">'
            + f"{_esc(i18n.enum_label(lang, 'weight', _wt(c.get('weight'))))}</td></tr>"
            for c in cues
        )
        return (
            f'<h2>{_esc(S["cues_h"])} <span class="muted">{_esc(S["cues_sub"])}</span></h2>'
            f'<table><thead><tr><th>{_esc(S["col_category"])}</th><th>{_esc(S["col_observation"])}</th>'
            f'<th>{_esc(S["col_weight"])}</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
        )

    def langs_block():
        if not langs:
            return ""
        rows = "".join(
            f"<tr><td>{_esc(l.get('language'))}"
            + (f' <span class="muted">({_esc(l.get("script"))})</span>' if l.get("script") else "")
            + f"</td><td>{_esc(l.get('evidence'))}</td><td>{_pct(l.get('confidence'))}%</td></tr>"
            for l in langs
        )
        return (
            f'<h2>{_esc(S["langs_h"])}</h2>'
            f'<table><thead><tr><th>{_esc(S["col_language"])}</th><th>{_esc(S["col_evidence"])}</th>'
            f'<th>{_esc(S["col_conf"])}</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
        )

    def landmarks_block():
        if not landmarks:
            return ""
        items = "".join(
            f"<li><b>{_esc(lm.get('name'))}</b> <span class='muted'>{_esc(lm.get('type'))}</span> · {_pct(lm.get('confidence'))}%</li>"
            for lm in landmarks
        )
        return f"<h2>{_esc(S['landmarks_h'])}</h2><ul class='clean'>{items}</ul>"

    def alts_block():
        if not alts:
            return ""
        items = " · ".join(
            f"{_esc(a.get('country'))} {_pct(a.get('confidence'))}%" for a in alts
        )
        return f'<p class="alt"><b>{_esc(S["alts_label"])}</b> {items}</p>'

    def coord_block():
        if not ce:
            return ""
        return (
            f'<div class="coord">{_esc(S["coord_prefix"])} {_esc(_num(ce.get("lat")))}, {_esc(_num(ce.get("lng")))}'
            f' (±{_esc(ce.get("radius_km"))}km)</div>'
        )

    def research_block():
        prof = (research or {}).get("profile") if isinstance(research, dict) else None
        if not prof:
            return ""
        sources = (research or {}).get("sources") or []

        def para(label_key, field):
            v = prof.get(field)
            if not v or not str(v).strip():
                return ""
            return (f'<div class="pf"><div class="pf-h">{_esc(S[label_key])}</div>'
                    f'<div class="pf-b">{_esc(v)}</div></div>')

        def list_block(label_key, field):
            items = [x for x in (prof.get(field) or []) if x and str(x).strip()]
            if not items:
                return ""
            lis = "".join(f"<li>{_esc(x)}</li>" for x in items)
            return (f'<div class="pf"><div class="pf-h">{_esc(S[label_key])}</div>'
                    f'<ul class="pf-l">{lis}</ul></div>')

        def people_block():
            ppl = [p for p in (prof.get("notable_people") or []) if isinstance(p, dict) and p.get("name")]
            if not ppl:
                return ""
            lis = "".join(
                f"<li><b>{_esc(p.get('name'))}</b>"
                + (f" — {_esc(p.get('note'))}" if p.get("note") else "")
                + "</li>"
                for p in ppl
            )
            return (f'<div class="pf"><div class="pf-h">{_esc(S["r_people"])}</div>'
                    f'<ul class="pf-l">{lis}</ul></div>')

        lead = (f'<p class="pf-lead">{_esc(prof.get("summary"))}</p>'
                if prof.get("summary") and str(prof.get("summary")).strip() else "")
        body = "".join([
            para("r_economy", "economy"),
            para("r_industry", "industry"),
            para("r_culture", "culture"),
            para("r_tourism", "tourism"),
            people_block(),
            para("r_neighbor", "neighbor_comparison"),
            list_block("r_pros", "pros"),
            list_block("r_cons", "cons"),
            para("r_residents", "residents"),
        ])
        src = ""
        if sources:
            safe = [(u, s.get("title") or u) for s in sources[:20] if (u := _safe_url(s.get("url")))]
            lis = "".join(
                f'<li><a href="{_esc(u)}" target="_blank" rel="noopener nofollow">{_esc(t)}</a></li>'
                for u, t in safe
            )
            if lis:
                src = (f'<div class="pf-src"><div class="pf-h">{_esc(S["r_sources"])} '
                       f'<span class="muted">({len(safe)})</span></div>'
                       f'<ul class="pf-srcl">{lis}</ul></div>')
        if not (lead or body):
            return ""
        return (
            f'<h2>{_esc(S["r_profile_h"])} <span class="muted">{_esc(S["r_profile_sub"])}</span></h2>'
            f'<div class="pf-note">{_esc(S["r_note"])}</div>'
            f'{lead}<div class="pf-grid">{body}</div>{src}'
        )

    culture = ("<h2>" + _esc(S["culture_h"]) + "</h2><div class='read'>" + _esc(read) + "</div>") if read else ""
    cost_chip = ("<span class='chip'>" + _esc(S["meta_cost"]) + _esc(cost) + "</span>") if cost is not None else ""
    # 어떤 모델이 초안을 쓰고 어떤 모델이 검증했는지 — 라우팅이 실제로 무엇을 했는지 남긴다.
    _sm = (script or {}).get("_meta") or {}
    script_chip = (
        "<span class='chip'>" + _esc(_sm.get("draftedBy")) + " → " + _esc(_sm.get("verifiedBy") or "-") + "</span>"
        if _sm.get("draftedBy") else ""
    )

    inner = (
        f'<div class="kicker">GeoGuessHelper {_esc(S["report_word"])} · {_esc(stamp)}</div>'
        f'<div class="hero">'
        f'<div class="country">{_esc(title)}</div>'
        f'<div class="region">{_esc(region)}</div>'
        f'<div class="confbar"><i style="width:{conf}%"></i></div>'
        f'<div class="conf-label">{_esc(S["conf_label"].format(conf=conf))}</div>'
        f'{coord_block()}{chips()}'
        f'</div>'
        f'{_tr_warning(result, S)}'   # 번역 실패를 **독자에게** 알린다(조용히 영어로 두지 않는다)
        f'{maps_html}'          # hero 바로 아래 — 축척 사다리로 위치를 먼저 잡아준다
        f'{_script_html(script, S)}'   # 사람이 읽는 서술 — 표·사슬보다 먼저
        f'<h2>{_esc(S["scenes_h"])} <span class="muted">{_esc(S["scenes_meta"].format(n=n_imgs))}</span></h2>'
        + (f'<div class="pf-note">{_esc(S["scenes_dropped"].format(n=dropped))}</div>' if dropped else "")
        + f'{gallery_html}'
        f'{narrowing_block()}{cues_table()}{langs_block()}{landmarks_block()}'
        f'{culture}'
        f'{research_block()}{_knowledge_html(knowledge, S)}{alts_block()}'
        f'<div class="meta">'
        f'<span class="chip">{_esc(S["meta_model"])} {_esc(model)}</span>'
        f'{cost_chip}{script_chip}'
        f'<span class="chip">{_esc(S["meta_images"].format(n=n_imgs))}</span>'
        f'</div>'
        f'<div class="foot">{S["foot"]}</div>'
    )
    return {"inner": inner, "iso": iso, "country": country, "city": city,
            "place_slug": place_slug, "title": title}


_BASE_CSS = """
/* 번역 실패 같은 '독자가 반드시 알아야 하는' 알림. 조용한 폴백을 눈에 보이게 만든다. */
.pf-note.warn{background:#fbf3dd;border-left:3px solid #9a6b00;color:#6b4a00;font-weight:600}

:root{--accent:hsl(168 74% 37%);--accent-deep:hsl(168 78% 27%);--accent-soft:hsl(168 58% 95%);
--accent2:hsl(38 92% 50%);--ink:#0e1a17;--ink-2:#2f4a43;--muted:#7c918a;--line:#e3ece9;--bg:#f4f7f6;--card:#fff;
--ok:#127a4b;--warn:#9a6b00;--shadow:0 1px 2px rgba(14,26,23,.05),0 10px 30px -16px rgba(14,26,23,.2)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Pretendard','Malgun Gothic',system-ui,sans-serif;color:var(--ink);background:var(--bg);
line-height:1.7;-webkit-font-smoothing:antialiased;letter-spacing:-.01em}
.wrap{max-width:940px;margin:0 auto;padding:28px 22px 60px}
.kicker{font-size:12px;font-weight:800;letter-spacing:.15em;color:var(--accent-deep);text-transform:uppercase;margin-bottom:12px}
.hero{background:linear-gradient(135deg,var(--accent-soft),#fff);border:1px solid #c4e8df;border-radius:18px;padding:24px;box-shadow:var(--shadow)}
.country{font-size:34px;font-weight:800;letter-spacing:-.03em;color:var(--accent-deep)}
.region{font-size:15px;color:var(--ink-2);margin-top:2px}
.confbar{height:10px;border-radius:99px;background:#dbeee8;margin-top:14px;overflow:hidden;max-width:420px}
.confbar>i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent-deep))}
.conf-label{font-size:12.5px;color:var(--muted);margin-top:5px;font-weight:600}
.coord{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;color:var(--ink-2);margin-top:8px}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}
.chips .c{font-size:12.5px;font-weight:700;padding:4px 11px;border-radius:999px;background:#fff;color:var(--ink-2);border:1px solid var(--line)}
h2{font-size:18px;font-weight:800;letter-spacing:-.02em;margin:30px 0 10px;display:flex;align-items:baseline;gap:8px}
h2 .muted{font-size:12px;font-weight:600}
.muted{color:var(--muted)}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-top:8px}
figure{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
figure img{width:100%;display:block}
figcaption{font-size:11px;color:var(--muted);padding:7px 10px;font-family:ui-monospace,Consolas,monospace}
table{width:100%;border-collapse:collapse;font-size:13.5px;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-top:6px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f7faf9;font-weight:700;font-size:12px;color:var(--ink-2)}
tr:last-child td{border-bottom:none}
.w-high{color:var(--ok);font-weight:700} .w-medium{color:var(--warn);font-weight:700} .w-low,.w-none{color:var(--muted)}
.read{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;font-size:14px;color:var(--ink-2);margin-top:8px;box-shadow:var(--shadow)}
.alt{font-size:13px;color:var(--ink-2);margin-top:12px}
ul.clean{list-style:none;padding:0;margin-top:6px} ul.clean li{padding:6px 0;border-bottom:1px dashed var(--line);font-size:14px;color:var(--ink-2)}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px;font-size:12px;color:var(--muted)}
.meta .chip{background:#fff;border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-weight:600}
.foot{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);font-size:11.5px;color:var(--muted);line-height:1.6}
.pf-note{font-size:11.5px;color:var(--muted);background:#fff;border:1px dashed var(--line);border-radius:8px;padding:6px 10px;margin:2px 0 12px}
.pf-lead{font-size:14.5px;color:var(--ink-2);background:linear-gradient(135deg,var(--accent-soft),#fff);border:1px solid #c4e8df;border-radius:12px;padding:14px 16px;margin-bottom:12px}
.pf-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:640px){.pf-grid{grid-template-columns:1fr}}
.pf{background:#fff;border:1px solid var(--line);border-radius:12px;padding:13px 15px;box-shadow:var(--shadow)}
.pf-h{font-size:12.5px;font-weight:800;color:var(--accent-deep);letter-spacing:-.01em;margin-bottom:5px;text-transform:uppercase}
.pf-b{font-size:13.5px;color:var(--ink-2)}
.pf-l{list-style:none;padding:0;margin:0} .pf-l li{font-size:13.5px;color:var(--ink-2);padding:3px 0 3px 15px;position:relative}
.pf-l li::before{content:"·";position:absolute;left:3px;color:var(--accent);font-weight:800}
.pf-src{margin-top:14px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:13px 15px}
.pf-srcl{list-style:none;padding:0;margin:6px 0 0;columns:2;column-gap:20px}
@media(max-width:640px){.pf-srcl{columns:1}}
.pf-srcl li{font-size:11.5px;margin-bottom:4px;break-inside:avoid}
.pf-srcl a{color:var(--accent-deep);text-decoration:none} .pf-srcl a:hover{text-decoration:underline}
.klist{list-style:none;padding:0;margin:8px 0 0;display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:640px){.klist{grid-template-columns:1fr}}
.klist li{background:#fff;border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:10px;padding:10px 12px;font-size:13px;color:var(--ink-2);box-shadow:var(--shadow)}
.katom code{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--accent-deep)}
.katom{text-decoration:none} .katom:hover code{text-decoration:underline}
.ktag{display:inline-block;font-size:10.5px;color:var(--muted);background:#f4f7f6;
border-radius:99px;padding:1px 7px;margin:3px 3px 0 0}
.maps-wrap{margin-top:18px}
.maps-h{font-size:13px;font-weight:800;color:var(--accent-deep);display:flex;align-items:baseline;
gap:8px;margin-bottom:8px;flex-wrap:wrap}
.maps-h .muted{font-size:11.5px;font-weight:600}
.mlink{margin-left:auto;font-size:11.5px;font-weight:700;color:var(--accent-deep);text-decoration:none;
background:#fff;border:1px solid var(--line);border-radius:8px;padding:3px 10px}
.mlink:hover{border-color:var(--accent)}
.maps{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
@media(max-width:900px){.maps{grid-template-columns:repeat(2,1fr)}}
@media(max-width:480px){.maps{grid-template-columns:1fr}}
.mcell{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;
box-shadow:var(--shadow);margin:0}
.mcell img,.mcell .mimg{width:100%;display:block;aspect-ratio:1.4;object-fit:cover}
.mcell .mimg{background-size:cover;background-position:center;background-repeat:no-repeat;background-color:#dfe8e5}
.mcell figcaption{font-size:11px;color:var(--ink-2);padding:6px 9px;font-weight:700;
display:flex;align-items:center;gap:6px}
.mz{margin-left:auto;font-family:ui-monospace,Consolas,monospace;font-weight:600;color:var(--muted);font-size:10px}
ol.narrow{list-style:none;counter-reset:n;padding:0;margin:10px 0 0;position:relative}
ol.narrow::before{content:"";position:absolute;left:15px;top:8px;bottom:8px;width:2px;background:linear-gradient(180deg,var(--accent),#dbeee8)}
.nstep{counter-increment:n;position:relative;padding:0 0 14px 42px;margin-bottom:12px}
.nstep::before{content:counter(n);position:absolute;left:5px;top:2px;width:22px;height:22px;border-radius:50%;
background:var(--accent);color:#fff;font-weight:800;font-size:11px;display:flex;align-items:center;justify-content:center;
box-shadow:0 0 0 3px var(--bg)}
.nstep.weak::before{background:var(--muted)}
.nhead{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.nlvl{font-size:10.5px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:999px;
background:var(--accent-soft);color:var(--accent-deep);white-space:nowrap}
.nlvl.l-cultural_sphere{background:#f3ecfb;color:#5b3d8a}
.nlvl.l-country{background:#eef1f0;color:var(--muted)}
.nq{font-size:14.5px;font-weight:750;color:var(--ink);flex:1;min-width:180px}
.nconf{font-family:ui-monospace,Consolas,monospace;font-size:11px;font-weight:700;color:var(--accent-deep)}
.nw{font-size:10.5px;font-weight:800}
.nrow{display:flex;gap:9px;font-size:13px;line-height:1.62;padding:2px 0}
.nk{flex:0 0 68px;color:var(--muted);font-weight:700;font-size:11.5px;padding-top:2px;text-align:right}
.nv{flex:1;color:var(--ink-2)}
.nv.em{color:var(--ink);background:var(--accent-soft);border-radius:8px;padding:6px 10px;border-left:3px solid var(--accent)}
.nrow.in .nv b{color:var(--accent-deep)}
.nrow.nocut .nv{color:var(--warn);font-style:italic}
.ncand{display:inline-block;font-size:11.5px;background:#fff;border:1px solid var(--line);border-radius:6px;
padding:1px 7px;margin:2px 4px 0 0;color:var(--ink-2)}
.nout{display:inline-block;font-size:11.5px;background:#fbe9e7;border:1px solid #f3c6c1;border-radius:6px;
padding:1px 7px;margin:2px 4px 0 0;color:#8a2a22;text-decoration:line-through;text-decoration-thickness:1px}
.sxwrap{margin-top:10px}
.sx{margin:0 0 16px}
.sx h3{font-size:15.5px;font-weight:750;letter-spacing:-.02em;margin:0 0 6px;color:var(--accent-deep)}
.sxb{font-size:14.5px;color:var(--ink-2);line-height:1.78}
.sxby{margin-top:10px;font-size:11px;color:var(--muted);font-family:ui-monospace,Consolas,monospace}
"""

# combined(다국어) 전용 — 언어 스위처 + 공유 이미지 박스
_SWITCHER_CSS = """
.langbar{position:sticky;top:0;z-index:20;display:flex;flex-wrap:wrap;gap:6px;padding:10px 22px;
background:rgba(244,247,246,.92);backdrop-filter:blur(6px);border-bottom:1px solid var(--line)}
.langbar[hidden]{display:none}

.langbar .lb{font-size:13px;font-weight:700;padding:6px 14px;border-radius:999px;border:1px solid var(--line);
background:#fff;color:var(--ink-2);cursor:pointer;font-family:inherit}
.langbar .lb:hover{border-color:var(--accent)}
.langbar .lb.active{background:var(--accent);border-color:var(--accent);color:#fff}
.doc[hidden]{display:none}
.doc-lang-tag{font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--accent-deep);padding:6px 0 0}
.capimg{width:100%;display:block;background-repeat:no-repeat;background-position:center;background-size:contain;background-color:#0d1512}
"""

_SWITCHER_JS = """
<script>
(function(){
  var bar=document.getElementById('langbar');
  if(!bar)return;
  var docs=[].slice.call(document.querySelectorAll('.doc'));
  function show(id){
    docs.forEach(function(d){d.hidden=(d.id!==id);});
    bar.querySelectorAll('.lb').forEach(function(x){x.classList.toggle('active',x.getAttribute('data-target')===id);});
    var b=bar.querySelector('.lb[data-target="'+id+'"]');
    if(b)document.documentElement.lang=b.getAttribute('data-lang')||'';
  }
  bar.hidden=false;
  bar.addEventListener('click',function(e){var b=e.target.closest('.lb');if(b)show(b.getAttribute('data-target'));});
  if(docs.length)show(docs[0].id);
})();
</script>
"""


def _document(lang_attr: str, report_word: str, title_txt: str,
              css: str, body_inner: str, tail: str = "") -> str:
    return (
        '<!DOCTYPE html>\n'
        f'<html lang="{_esc(lang_attr)}"><head><meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<title>GeoGuessHelper {_esc(report_word)} — {_esc(title_txt)}</title>\n'
        '<link rel="preconnect" href="https://cdn.jsdelivr.net">\n'
        '<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css" rel="stylesheet">\n'
        f'<style>{css}</style></head><body>{body_inner}{tail}</body></html>'
    )


def _map_center(result: dict | None, start_lat, start_lng) -> tuple:
    """지도 중심 — 링크 시작 좌표가 있으면 그것(실측), 없으면 분석의 추정 좌표."""
    try:
        return float(start_lat), float(start_lng)
    except (TypeError, ValueError):
        pass
    a = (result or {}).get("analysis") if isinstance(result, dict) else None
    ce = ((a or {}).get("best_guess") or {}).get("coordinate_estimate") or {}
    try:
        return float(ce.get("lat")), float(ce.get("lng"))
    except (TypeError, ValueError):
        return None, None


def _analyzed_files(result: dict | None, files: list[str]) -> tuple[list[str], int]:
    """갤러리에 실을 파일 = **실제로 분석된** 장면만.

    분석은 max_analyze_images(기본 6)까지만 보는데 갤러리는 선택한 캡처를 전부 실었다.
    7번째부터는 '보지도 않은 사진이 근거로 제시'되는 상태였고, 보고서만 봐서는 알 수 없었다.
    """
    files = list(files or [])
    analyzed = (result or {}).get("images") if isinstance(result, dict) else None
    if not analyzed:
        return files, 0
    names = {Path(a).name for a in analyzed}
    keep = [f for f in files if Path(f).name in names]
    if not keep:
        keep = list(analyzed)
    return keep, max(0, len(files) - len(keep))


def _tr_warning(result: dict, S: dict) -> str:
    """이 문서가 번역되지 않았다면 **본문 맨 위에** 그렇게 적는다.

    예전에는 번역이 통째로 실패해도 아무 표시가 없어서, 한국어 탭을 연 사람이
    '왜 영어지?' 하고 도구를 의심하게 됐다. 실패는 숨기는 것보다 적는 것이 낫다.
    """
    from . import i18n as _i18n

    st = result.get("translation_stats") if isinstance(result, dict) else None
    if not isinstance(st, dict):
        return ""
    total = st.get("total") or 0
    done = st.get("translated") or 0
    if total <= 0 or done >= max(1, int(total * 0.5)):
        return ""
    msg = S.get("tr_failed", "").format(
        src=_i18n.native_name(st.get("base_lang") or "en"), done=done, total=total)
    return f'<div class="pf-note warn">{_esc(msg)}</div>' if msg else ""


def build_report(result: dict, files: list[str], settings: Settings, lang: str = "ko",
                 research: dict | None = None, start_lat=None, start_lng=None,
                 knowledge: list | None = None, script: dict | None = None) -> dict:
    """단일 언어 리포트. result = analyze_captures() 반환값(또는 그 안의 analysis).
    lang = 리포트 고정 라벨 언어. research = research_location() 결과(선택).
    start_lat/start_lng = 링크 시작 좌표(파일명에 기록). 반환 {status, file, url, path, lang}."""
    from . import i18n

    result = result or {}
    lang = i18n.normalize(lang or (result.get("lang") if isinstance(result, dict) else None))
    S = i18n.report_strings(lang)

    now = datetime.datetime.now()
    ymd, hms = now.strftime("%y%m%d"), now.strftime("%H%M%S")
    stamp = now.strftime("%Y-%m-%d %H:%M")

    use_files, dropped = _analyzed_files(result, files)
    imgs = _embed_images(use_files, settings)
    gallery = _gallery_html(imgs, S)
    map_lat, map_lng = _map_center(result, start_lat, start_lng)
    maps = _locator_maps(map_lat, map_lng, settings)
    body = _compute_body(result, lang, research, gallery, len(imgs), stamp,
                         knowledge=knowledge, dropped=dropped,
                         maps_html=_maps_html(maps, S, map_lat, map_lng), script=script)

    body_inner = f'<div class="wrap">{body["inner"]}</div>'
    html_doc = _document(lang, S["report_word"], body["title"], _BASE_CSS, body_inner)

    fname = _write_report_atomic(
        settings.reports_dir,
        _report_filename(body["iso"], body["country"], body["place_slug"],
                         start_lat, start_lng, lang, ymd, hms),
        html_doc,
    )
    return {"status": "OK", "file": fname, "url": f"/reports/{fname}",
            "path": str(settings.reports_dir / fname), "lang": lang,
            "images": [im["name"] for im in imgs], "droppedImages": dropped}


def build_combined_report(sections: list[dict], files: list[str], settings: Settings,
                          start_lat=None, start_lng=None, knowledge: list | None = None,
                          script: dict | None = None) -> dict:
    """다국어 통합 리포트 — 한 파일 안에 언어별 섹션 + 상단 언어 스위처.

    sections = [{lang, result, research}, ...] (순서 = 스위처 순서, 첫 번째 = 기본 표시).
    이미지 base64 는 공유 CSS 클래스로 한 번만 임베드. 반환 {status, file, url, path, lang, langs}.
    """
    from . import i18n

    sections = [s for s in (sections or []) if s and s.get("lang")]
    if not sections:
        return {"status": "FAIL", "message": "통합 리포트에 언어 섹션이 없습니다."}
    if len(sections) == 1:
        s = sections[0]
        return build_report(s["result"], files, settings, s["lang"], s.get("research"),
                            start_lat, start_lng, knowledge=knowledge, script=script)

    langs = [i18n.normalize(s["lang"]) for s in sections]
    primary = langs[0]

    now = datetime.datetime.now()
    ymd, hms = now.strftime("%y%m%d"), now.strftime("%H%M%S")
    stamp = now.strftime("%Y-%m-%d %H:%M")

    use_files, dropped = _analyzed_files(sections[0].get("result"), files)
    imgs = _embed_images(use_files, settings)
    classes = [f"cap{i}" for i in range(len(imgs))]
    img_css = "".join(
        f'.{classes[i]}{{aspect-ratio:{im["ratio"] or 1.6};background-image:url("{im["uri"]}")}}'
        for i, im in enumerate(imgs)
    )
    # 위치 지도도 언어 수만큼 반복되면 파일이 그만큼 커진다 → 캡처와 같이 CSS 로 한 번만.
    map_lat, map_lng = _map_center(sections[0].get("result"), start_lat, start_lng)
    maps = _locator_maps(map_lat, map_lng, settings)
    mclasses = [f"mp{i}" for i in range(len(maps))]
    img_css += "".join(
        f'.{mclasses[i]}{{background-image:url("{m["uri"]}")}}' for i, m in enumerate(maps)
    )

    docs = []
    langbar_btns = []
    meta = {"iso": "", "country": "", "place_slug": "", "title": ""}
    for s, lg in zip(sections, langs):
        S = i18n.report_strings(lg)
        gallery = _gallery_html(imgs, S, classes=classes)
        # 스크립트는 기준 언어로만 쓰였다 — 그 언어 섹션에만 싣는다(오역된 서술을 만들지 않는다).
        body = _compute_body(s["result"], lg, s.get("research"), gallery, len(imgs), stamp,
                             knowledge=knowledge, dropped=dropped,
                             maps_html=_maps_html(maps, S, map_lat, map_lng, classes=mclasses),
                             script=script if (script or {}).get("_meta", {}).get("lang") == lg else None)
        doc_id = f"doc-{lg}"
        tag = f'<div class="doc-lang-tag">{_esc(i18n.native_name(lg))}</div>'
        docs.append(f'<div class="wrap doc" id="{doc_id}" lang="{_esc(lg)}">{tag}{body["inner"]}</div>')
        langbar_btns.append(
            f'<button class="lb" data-target="{doc_id}" data-lang="{_esc(lg)}">'
            f'{_esc(i18n.native_name(lg))}</button>'
        )
        # 파일명 메타는 첫 번째로 값이 채워지는 섹션 기준(대개 primary).
        for k in ("iso", "country", "place_slug", "title"):
            if not meta[k] and body.get(k):
                meta[k] = body[k]

    Sp = i18n.report_strings(primary)
    langbar = f'<div class="langbar" id="langbar" hidden>{"".join(langbar_btns)}</div>'
    title_txt = f'{meta["title"] or Sp["unknown"]} · {len(sections)}×'
    css = _BASE_CSS + _SWITCHER_CSS + img_css
    body_inner = langbar + "".join(docs)
    html_doc = _document(primary, Sp["report_word"], title_txt, css, body_inner, tail=_SWITCHER_JS)

    _ = knowledge  # combined 본문에서 이미 렌더됨(각 언어 섹션마다)
    lang_tag = "-".join(langs)
    fname = _write_report_atomic(
        settings.reports_dir,
        _report_filename(meta["iso"], meta["country"], meta["place_slug"],
                         start_lat, start_lng, lang_tag, ymd, hms),
        html_doc,
    )
    return {"status": "OK", "file": fname, "url": f"/reports/{fname}",
            "path": str(settings.reports_dir / fname), "lang": primary, "langs": langs,
            "images": [im["name"] for im in imgs], "droppedImages": dropped}


# ── 종합 보고서 (A + B' + C') ────────────────────────────────────
def build_synthesis_report(syn: dict, meta: dict, settings: Settings, lang: str = "en") -> dict:
    """저장된 원자만으로 재조립한 종합 보고서. 새 웹 검색 없음.

    같은 문명권·같은 시기를 다룬 여러 보고서가 쌓이면, 광역 사실 A 는 이미 저장돼 있으므로
    다시 조사하지 않고 각 장소의 B'·C' 만 붙여 새 문서를 만든다. .html 은 docs/report/ 에,
    같은 내용의 .md 는 docs/knowledge/synthesis/ 에 남아 다음 종합의 재료가 된다.
    """
    from . import i18n

    lang = i18n.normalize(lang)
    S = i18n.report_strings(lang)
    now = datetime.datetime.now()
    ymd, hms = now.strftime("%y%m%d"), now.strftime("%H%M%S")
    stamp = now.strftime("%Y-%m-%d %H:%M")

    title = (syn or {}).get("title") or (meta or {}).get("selector") or "Synthesis"
    thesis = (syn or {}).get("thesis") or ""
    sections = [s for s in ((syn or {}).get("sections") or []) if isinstance(s, dict)]
    conns = [c for c in ((syn or {}).get("connections") or []) if isinstance(c, dict)]
    opens = [q for q in ((syn or {}).get("open_questions") or []) if q]
    atoms = (meta or {}).get("atoms") or []
    places = (meta or {}).get("places") or []
    reports = (meta or {}).get("reports") or []

    def atom_links(ids) -> str:
        good = [i for i in (ids or []) if isinstance(i, str) and i.startswith("atm_")]
        if not good:
            return ""
        return " ".join(
            f'<a class="katom" href="../knowledge/atoms/{_esc(i)}.md"><code>{_esc(i)}</code></a>'
            for i in good[:8]
        )

    # 본문 안의 [[atm_xxx]] 인용을 실제 링크로.
    def linkify(text: str) -> str:
        out = _esc(text)
        return re.sub(
            r"\[\[(atm_[0-9a-f]{6,})\]\]",
            lambda m: f'<a class="katom" href="../knowledge/atoms/{m.group(1)}.md"><code>{m.group(1)}</code></a>',
            out,
        )

    body_html = "".join(
        f'<h2>{_esc(s.get("heading") or "")}</h2>'
        f'<div class="read">{linkify(s.get("body") or "")}</div>'
        + (f'<div class="alt">{atom_links(s.get("atom_ids"))}</div>' if s.get("atom_ids") else "")
        for s in sections
    )
    conn_html = ""
    if conns:
        items = "".join(
            f'<li>{linkify(c.get("claim") or "")} {atom_links(c.get("atom_ids"))}</li>' for c in conns
        )
        conn_html = f'<h2>{_esc(S["syn_conn"])}</h2><ul class="clean">{items}</ul>'
    open_html = ""
    if opens:
        open_html = (
            f'<h2>{_esc(S["syn_open"])}</h2><ul class="clean">'
            + "".join(f"<li>{_esc(q)}</li>" for q in opens)
            + "</ul>"
        )

    inner = (
        f'<div class="kicker">GeoGuessHelper {_esc(S["syn_word"])} · {_esc(stamp)}</div>'
        f'<div class="hero"><div class="country">{_esc(title)}</div>'
        f'<div class="region">{_esc(S["syn_places"])}: {_esc(" · ".join(places) or "—")}</div></div>'
        f'<div class="pf-note">{_esc(S["syn_basis"])}</div>'
        + (f'<h2>{_esc(S["syn_thesis"])}</h2><div class="pf-lead">{linkify(thesis)}</div>' if thesis else "")
        + body_html + conn_html + open_html
        + f'<h2>{_esc(S["syn_atoms"])} <span class="muted">{len(atoms)}</span></h2>'
        f'<div class="alt">{atom_links(atoms)}</div>'
        + (
            '<h2>' + _esc(S["r_sources"]) + '</h2><ul class="clean">'
            + "".join(f'<li><a href="/reports/{_esc(r)}">{_esc(r)}</a></li>' for r in reports)
            + "</ul>"
            if reports else ""
        )
        + f'<div class="foot">{S["foot"]}</div>'
    )
    html_doc = _document(lang, S["syn_word"], title, _BASE_CSS,
                         f'<div class="wrap">{inner}</div>')

    base = f"synth_{_slug(title)[:40]}_{ymd}_{hms}_{lang}.html"
    fname = _write_report_atomic(settings.reports_dir, base, html_doc)

    # 같은 내용의 .md 를 지식 저장소에 남긴다 — 다음 종합의 재료.
    try:
        md_dir = settings.knowledge_dir / "synthesis"
        md_dir.mkdir(parents=True, exist_ok=True)
        md = (
            f"# {title}\n\n> {S['syn_basis']}\n\n"
            f"**{S['syn_places']}**: {', '.join(places) or '—'}\n\n"
            + (f"## {S['syn_thesis']}\n{thesis}\n\n" if thesis else "")
            + "".join(f"## {s.get('heading','')}\n{s.get('body','')}\n\n" for s in sections)
            + (f"## {S['syn_conn']}\n" + "".join(f"- {c.get('claim','')}\n" for c in conns) + "\n" if conns else "")
            + (f"## {S['syn_open']}\n" + "".join(f"- {q}\n" for q in opens) + "\n" if opens else "")
            + f"## {S['syn_atoms']}\n" + "".join(f"- [[{a}]]\n" for a in atoms)
            + ("\n## " + S["r_sources"] + "\n" + "".join(f"- `{r}`\n" for r in reports) if reports else "")
        )
        (md_dir / f"{Path(fname).stem}.md").write_text(md, encoding="utf-8")
    except Exception:  # noqa: BLE001 — md 실패가 html 을 막지 않는다
        pass

    return {"status": "OK", "file": fname, "url": f"/reports/{fname}",
            "path": str(settings.reports_dir / fname), "lang": lang,
            "langName": i18n.native_name(lang), "combined": False, "synthesis": True}
