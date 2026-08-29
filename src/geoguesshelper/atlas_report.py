"""어드바이저 종합 보고서 — 아틀라스 슬라이스(테마 × 범위)를 원자만으로 서술한다.

기획 v2(knowledge-atlas_260816) 5단계. 지점 보고서가 "이 장소는 무엇인가"라면, 이 문서는
"이 판을 누가 움직이는가"다: 거점 → 행위자 → 관계 역학 → 행동 포인트 → 공백.

원칙
  · 입력은 닫혀 있다 — 슬라이스의 행위자·관계·인용 원자 본문만. 웹 검색 없음.
    서술의 모든 문장은 [[atm_...]] 인용을 갖고, 인용 없는 주장은 스키마가 막는다.
  · 비순환 — 이 보고서는 지식 저장소에 적재하지 않는다. 기존 원자의 재서술을 다시
    원자로 만들면 같은 사실이 출처를 잃고 증식한다(자기 인용 오염).
  · 기계 가독 슬라이스를 내장한다(<script id="atlas-slice">) — 훗날의 보고서와 대조하고,
    뷰어 없이도 재이용할 수 있게.
  · 주식·부동산 테마는 "확인하라·주목하라·조사하라"까지다. 매수·매도는 구조적으로 못 낸다.

데이터는 altaiya 의 조립(atlas.build)을 그대로 가져온다 — 사이드카가 정본이고 서버가
따로 떠 있을 필요가 없다. 파일은 docs/report/atlas/ 에 놓인다.
"""
from __future__ import annotations

import copy
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import i18n, llm, translate
from .config import Settings, project_root, report_subdir
from .report import _BASE_CSS, _SWITCHER_CSS, _SWITCHER_JS, _document, _esc, _slug, _write_report_atomic

THEMES = ("stock", "realty", "trade", "corporate", "talent", "travel", "heritage")
THEME_KO = {"stock": "주식 투자", "realty": "부동산", "trade": "수출입", "corporate": "법인사업",
            "talent": "교육·인재", "travel": "여행·문화", "heritage": "역사·기록", "all": "전체"}
THEME_EN = {"stock": "Equity", "realty": "Real estate", "trade": "Trade", "corporate": "Corporate",
            "talent": "Talent", "travel": "Travel", "heritage": "Heritage", "all": "All themes"}
_INVEST = {"stock", "realty"}

MAX_ACTORS = 40
MAX_EDGES = 90
ATOM_CHAR_BUDGET = 26000

_LAYER_COLOR = {"economy": "#34d3a6", "history": "#e8b34b", "culture": "#c983e8", "geography": "#5fb7dd",
                "language": "#ee7fa9", "architecture": "#9d92f2", "nature": "#8ed877"}
_TYPE_ICON = {"place": "📍", "company": "🏭", "org": "🏛", "person": "👤",
              "infra": "🌉", "polity": "👑", "culture": "🗣", "nature": "🏞"}

# 고정 라벨 — 서술은 모델이 대상 언어로 쓰지만, 뼈대 제목은 여기서 온다.
_L = {
    "ko": {"word": "아틀라스 보고서", "map": "전략 지도 — 이 판", "hubs": "주요 거점", "actors": "주요 행위자",
           "dyn": "관계 역학", "act": "행동 포인트", "gaps": "공백과 다음 탐사", "open": "남은 질문",
           "atoms": "사용한 원자", "basis": "슬라이스의 원자만으로 서술했다 — 새 웹 검색은 없다. 모든 문장은 인용 원자로 검증하라.",
           "notice": "투자 조언이 아니다. 현장 관찰과 지역 지식의 참고 단서와 출처까지만이다 — 재무·시세·규제 데이터가 없으므로 종목·매수 판단은 만들 수 없다.",
           "cov": "데이터 기반", "range": "범위", "theme": "테마", "built": "조립", "model": "생성 모델"},
    "en": {"word": "Atlas report", "map": "Strategic map — this board", "hubs": "Key hubs", "actors": "Key actors",
           "dyn": "Relationship dynamics", "act": "Action points", "gaps": "Gaps and next exploration", "open": "Open questions",
           "atoms": "Atoms used", "basis": "Written from the slice's atoms only — no new web research. Verify every sentence against its cited atoms.",
           "notice": "Not investment advice. Field observation and local knowledge with sources, nothing more — without financial, price or regulatory data no security or buy/sell judgement can be made.",
           "cov": "Data basis", "range": "Range", "theme": "Theme", "built": "Built", "model": "Model"},
    "fr": {"word": "Rapport d'atlas", "map": "Carte stratégique — ce plateau", "hubs": "Nœuds principaux", "actors": "Acteurs principaux",
           "dyn": "Dynamiques relationnelles", "act": "Points d'action", "gaps": "Lacunes et prochaine exploration", "open": "Questions ouvertes",
           "atoms": "Atomes utilisés", "basis": "Rédigé uniquement à partir des atomes de la tranche — aucune nouvelle recherche web. Vérifiez chaque phrase avec ses atomes cités.",
           "notice": "Ceci n'est pas un conseil en investissement. Observations de terrain et connaissance locale avec sources, rien de plus.",
           "cov": "Base de données", "range": "Portée", "theme": "Thème", "built": "Assemblé", "model": "Modèle"},
}


