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
import html
import re
from pathlib import Path

from .config import Settings


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


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
    parts = ["report", _slug(iso or country)]
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


# ── 언어별 본문(내부 HTML) 조립 ──────────────────────────────────
def _compute_body(result: dict, lang: str, research: dict | None,
                  gallery_html: str, n_imgs: int, stamp: str) -> dict:
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

    def cues_table():
        if not cues:
            return ""
        rows = "".join(
            f"<tr><td>{_esc(c.get('category'))}</td>"
            f"<td>{_esc(c.get('observation'))}"
            + (f' <span class="muted">[{_esc(",".join(c.get("supports") or []))}]</span>' if c.get("supports") else "")
            + f'</td><td class="w-{_esc((c.get("weight") or "").lower())}">'
            + f"{_esc(i18n.enum_label(lang, 'weight', c.get('weight')))}</td></tr>"
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

    inner = (
        f'<div class="kicker">GeoGuessHelper {_esc(S["report_word"])} · {_esc(stamp)}</div>'
        f'<div class="hero">'
        f'<div class="country">{_esc(title)}</div>'
        f'<div class="region">{_esc(region)}</div>'
        f'<div class="confbar"><i style="width:{conf}%"></i></div>'
        f'<div class="conf-label">{_esc(S["conf_label"].format(conf=conf))}</div>'
        f'{coord_block()}{chips()}'
        f'</div>'
        f'<h2>{_esc(S["scenes_h"])} <span class="muted">{_esc(S["scenes_meta"].format(n=n_imgs))}</span></h2>'
        f'{gallery_html}'
        f'{cues_table()}{langs_block()}{landmarks_block()}'
        f'{culture}'
        f'{research_block()}{alts_block()}'
        f'<div class="meta">'
        f'<span class="chip">{_esc(S["meta_model"])} {_esc(model)}</span>'
        f'{cost_chip}'
        f'<span class="chip">{_esc(S["meta_images"].format(n=n_imgs))}</span>'
        f'</div>'
        f'<div class="foot">{S["foot"]}</div>'
    )
    return {"inner": inner, "iso": iso, "country": country, "city": city,
            "place_slug": place_slug, "title": title}


_BASE_CSS = """
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


def build_report(result: dict, files: list[str], settings: Settings, lang: str = "ko",
                 research: dict | None = None, start_lat=None, start_lng=None) -> dict:
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

    imgs = _embed_images(files, settings)
    gallery = _gallery_html(imgs, S)
    body = _compute_body(result, lang, research, gallery, len(imgs), stamp)

    body_inner = f'<div class="wrap">{body["inner"]}</div>'
    html_doc = _document(lang, S["report_word"], body["title"], _BASE_CSS, body_inner)

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    fname = _report_filename(body["iso"], body["country"], body["place_slug"],
                             start_lat, start_lng, lang, ymd, hms)
    out = settings.reports_dir / fname
    out.write_text(html_doc, encoding="utf-8")

    return {"status": "OK", "file": fname, "url": f"/reports/{fname}",
            "path": str(out), "lang": lang}


def build_combined_report(sections: list[dict], files: list[str], settings: Settings,
                          start_lat=None, start_lng=None) -> dict:
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
                            start_lat, start_lng)

    langs = [i18n.normalize(s["lang"]) for s in sections]
    primary = langs[0]

    now = datetime.datetime.now()
    ymd, hms = now.strftime("%y%m%d"), now.strftime("%H%M%S")
    stamp = now.strftime("%Y-%m-%d %H:%M")

    imgs = _embed_images(files, settings)
    classes = [f"cap{i}" for i in range(len(imgs))]
    img_css = "".join(
        f'.{classes[i]}{{aspect-ratio:{im["ratio"] or 1.6};background-image:url("{im["uri"]}")}}'
        for i, im in enumerate(imgs)
    )

    docs = []
    langbar_btns = []
    meta = {"iso": "", "country": "", "place_slug": "", "title": ""}
    for s, lg in zip(sections, langs):
        S = i18n.report_strings(lg)
        gallery = _gallery_html(imgs, S, classes=classes)
        body = _compute_body(s["result"], lg, s.get("research"), gallery, len(imgs), stamp)
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

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    lang_tag = "-".join(langs)
    fname = _report_filename(meta["iso"], meta["country"], meta["place_slug"],
                             start_lat, start_lng, lang_tag, ymd, hms)
    out = settings.reports_dir / fname
    out.write_text(html_doc, encoding="utf-8")

    return {"status": "OK", "file": fname, "url": f"/reports/{fname}",
            "path": str(out), "lang": primary, "langs": langs}