def _labels(lang: str) -> dict:
    return _L.get(i18n.normalize(lang), _L["en"])


# ── 아틀라스 로드 (altaiya 조립 재사용) ─────────────────────────────
def _atlas_module():
    backend = project_root() / "altaiya" / "altaiya-backend"
    if not (backend / "atlas.py").exists():
        raise RuntimeError("altaiya 서브모듈이 없다 — git submodule update --init")
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    import atlas  # noqa: WPS433 — altaiya 의 조립기
    return atlas


def load_atlas() -> dict:
    return _atlas_module().build()


# ── 슬라이스 ─────────────────────────────────────────────────────
def parse_range(spec: str) -> tuple[str, str]:
    """'world' | 'sphere:<key>' | 'actor:<slug>' → (kind, key)."""
    s = (spec or "world").strip()
    if ":" in s:
        kind, key = s.split(":", 1)
        kind = kind.strip().lower()
        if kind in ("sphere", "actor") and key.strip():
            return kind, key.strip()
        raise ValueError(f"범위 형식: world | sphere:<key> | actor:<slug> (받은 값 {spec!r})")
    return "world", ""


def slice_atlas(data: dict, theme: str, range_spec: str, settings: Settings) -> dict:
    """테마 × 범위로 서브그래프를 자른다. 반환은 서술과 지도 스냅샷의 유일한 재료."""
    from . import knowledge

    kind, key = parse_range(range_spec)
    meta = data["meta"]
    a_min = meta.get("actor_theme_min", 0.5)
    e_min = meta.get("edge_theme_min", 0.4)
    actors = {a["slug"]: a for a in data["actors"]}

    def theme_ok(a: dict) -> bool:
        return theme == "all" or (a.get("themes") or {}).get(theme, 0) >= a_min

    if kind == "sphere":
        sp = next((s for s in data.get("spheres") or [] if s["key"] == key), None)
        if sp is None:
            raise ValueError(f"사료권 {key!r} 이 없다")
        pool = [a for a in actors.values() if a.get("sphere") == key]
        range_label = sp.get("label_ko") or key
        range_en = sp.get("label_en") or key
    elif kind == "actor":
        seed = actors.get(key)
        if seed is None:
            raise ValueError(f"행위자 {key!r} 이 없다")
        neigh = {key}
        for e in data["edges"]:
            if e["src"] == key:
                neigh.add(e["dst"])
            elif e["dst"] == key:
                neigh.add(e["src"])
        pool = [actors[s] for s in neigh if s in actors]
        range_label = seed.get("label") or key
        range_en = seed.get("label_en") or key
        sp = None
    else:
        pool = list(actors.values())
        range_label, range_en, sp = "세계", "World", None

    pool = [a for a in pool if theme_ok(a)]
    if kind == "actor" and key in actors and actors[key] not in pool:
        pool.append(actors[key])            # 씨앗은 테마 점수와 무관하게 남긴다
    pool.sort(key=lambda a: (-(a.get("themes") or {}).get(theme, 0) if theme != "all" else 0,
                             -a.get("degree", 0), -a.get("atom_count", 0)))
    chosen = pool[:MAX_ACTORS]
    keep = {a["slug"] for a in chosen}
    dropped_actors = len(pool) - len(chosen)

    edges = [e for e in data["edges"] if e["src"] in keep and e["dst"] in keep
             and (theme == "all" or (e.get("themes") or {}).get(theme, 0) >= e_min)]
    edges.sort(key=lambda e: -e.get("strength", 1))
    dropped_edges = max(0, len(edges) - MAX_EDGES)
    edges = edges[:MAX_EDGES]

    # 인용 원자 — 관계가 인용한 것 먼저, 그다음 행위자의 원자. 문자 예산 안에서.
    st = knowledge.store_for(settings)
    order: list[str] = []
    seen: set[str] = set()
    for e in edges:
        for aid in e.get("atoms") or []:
            if aid not in seen:
                seen.add(aid); order.append(aid)
    for a in chosen:
        for aid in a.get("atoms") or []:
            if aid not in seen:
                seen.add(aid); order.append(aid)
    atoms, used = [], 0
    for aid in order:
        at = st.load(aid)
        if not at:
            continue
        used += len(at.body) + len(at.title)
        if used > ATOM_CHAR_BUDGET:
            break
        atoms.append(at)
    dropped_atoms = len(order) - len(atoms)

    # 중심성 — 슬라이스 안에서 다시 센다("이 판의 주인공")
    deg: dict[str, int] = {s: 0 for s in keep}
    for e in edges:
        deg[e["src"]] += 1; deg[e["dst"]] += 1
    hubs = sorted(chosen, key=lambda a: (-deg[a["slug"]], -a.get("atom_count", 0)))[:8]

    return {
        "theme": theme, "range": range_spec, "range_kind": kind, "range_key": key,
        "range_label": range_label, "range_en": range_en, "sphere": sp,
        "actors": [{k: a.get(k) for k in ("slug", "label", "label_en", "type", "scope", "lat", "lng",
                                          "atom_count", "degree", "period", "sphere", "placeless")}
                   | {"slice_degree": deg[a["slug"]], "theme_score": (a.get("themes") or {}).get(theme, 0)}
                   for a in chosen],
        "edges": [{k: e.get(k) for k in ("src", "dst", "rel", "rel_ko", "dir", "period", "atoms",
                                         "strength", "layer")} for e in edges],
        "atoms": atoms,
        "hubs": [a["slug"] for a in hubs],
        "dropped": {"actors": dropped_actors, "edges": dropped_edges, "atoms": dropped_atoms},
        "built": meta.get("built"),
        "coverage_note": (f"행위자 {len(chosen)} · 관계 {len(edges)} · 원자 {len(atoms)}"
                          + (f" (예산 밖: 행위자 {dropped_actors} · 관계 {dropped_edges} · 원자 {dropped_atoms})"
                             if dropped_actors or dropped_edges or dropped_atoms else "")),
    }


# ── 서술 (LLM, 닫힌 세계) ─────────────────────────────────────────
_ADVISOR_TOOL = {
    "name": "atlas_advisor",
    "description": "Write the advisor report for one theme × range slice of the knowledge atlas.",
    "input_schema": {
        "type": "object",
        "required": ["title", "thesis", "hubs", "actors", "dynamics", "actions", "gaps"],
        "properties": {
            "title": {"type": "string", "description": "One line naming the board (theme × range)."},
            "thesis": {"type": "string", "description": "3-5 sentences: who moves this board and why. Cite atoms inline as [[atm_...]]."},
            "hubs": {"type": "array", "items": {"type": "object", "required": ["slug", "why", "atom_ids"],
                     "properties": {"slug": {"type": "string"}, "why": {"type": "string", "description": "Why this is a node — what gathers here. Cite atoms."},
                                    "atom_ids": {"type": "array", "items": {"type": "string"}}}}},
            "actors": {"type": "array", "items": {"type": "object", "required": ["slug", "role", "atom_ids"],
                       "properties": {"slug": {"type": "string"}, "role": {"type": "string", "description": "Type, reach, period, and what it does on this board. Cite atoms."},
                                      "atom_ids": {"type": "array", "items": {"type": "string"}}}}},
            "dynamics": {"type": "array", "description": "Chains: supply chains, lineages, oppositions. 2-6 items.",
                         "items": {"type": "object", "required": ["heading", "chain", "atom_ids"],
                                   "properties": {"heading": {"type": "string"}, "chain": {"type": "string", "description": "Prose narrating the chain A → B → C. Every claim cites an atom."},
                                                  "atom_ids": {"type": "array", "items": {"type": "string"}}}}},
            "actions": {"type": "array", "description": "For the reader of this theme. Verbs limited to check / watch / investigate for investment themes — never buy/sell.",
                        "items": {"type": "object", "required": ["audience", "text", "atom_ids"],
                                  "properties": {"audience": {"type": "string"}, "text": {"type": "string"},
                                                 "atom_ids": {"type": "array", "items": {"type": "string"}}}}},
            "gaps": {"type": "array", "items": {"type": "string"}, "description": "What is missing on this board — actor types, regions, broken chains. Plain sentences."},
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def _prompt_block(sl: dict) -> str:
    actors = "\n".join(
        f"- {a['slug']} | {a['label']} ({a.get('label_en') or ''}) | type={a['type']} scope={a['scope']}"
        f" | slice_degree={a['slice_degree']} atoms={a['atom_count']}"
        + (f" | period={a['period']}" if a.get("period") else "")
        for a in sl["actors"])
    edges = "\n".join(
        f"- {e['src']} {e['dir']} {e['dst']} : {e['rel']}"
        + (f" period={e['period']}" if e.get("period") else "")
        + f" atoms={','.join(e.get('atoms') or [])}"
        for e in sl["edges"])
    atoms = "\n".join(
        f"- [[{a.id}]] ({a.layer}/{a.scope})"
        + (f" [{a.period_start or '?'}–{a.period_end or '?'}]" if a.period_start is not None or a.period_end is not None else "")
        + f" **{a.title}** — {a.body}"
        for a in sl["atoms"])
    return (f"THEME: {sl['theme']}\nRANGE: {sl['range_kind']} {sl['range_label']} ({sl['range_en']})\n"
            f"HUBS (by slice centrality): {', '.join(sl['hubs'])}\n\n"
            f"ACTORS ({len(sl['actors'])}):\n{actors}\n\n"
            f"RELATIONS ({len(sl['edges'])}) — every relation cites atoms:\n{edges}\n\n"
            f"ATOMS ({len(sl['atoms'])}) — this is your ONLY evidence:\n{atoms}\n")


def compose(settings: Settings, sl: dict, lang: str, *, should_stop=None) -> tuple[dict, float]:
    theme = sl["theme"]
    invest = theme in _INVEST
    lang_name = i18n.native_name(lang)
    system = (
        "You are a strategic analyst reading a knowledge atlas — actors (places, companies, organisations, "
        "people, infrastructure, polities, cultures) and their relations, all extracted from field reports. "
        "You write an advisor report for ONE theme on ONE board (range).\n\n"
        "RULES\n"
        "- Closed world: use ONLY the supplied atoms as evidence. Cite them inline as [[atm_id]] in every "
        "sentence that asserts a fact. If the atoms do not support a claim, do not make it.\n"
        "- Relations listed are the only relations that exist. An absent relation is not evidence of absence — "
        "call it a gap, never infer it.\n"
        "- Name actors by their label; slugs go only in the slug fields.\n"
        "- Say plainly where the evidence is thin. The 'gaps' list is as important as the thesis.\n"
        + ("- INVESTMENT THEME: actions may only use the verbs check / watch / investigate (확인하라·주목하라·조사하라). "
           "Never recommend buying, selling, holding, or any security. No prices, no forecasts.\n" if invest else "")
        + f"- Write all prose in {lang_name}. Keep atom ids and slugs verbatim.\n"
        "- 4-8 hubs, 6-14 actors, 2-6 dynamics, 3-8 actions. Call atlas_advisor once."
    )
    resp = llm.call(
        settings,
        system=system,
        messages=[{"role": "user", "content": _prompt_block(sl)}],
        tools=[_ADVISOR_TOOL],
        tool_choice={"type": "tool", "name": "atlas_advisor"},
        max_tokens=settings.synthesis_max_tokens,
        effort=settings.knowledge_effort,
        deadline_s=max(240.0, settings.knowledge_timeout_s * 4),
        role="reason",
        should_stop=should_stop,
    )
    doc = llm.tool_input(resp, "atlas_advisor") or {}
    doc["_meta"] = {"lang": i18n.normalize(lang), "model": llm.used_model(resp)}
    return doc, llm.spend(resp)


# ── 번역 — 서술 문자열만 옮긴다 (id·슬러그·인용은 고정) ──────────────
_TEXT_FIELDS = (("title",), ("thesis",))


def _harvest(doc: dict) -> dict[str, str]:
    texts: dict[str, str] = {}
    for k in ("title", "thesis"):
        if doc.get(k):
            texts[k] = doc[k]
    for sec, field in (("hubs", "why"), ("actors", "role"), ("dynamics", "chain"), ("dynamics", "heading"),
                       ("actions", "text"), ("actions", "audience")):
        for i, item in enumerate(doc.get(sec) or []):
            if isinstance(item, dict) and item.get(field):
                texts[f"{sec}.{i}.{field}"] = item[field]
    for sec in ("gaps", "open_questions"):
        for i, s in enumerate(doc.get(sec) or []):
            if isinstance(s, str) and s:
                texts[f"{sec}.{i}"] = s
    return texts


def _splice(doc: dict, done: dict[str, str]) -> dict:
    out = copy.deepcopy(doc)
    for key, val in done.items():
        parts = key.split(".")
        if len(parts) == 1:
            out[parts[0]] = val
        elif len(parts) == 2:
            sec, i = parts[0], int(parts[1])
            if i < len(out.get(sec) or []):
                out[sec][i] = val
        else:
            sec, i, field = parts[0], int(parts[1]), parts[2]
            if i < len(out.get(sec) or []) and isinstance(out[sec][i], dict):
                out[sec][i][field] = val
    return out


def translate_doc(doc: dict, lang: str, settings: Settings, *, base_lang: str) -> tuple[dict, float]:
    texts = _harvest(doc)
    if not texts:
        return doc, 0.0
    st: dict = {}
    done, cost = translate.translate_texts(texts, lang, settings, base_lang=base_lang, stats=st)
    out = _splice(doc, done)
    out["_meta"] = dict(doc.get("_meta") or {}) | {"lang": i18n.normalize(lang), "translatedFrom": i18n.normalize(base_lang),
                                                    "translated": st.get("translated"), "requested": st.get("requested")}
    return out, cost


# ── 렌더 ─────────────────────────────────────────────────────────
_ATOM_RE = re.compile(r"\[\[(atm_[0-9a-zA-Z]{4,})\]\]")


def _linkify(text: Any) -> str:
    esc = _esc(text)
    return _ATOM_RE.sub(lambda m: f'<a class="katom" href="../../knowledge/atoms/{m.group(1)}.md"><code>{m.group(1)}</code></a>', esc)


def _atom_links(ids) -> str:
    good = [i for i in (ids or []) if isinstance(i, str) and i.startswith("atm_")]
    return " ".join(f'<a class="katom" href="../../knowledge/atoms/{_esc(i)}.md"><code>{_esc(i)}</code></a>' for i in good[:10])


def _map_svg(sl: dict) -> str:
    """슬라이스의 전략 지도 스냅샷 — 뷰어 없이도 보고서 안에서 판이 보인다.

    등장방형 투영, 슬라이스 행위자의 bbox 에 맞춘다. 해안선은 뷰어와 같은 Natural Earth
    110m(퍼블릭 도메인)에서 bbox 와 겹치는 폴리곤만 넣는다.
    """
    pts = [(a["lat"], a["lng"], a) for a in sl["actors"] if a.get("lat") is not None and a.get("lng") is not None]
    if not pts:
        return ""
    lats = [p[0] for p in pts]; lngs = [p[1] for p in pts]
    pad_lat = max(1.0, (max(lats) - min(lats)) * 0.25)
    pad_lng = max(1.5, (max(lngs) - min(lngs)) * 0.25)
    s, n = max(-85, min(lats) - pad_lat), min(85, max(lats) + pad_lat)
    w, e = max(-180, min(lngs) - pad_lng), min(180, max(lngs) + pad_lng)
    W = 900.0
    H = max(280.0, min(620.0, W * (n - s) / max(0.01, (e - w))))
    X = lambda lng: (lng - w) / (e - w) * W        # noqa: E731
    Y = lambda lat: (n - lat) / (n - s) * H        # noqa: E731

    coast = ""
    try:
        geo = json.loads((project_root() / "altaiya" / "altaiya-frontend" / "world.geo.json").read_text(encoding="utf-8"))
        parts = []
        for f in geo.get("features") or []:
            g = f.get("geometry") or {}
            polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates") if g.get("type") == "MultiPolygon" else []
            for poly in polys:
                for ring in poly:
                    xs = [c[0] for c in ring]; ys = [c[1] for c in ring]
                    if max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n:
                        continue
                    parts.append("M" + "L".join(f"{X(c[0]):.1f},{Y(c[1]):.1f}" for c in ring) + "Z")
        if parts:
            coast = f'<path d="{"".join(parts)}" fill="#e6ebe8" stroke="#b9c4bf" stroke-width="0.7"/>'
    except Exception:  # noqa: BLE001 — 해안선이 없어도 판은 그린다
        coast = ""

    by = {a["slug"]: a for a in sl["actors"]}
    lines = []
    for ed in sl["edges"]:
        a, b = by.get(ed["src"]), by.get(ed["dst"])
        if not a or not b or a.get("lat") is None or b.get("lat") is None:
            continue
        x1, y1, x2, y2 = X(a["lng"]), Y(a["lat"]), X(b["lng"]), Y(b["lat"])
        mx, my = (x1 + x2) / 2 - (y2 - y1) * 0.12, (y1 + y2) / 2 + (x2 - x1) * 0.12
        col = _LAYER_COLOR.get(ed.get("layer") or "", "#889")
        lines.append(f'<path d="M{x1:.1f},{y1:.1f}Q{mx:.1f},{my:.1f} {x2:.1f},{y2:.1f}" fill="none" stroke="{col}" '
                     f'stroke-width="{0.8 + min(ed.get("strength") or 1, 4) * 0.4:.1f}" opacity="0.75"><title>{_esc(a["label"])} {_esc(ed["dir"])} {_esc(b["label"])} · {_esc(ed.get("rel_ko") or ed["rel"])}</title></path>')
    nodes = []
    for lat, lng, a in sorted(pts, key=lambda p: -p[2]["slice_degree"]):
        r = 4 + (a["slice_degree"] ** 0.5) * 2.2
        x, y = X(lng), Y(lat)
        hub = a["slug"] in sl["hubs"]
        nodes.append(
            f'<g><circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{"#f0b429" if hub else "#3a7d6b"}" '
            f'fill-opacity="0.85" stroke="#fff" stroke-width="1.2"><title>{_esc(a["label"])} · {_esc(a["type"])} · 연결 {a["slice_degree"]}</title></circle>'
            f'<text x="{x + r + 3:.1f}" y="{y + 4:.1f}" font-size="{11 if hub else 9.5}" font-weight="{700 if hub else 500}" fill="#243">{_esc(a["label"])}</text></g>')
    return (f'<svg class="atlas-map" viewBox="0 0 {W:.0f} {H:.0f}" xmlns="http://www.w3.org/2000/svg" role="img">'
            f'<rect width="{W:.0f}" height="{H:.0f}" fill="#f4f7f6"/>{coast}{"".join(lines)}{"".join(nodes)}</svg>')


_ATLAS_CSS = """
.atlas-map{width:100%;height:auto;display:block;border:1px solid var(--line);border-radius:10px;margin:8px 0 4px;background:#f4f7f6}
.al-hub{display:grid;grid-template-columns:1fr;gap:10px;margin:8px 0}
.al-card{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:#fff}
.al-card b{display:block;font-size:15px;margin-bottom:4px}
.al-card .role{color:var(--ink-2);font-size:13.5px;line-height:1.6}
.al-act{margin:6px 0;padding:10px 14px;border-left:3px solid var(--accent);background:#fafcfb;border-radius:0 8px 8px 0}
.al-act .aud{font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--accent-deep)}
.al-notice{margin:8px 0 14px;padding:10px 14px;border:1px dashed #c98;border-radius:8px;color:#7a3b2e;background:#fff7f4;font-size:13px}
.al-meta{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:12.5px;color:var(--ink-2);margin:6px 0 10px}
.al-meta b{color:var(--ink)}
"""


def _section_html(doc: dict, sl: dict, lang: str, stamp: str) -> str:
    L = _labels(lang)
    by = {a["slug"]: a for a in sl["actors"]}
    theme = sl["theme"]
    theme_name = (THEME_KO if i18n.normalize(lang) == "ko" else THEME_EN).get(theme, theme)

    def card(item: dict, field: str) -> str:
        a = by.get(item.get("slug") or "", {})
        label = a.get("label") or item.get("slug") or "?"
        ico = _TYPE_ICON.get(a.get("type") or "", "📍")
        return (f'<div class="al-card"><b>{ico} {_esc(label)}'
                + (f' <span class="muted">({_esc(a.get("label_en"))})</span>' if a.get("label_en") and a.get("label_en") != label else "")
                + f'</b><div class="role">{_linkify(item.get(field) or "")}</div>'
                f'<div class="alt">{_atom_links(item.get("atom_ids"))}</div></div>')

    hubs = "".join(card(h, "why") for h in doc.get("hubs") or [] if isinstance(h, dict))
    actors = "".join(card(a, "role") for a in doc.get("actors") or [] if isinstance(a, dict))
    dyn = "".join(
        f'<h3>{_esc(d.get("heading") or "")}</h3><div class="read">{_linkify(d.get("chain") or "")}</div>'
        f'<div class="alt">{_atom_links(d.get("atom_ids"))}</div>'
        for d in doc.get("dynamics") or [] if isinstance(d, dict))
    acts = "".join(
        f'<div class="al-act"><div class="aud">{_esc(a.get("audience") or "")}</div>'
        f'<div>{_linkify(a.get("text") or "")}</div><div class="alt">{_atom_links(a.get("atom_ids"))}</div></div>'
        for a in doc.get("actions") or [] if isinstance(a, dict))
    gaps = "".join(f"<li>{_linkify(g)}</li>" for g in doc.get("gaps") or [] if isinstance(g, str))
    opens = "".join(f"<li>{_linkify(q)}</li>" for q in doc.get("open_questions") or [] if isinstance(q, str))
    atom_ids = [a.id for a in sl["atoms"]]
    model = (doc.get("_meta") or {}).get("model") or ""
    built = datetime.datetime.fromtimestamp(sl["built"]).strftime("%Y-%m-%d %H:%M") if sl.get("built") else "—"

    return (
        f'<div class="kicker">GeoGuessHelper · ALTAIYA {_esc(L["word"])} · {_esc(stamp)}</div>'
        f'<div class="hero"><div class="country">{_esc(doc.get("title") or sl["range_label"])}</div>'
        f'<div class="region">{_esc(L["theme"])}: {_esc(theme_name)} · {_esc(L["range"])}: {_esc(sl["range_label"])}</div></div>'
        f'<div class="al-meta"><span>{_esc(L["cov"])}: <b>{_esc(sl["coverage_note"])}</b></span>'
        f'<span>{_esc(L["built"])}: {_esc(built)}</span><span>{_esc(L["model"])}: {_esc(model)}</span></div>'
        f'<div class="pf-note">{_esc(L["basis"])}</div>'
        + (f'<div class="al-notice">{_esc(L["notice"])}</div>' if theme in _INVEST else "")
        + f'<div class="pf-lead">{_linkify(doc.get("thesis") or "")}</div>'
        f'<h2>{_esc(L["map"])}</h2>{_map_svg(sl)}'
        f'<h2>{_esc(L["hubs"])}</h2><div class="al-hub">{hubs}</div>'
        f'<h2>{_esc(L["actors"])}</h2><div class="al-hub">{actors}</div>'
        f'<h2>{_esc(L["dyn"])}</h2>{dyn}'
        f'<h2>{_esc(L["act"])}</h2>{acts}'
        + (f'<h2>{_esc(L["gaps"])}</h2><ul class="clean">{gaps}</ul>' if gaps else "")
        + (f'<h2>{_esc(L["open"])}</h2><ul class="clean">{opens}</ul>' if opens else "")
        + f'<h2>{_esc(L["atoms"])} <span class="muted">{len(atom_ids)}</span></h2><div class="alt">{_atom_links(atom_ids)}</div>'
        f'<div class="foot">{i18n.report_strings(lang)["foot"]}</div>'
    )


def _slice_json(sl: dict, docs: dict[str, dict]) -> str:
    payload = {
        "schema": 1, "theme": sl["theme"], "range": sl["range"], "range_label": sl["range_label"],
        "built": sl.get("built"), "actors": sl["actors"], "edges": sl["edges"],
        "atom_ids": [a.id for a in sl["atoms"]], "hubs": sl["hubs"], "dropped": sl["dropped"],
        "langs": list(docs), "models": {lg: (d.get("_meta") or {}).get("model") for lg, d in docs.items()},
    }
    return json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")


def render(settings: Settings, sl: dict, docs: dict[str, dict]) -> dict:
    """언어별 doc → 한 HTML(언어 스위처) — docs/report/atlas/."""
    langs = list(docs)
    primary = langs[0]
    now = datetime.datetime.now()
    ymd, hms, stamp = now.strftime("%y%m%d"), now.strftime("%H%M%S"), now.strftime("%Y-%m-%d %H:%M")
    Lp = _labels(primary)
    title = docs[primary].get("title") or sl["range_label"]

    if len(langs) == 1:
        inner = f'<div class="wrap">{_section_html(docs[primary], sl, primary, stamp)}</div>'
        tail = ""
        css = _BASE_CSS + _ATLAS_CSS
    else:
        parts, btns = [], []
        for lg in langs:
            parts.append(f'<div class="wrap doc" id="doc-{_esc(lg)}" lang="{_esc(lg)}">'
                         f'<div class="doc-lang-tag">{_esc(i18n.native_name(lg))}</div>{_section_html(docs[lg], sl, lg, stamp)}</div>')
            btns.append(f'<button class="lb" data-target="doc-{_esc(lg)}" data-lang="{_esc(lg)}">{_esc(i18n.native_name(lg))}</button>')
        inner = f'<div class="langbar" id="langbar" hidden>{"".join(btns)}</div>' + "".join(parts)
        tail = _SWITCHER_JS
        css = _BASE_CSS + _SWITCHER_CSS + _ATLAS_CSS

    slice_tag = f'<script type="application/json" id="atlas-slice">{_slice_json(sl, docs)}</script>'
    html_doc = _document(primary, Lp["word"], title, css, inner + slice_tag, tail=tail)
    range_slug = _slug(sl["range_key"] or "world")[:32] or "world"
    fname = f"atlas_{sl['theme']}_{range_slug}_{ymd}_{hms}_{'-'.join(langs)}.html"
    out_dir = report_subdir(settings, "atlas")
    fname = _write_report_atomic(out_dir, fname, html_doc)
    return {"status": "OK", "file": fname, "url": f"/reports/{fname}", "path": str(out_dir / fname),
            "lang": primary, "langs": langs, "langName": " · ".join(i18n.native_name(lg) for lg in langs),
            "combined": len(langs) > 1, "atlas": True}


# ── 실행 ─────────────────────────────────────────────────────────
def run(settings: Settings, *, theme: str, range_spec: str = "world", langs: list[str] | None = None,
        on_progress=None, should_stop=None) -> dict:
    """테마 × 범위 → 어드바이저 보고서. 반환 {status, reports, cost_usd, slice, message}."""
    theme = (theme or "all").strip().lower()
    if theme not in THEMES and theme != "all":
        return {"status": "FAIL", "message": f"테마는 {', '.join(THEMES)} 또는 all — 받은 값 {theme!r}"}
    langs = [i18n.normalize(x) for x in (langs or ["ko"])] or ["ko"]
    emit = on_progress or (lambda *_: None)

    emit("slice", "아틀라스 조립·슬라이스 중…", 10)
    try:
        data = load_atlas()
        sl = slice_atlas(data, theme, range_spec, settings)
    except (ValueError, RuntimeError) as exc:
        return {"status": "FAIL", "message": str(exc)}
    if len(sl["actors"]) < 2 or not sl["atoms"]:
        return {"status": "FAIL", "message": f"슬라이스가 너무 작다 — {sl['coverage_note']}. 범위나 테마를 넓혀라.",
                "slice": {k: sl[k] for k in ("theme", "range", "coverage_note")}}
    emit("slice", f"슬라이스: {sl['coverage_note']}", 20)

    base = langs[0]
    emit("compose", f"관계 역학 서술 중… ({i18n.native_name(base)})", 30)
    try:
        doc, cost = compose(settings, sl, base, should_stop=should_stop)
    except llm.LLMCanceled:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"status": "FAIL", "message": f"서술 실패: {type(exc).__name__}: {exc}"}
    if not doc.get("thesis"):
        return {"status": "FAIL", "message": "모델이 서술을 내지 않았다", "cost_usd": round(cost, 4)}
    docs = {base: doc}
    errors = []
    for lg in langs[1:]:
        emit("translate", f"{i18n.native_name(lg)} 번역 중…", 60)
        try:
            tr, c = translate_doc(doc, lg, settings, base_lang=base)
            cost += c
            docs[lg] = tr
        except Exception as exc:  # noqa: BLE001
            errors.append({"lang": lg, "message": f"번역 실패: {type(exc).__name__}: {exc}"})
    emit("render", "보고서 작성 중…", 85)
    rep = render(settings, sl, docs)
    emit("done", f"완료 — {rep['file']}", 100)
    return {"status": "OK", "reports": [rep], "errors": errors, "cost_usd": round(cost, 4),
            "slice": {"theme": sl["theme"], "range": sl["range"], "range_label": sl["range_label"],
                      "actors": len(sl["actors"]), "edges": len(sl["edges"]), "atoms": len(sl["atoms"]),
                      "dropped": sl["dropped"]},
            "message": None}
