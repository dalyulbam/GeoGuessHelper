"""지식 저장소(docs/knowledge) → 아틀라스 그래프 조립.

읽기 전용이다 — 원자·엔티티 파일을 절대 수정하지 않는다.
전 엣지가 출처 원자 id 를 인용한다(인용 없는 엣지 금지 — 기획서 v2 원칙).

재료는 두 층이다.
  1. 사이드카 — `altaiya/data/entities.json`(패스A 대장) · `relations.json`(패스B 관계).
     있으면 이쪽이 정본이다: 타입·한국어 표기는 대장에서, 엣지는 유형·방향이 붙은 관계에서 온다.
  2. 휴리스틱 폴백 — 사이드카가 없거나 아직 안 다룬 슬러그는 `rules.py` 의 근사와
     원자 동시 서술로 채운다. meta.source 가 어느 쪽인지 밝힌다.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from itertools import combinations
from pathlib import Path
from typing import Any

import rules

ROOT = Path(__file__).resolve().parents[2]          # E:\GeoguessHelper
KNOW = ROOT / "docs" / "knowledge"
REPORT_DIR = ROOT / "docs" / "report"
DATA = Path(__file__).resolve().parents[1] / "data"  # 패스A·B 사이드카
ENTITIES_JSON = DATA / "entities.json"
RELATIONS_JSON = DATA / "relations.json"
PERIODS_JSON = DATA / "periods.json"
TERRITORY_DIR = DATA / "territories"                 # 손으로 그린 강역 — 행위자당 GeoJSON 한 파일

# 관계 유형 표기 — 뷰어가 그대로 쓴다(백엔드가 유일한 어휘 출처)
REL_KO = {
    "operates": "운영", "supplies": "공급", "produces": "생산", "connects": "연결",
    "ruled": "통치", "succeeded": "계승", "founded": "후원", "opposed": "대립",
    "part-of": "소속", "related": "관련",
}

_SCOPE_ORDER = {"point": 0, "city": 1, "region": 2, "country": 3, "polity": 4, "period": 2, "global": 5}
# 렌더용 영향 반경(km) — knowledge.py 의 _SCOPE_RADIUS_KM 과 같은 눈금
_SCOPE_RADIUS = {"point": 1, "city": 25, "region": 200, "country": 1200, "polity": 2000, "period": 0, "global": 0}


def _load_index() -> dict:
    return json.loads((KNOW / "index.json").read_text(encoding="utf-8"))


def _load_sidecar(p: Path, key: str, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8")).get(key, default)
    except (ValueError, OSError):
        return default


# ── 국가 폴리곤 앵커 ────────────────────────────────────────────
# 원자 확장(패스D)이 만든 세계 패턴 원자는 scope=global 이라 좌표가 없다. 그 바람에
# "프랑스"·"인도네시아" 같은 **실재하는 나라**까지 앵커를 못 받아 지도에서 빠졌다.
# LLM 지오코딩은 여전히 금지다(없는 좌표를 지어내는 순간 아틀라스가 거짓말을 한다).
# 대신 뷰어가 이미 쓰는 Natural Earth 해안선(퍼블릭 도메인)의 국가 폴리곤에서
# 중심을 계산해 붙인다 — 결정론적이고, 출처가 분명하고, 재현된다.
WORLD_GEOJSON = Path(__file__).resolve().parents[1] / "altaiya-frontend" / "world.geo.json"

# 저장소 슬러그와 Natural Earth 표기가 다른 것들만 손으로 맞춘다
_COUNTRY_ALIAS = {
    "united-states": "United States of America", "usa": "United States of America",
    "america": "United States of America", "uk": "United Kingdom",
    "britain": "United Kingdom", "great-britain": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "russia": "Russia", "south-korea": "South Korea", "north-korea": "North Korea",
    "czechia": "Czech Republic", "czech-republic": "Czech Republic",
    "turkiye": "Turkey", "netherlands": "Netherlands", "bosnia": "Bosnia and Herzegovina",
    "macedonia": "Macedonia", "north-macedonia": "Macedonia", "myanmar": "Myanmar",
    "ivory-coast": "Ivory Coast", "drc": "Democratic Republic of the Congo",
}

_country_cache: dict[str, tuple[float, float]] | None = None


def _ring_centroid(ring: list) -> tuple[float, float, float]:
    """폴리곤 링의 면적과 무게중심(x,y). 면적은 부호 없는 값."""
    a = cx = cy = 0.0
    n = len(ring)
    for i in range(n - 1):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[i + 1][0], ring[i + 1][1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return 0.0, ring[0][0], ring[0][1]
    return abs(a / 2.0), cx / (3.0 * a), cy / (3.0 * a)


def _country_anchors() -> dict[str, tuple[float, float]]:
    """국가명 슬러그 → (lat, lng). 본토(가장 큰 링)의 중심을 쓴다 —
    미국·프랑스처럼 멀리 떨어진 해외 영토가 있는 나라는 평균을 내면 바다에 찍힌다."""
    global _country_cache
    if _country_cache is not None:
        return _country_cache
    out: dict[str, tuple[float, float]] = {}
    try:
        geo = json.loads(WORLD_GEOJSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _country_cache = out
        return out
    for f in geo.get("features") or []:
        name = ((f.get("properties") or {}).get("name") or "").strip()
        geom = f.get("geometry") or {}
        polys = (geom.get("coordinates") or []) if geom.get("type") == "MultiPolygon" \
            else [geom.get("coordinates") or []] if geom.get("type") == "Polygon" else []
        best = (0.0, None)
        for poly in polys:
            if not poly:
                continue
            area, x, y = _ring_centroid(poly[0])
            if area > best[0]:
                best = (area, (y, x))          # GeoJSON 은 [lng, lat] 순서다
        if name and best[1]:
            out[re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")] = best[1]
    _country_cache = out
    return out


def _country_anchor(slug: str, label_en: str) -> tuple[float, float] | None:
    idx = _country_anchors()
    for key in (slug, re.sub(r"[^a-z0-9]+", "-", (label_en or "").lower()).strip("-")):
        if not key:
            continue
        if key in idx:
            return idx[key]
        alias = _COUNTRY_ALIAS.get(key)
        if alias:
            a = re.sub(r"[^a-z0-9]+", "-", alias.lower()).strip("-")
            if a in idx:
                return idx[a]
    return None


def _median_anchor(coords: list[tuple[float, float]]) -> tuple[float, float] | None:
    """중앙값 앵커 — 평균과 달리 한쪽 끝의 원자 하나가 앵커를 끌고 가지 않는다."""
    if not coords:
        return None
    lats = sorted(c[0] for c in coords)
    lngs = sorted(c[1] for c in coords)
    return statistics.median(lats), statistics.median(lngs)


# ── 손으로 그린 강역 (territory-sketch 기획, 260826) ──────────────
# 강역은 원자에서 유도된 사실이 아니라 **서명된 주장**이다 — 그린 이·날짜·근거(basis)·
# 시기가 판본(claim)마다 붙고, 뷰어는 점선·✍ 로 원자 데이터와 절대 섞지 않는다.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")


def _load_territories() -> dict[str, dict]:
    """territories/*.geojson → slug → {label, features}. 깨진 파일은 건너뛰되 meta 에 남긴다."""
    out: dict[str, dict] = {}
    if not TERRITORY_DIR.exists():
        return out
    for p in sorted(TERRITORY_DIR.glob("*.geojson")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            slug = d.get("slug") or p.stem
            if not _SLUG_RE.match(slug):
                continue
            feats = [f for f in (d.get("features") or [])
                     if isinstance(f, dict) and (f.get("geometry") or {}).get("coordinates")]
            if feats:
                out[slug] = {"slug": slug, "label": d.get("label") or slug,
                             "rev": d.get("rev") or 1, "features": feats}
        except (ValueError, OSError):
            continue
    return out


def _territory_polys(feats: list[dict]) -> list[list]:
    """Feature 들의 폴리곤 목록(MultiPolygon 평탄화). 각 항목 = [외곽링, 구멍…]."""
    polys: list[list] = []
    for f in feats:
        g = f.get("geometry") or {}
        c = g.get("coordinates") or []
        if g.get("type") == "Polygon":
            polys.append(c)
        elif g.get("type") == "MultiPolygon":
            polys.extend(c)
    return polys


def _territory_anchor(feats: list[dict]) -> tuple[float, float] | None:
    """전 판본을 통틀어 가장 큰 외곽링의 중심 — 국가 폴리곤 앵커와 같은 결정론적 계산."""
    best = (0.0, None)
    for poly in _territory_polys(feats):
        if not poly:
            continue
        area, x, y = _ring_centroid(poly[0])
        if area > best[0]:
            best = (area, (y, x))               # GeoJSON 은 [lng, lat] 순서다
    return best[1]


def build() -> dict[str, Any]:
    idx = _load_index()
    atoms: dict[str, dict] = idx.get("atoms") or {}
    ent_index: dict[str, list[str]] = idx.get("entities") or {}
    ledger: dict[str, dict] = _load_sidecar(ENTITIES_JSON, "actors", {})
    rel_side: list[dict] = _load_sidecar(RELATIONS_JSON, "relations", [])
    spheres: dict[str, dict] = _load_sidecar(PERIODS_JSON, "spheres", {})
    atom_era: dict[str, dict] = _load_sidecar(PERIODS_JSON, "atom_era", {})

    # ── 행위자 ──────────────────────────────────────────────────
    actors: dict[str, dict] = {}
    from_ledger = 0
    for slug, atom_ids in ent_index.items():
        my_atoms = [atoms[a] for a in atom_ids if a in atoms]
        if not my_atoms:
            continue
        coords = [(a["lat"], a["lng"]) for a in my_atoms
                  if isinstance(a.get("lat"), (int, float)) and isinstance(a.get("lng"), (int, float))]
        anchor = _median_anchor(coords)
        anchor_from = "원자 좌표 중앙값" if anchor else None
        if anchor is None:
            # 나라는 무장소가 아니다 — 원자가 전부 scope=global 이라 좌표를 못 받았을 뿐이다.
            # 뷰어가 쓰는 Natural Earth 폴리곤에서 본토 중심을 계산해 붙인다(지오코딩 아님).
            led0 = ledger.get(slug) or {}
            anchor = _country_anchor(slug, led0.get("label_en") or "")
            if anchor:
                anchor_from = "국가 폴리곤 중심(Natural Earth)"
        # 그래도 좌표가 없으면 버리지 않는다 — 원자 확장(패스D)이 만든 세계 패턴·인물·
        # 반대급부는 본래 무장소다("소비에트 조립식 주거 가족", "포드주의"). 예전에는 여기서
        # continue 로 통째로 사라져 백과에서도 검색되지 않았다. 이제 placeless 로 그래프에
        # 남기고, 지도에는 올리지 않되 백과·검색·관계 목록에서는 1급 시민으로 다룬다.
        placeless = anchor is None

        layers: dict[str, int] = {}
        reports: set[str] = set()
        p_start, p_end = None, None
        scope = "point"
        for a in my_atoms:
            layers[a.get("layer", "?")] = layers.get(a.get("layer", "?"), 0) + 1
            reports.update(a.get("reports") or [])
            if a.get("period_start") is not None:
                p_start = a["period_start"] if p_start is None else min(p_start, a["period_start"])
                pe = a.get("period_end")   # end 없음 = 현재 진행
                p_end = None if pe is None else (pe if p_end is None else max(p_end, pe))
            if _SCOPE_ORDER.get(a.get("scope", "point"), 0) > _SCOPE_ORDER.get(scope, 0):
                scope = a.get("scope", "point")

        # 대장(패스A)이 정본, 없으면 슬러그 휴리스틱
        led = ledger.get(slug) or {}
        atype = led.get("type") or rules.classify(slug)
        if led:
            from_ledger += 1
        themes = {}
        for th in rules.THEMES:
            w = rules.THEME_MATRIX[th]
            score = sum(w.get(a.get("layer", ""), 0.0) for a in my_atoms)
            score += rules.TYPE_THEME_BOOST.get(atype, {}).get(th, 0.0)
            themes[th] = round(score, 2)

        # 시대 층위 — 이 행위자의 원자가 어느 층에 앉아 있나(사료권마다 눈금이 다르다)
        eras: dict[str, int] = {}
        sph: dict[str, int] = {}
        for a in my_atoms:
            ae = atom_era.get(a["id"])
            if not ae:
                continue
            eras[ae["era"]] = eras.get(ae["era"], 0) + 1
            sph[ae["sphere"]] = sph.get(ae["sphere"], 0) + 1

        actors[slug] = {
            "slug": slug,
            "sphere": (max(sph, key=sph.get) if sph else None),
            "eras": eras,
            "label": led.get("label_ko") or rules.label_of(slug),
            "label_en": led.get("label_en") or rules.label_of(slug),
            "basis": led.get("basis") or None,
            "type": atype,
            "typed_by": "ledger" if led else "heuristic",
            "placeless": placeless,
            "anchor_from": anchor_from,
            "lat": None if placeless else round(anchor[0], 5),
            "lng": None if placeless else round(anchor[1], 5),
            "scope": scope,
            "influence_km": 0 if placeless else _SCOPE_RADIUS.get(scope, 0),
            "atoms": atom_ids,
            "atom_count": len(my_atoms),
            "layers": layers,
            "themes": themes,
            "reports": sorted(reports),
            "period": [p_start, p_end] if p_start is not None else None,
            "degree": 0,                 # 아래 엣지 조립에서 채움
        }

    # ── 손으로 그린 강역 조인 ───────────────────────────────────
    territories = _load_territories()
    for t_slug, terr in territories.items():
        feats = terr["features"]
        # 판본 시기의 합집합 — 강역 전용 행위자의 period 이자, 시간 렌즈의 재료
        t_start, t_end, open_end = None, None, False
        for f in feats:
            per = (f.get("properties") or {}).get("period")
            if isinstance(per, list) and per and per[0] is not None:
                t_start = per[0] if t_start is None else min(t_start, per[0])
                if per[1] is None or len(per) < 2:
                    open_end = True
                elif not open_end:
                    t_end = per[1] if t_end is None else max(t_end, per[1])
        if t_slug not in actors:
            # 강역 전용 행위자 — 원자가 아직 없는 대상(청나라·한나라)도 강역이 먼저 올 수
            # 있다. 원자 0 을 숨기지 않는다 — 훗날 원자가 쌓이면 같은 슬러그로 합쳐진다.
            actors[t_slug] = {
                "slug": t_slug, "sphere": None, "eras": {},
                "label": terr["label"], "label_en": terr["label"],
                "basis": "손으로 그린 강역만 있음 — 원자 없음",
                "type": "polity", "typed_by": "territory",
                "placeless": True, "anchor_from": None, "lat": None, "lng": None,
                "scope": "polity", "influence_km": 0,
                "atoms": [], "atom_count": 0, "layers": {},
                # 강역 전용 행위자는 역사·기록 테마에만 산다 — 다른 테마 점수를 지어내지 않는다
                "themes": {th: (1.0 if th == "heritage" else 0.0) for th in rules.THEMES},
                "reports": [],
                "period": [t_start, t_end if not open_end else None] if t_start is not None else None,
                "degree": 0,
            }
        a = actors[t_slug]
        a["territory"] = {"claims": len(feats), "rev": terr["rev"]}
        if a.get("period") is None and t_start is not None:
            a["period"] = [t_start, t_end if not open_end else None]
        # 앵커 승격 — 무장소 행위자가 강역을 얻으면 본토 최대 링의 중심으로 지도에 선다.
        # 사람이 그린 다각형에서 나온 결정론적 계산이라 지오코딩 금지 원칙을 어기지 않는다.
        if a["placeless"]:
            anc = _territory_anchor(feats)
            if anc:
                a["placeless"] = False
                a["anchor_from"] = "손으로 그린 강역 중심"
                a["lat"], a["lng"] = round(anc[0], 5), round(anc[1], 5)

    # ── 동시 서술 쌍 ────────────────────────────────────────────
    # 관계 사이드카가 없을 때의 폴백이자, 있을 때는 커버리지의 분모다
    # ("함께 서술됐지만 관계로 인정되지 않은 쌍"이 몇 개인지 정직하게 밝힌다).
    comention: dict[tuple[str, str], dict] = {}
    for aid, a in atoms.items():
        ents = [e for e in (a.get("entities") or []) if e in actors]
        if len(ents) < 2:
            continue
        for s, d in combinations(sorted(set(ents)), 2):
            e = comention.setdefault((s, d), {"src": s, "dst": d, "atoms": [], "layers": {}})
            e["atoms"].append(aid)
            lay = a.get("layer", "?")
            e["layers"][lay] = e["layers"].get(lay, 0) + 1

    def _layer_themes(layers: dict[str, int]) -> dict[str, float]:
        return {th: round(max(rules.THEME_MATRIX[th].get(lay, 0.0) for lay in layers), 2)
                for th in rules.THEMES}

    edge_list: list[dict] = []
    if rel_side:
        # 패스B 관계 — 유형·방향·시기가 붙어 있다
        for r in rel_side:
            s, d = r.get("src"), r.get("dst")
            if s not in actors or d not in actors:
                continue
            layers = dict(r.get("layers") or {})
            if not layers:
                for aid in r.get("atoms") or []:
                    lay = (atoms.get(aid) or {}).get("layer", "?")
                    layers[lay] = layers.get(lay, 0) + 1
            themes = _layer_themes(layers or {"?": 1})
            for th in (r.get("themes") or []):      # 유형이 지정한 주 테마는 반드시 남긴다
                themes[th] = max(themes.get(th, 0.0), 1.0)
            rel = r.get("type", "related")
            e_eras: dict[str, int] = {}
            e_sph: dict[str, int] = {}
            for aid in (r.get("atoms") or []):
                ae = atom_era.get(aid)
                if ae:
                    e_eras[ae["era"]] = e_eras.get(ae["era"], 0) + 1
                    e_sph[ae["sphere"]] = e_sph.get(ae["sphere"], 0) + 1
            edge_list.append({
                "eras": e_eras,
                "sphere": (max(e_sph, key=e_sph.get) if e_sph else None),
                "src": s, "dst": d,
                "rel": rel, "rel_ko": REL_KO.get(rel, "관련"),
                "dir": r.get("dir", "->"),
                "period": r.get("period"),
                "atoms": r.get("atoms") or [],
                "strength": r.get("strength") or len(r.get("atoms") or []),
                "layer": r.get("layer") or max(layers, key=layers.get),
                "layers": layers,
                "themes": themes,
            })
    else:
        for (s, d), e in comention.items():
            edge_list.append({
                "src": s, "dst": d,
                "rel": "comention", "rel_ko": "동시 서술", "dir": "<->", "period": None,
                "eras": {}, "sphere": None,
                "atoms": e["atoms"],
                "strength": len(e["atoms"]),
                "layer": max(e["layers"], key=e["layers"].get),
                "layers": e["layers"],
                "themes": _layer_themes(e["layers"]),
            })

    for e in edge_list:
        actors[e["src"]]["degree"] += 1
        actors[e["dst"]]["degree"] += 1
        # 양끝이 다 좌표를 가져야 선을 그을 수 있다. 무장소 행위자가 낀 관계는 실재하지만
        # 지도에 선으로 표현할 수 없다 — 버리지 말고 외교 화면·백과에서만 쓰도록 표시한다.
        e["mappable"] = not (actors[e["src"]]["placeless"] or actors[e["dst"]]["placeless"])

    edge_list.sort(key=lambda e: -e["strength"])
    linked = {(e["src"], e["dst"]) for e in edge_list} | {(e["dst"], e["src"]) for e in edge_list}
    uncovered = sum(1 for pair in comention if pair not in linked)

    placeless_n = sum(1 for a in actors.values() if a["placeless"])
    unmappable_n = sum(1 for e in edge_list if not e["mappable"])
    return {
        "meta": {
            "built": time.time(),
            "territory_actors": len(territories),
            "territory_claims": sum(len(t["features"]) for t in territories.values()),
            "atom_total": len(atoms),
            "actor_total": len(actors),
            "actor_mapped": len(actors) - placeless_n,
            "actor_placeless": placeless_n,
            "edge_total": len(edge_list),
            "edge_mappable": len(edge_list) - unmappable_n,
            "edge_placeless": unmappable_n,
            "themes": list(rules.THEMES),
            "edge_theme_min": rules.EDGE_THEME_MIN,
            "actor_theme_min": rules.ACTOR_THEME_MIN,
            "source": {
                "actors": "ledger" if ledger else "heuristic",
                "actors_from_ledger": from_ledger,
                "actors_heuristic": len(actors) - from_ledger,
                "edges": "relations" if rel_side else "comention",
            },
            "spheres_total": len(spheres),
            "atom_era_assigned": len(atom_era),
            "comention_pairs": len(comention),
            "comention_uncovered": uncovered,
            "rel_types": sorted({e["rel"] for e in edge_list}),
            "note": ("엣지 = 패스B 관계(유형·방향·인용 원자). 함께 서술됐지만 관계로 인정되지 않은 "
                     f"쌍 {uncovered}개는 그리지 않는다 — 공백은 '관계 없음'이 아니라 '아직 근거 없음'이다."
                     if rel_side else
                     "엣지 = 원자 동시 서술(전 엣지 원자 인용). 관계 유형은 패스B 를 돌리면 붙는다."),
        },
        "spheres": sorted(spheres.values(), key=lambda s: -s.get("atom_count", 0)),
        "actors": sorted(actors.values(), key=lambda x: -x["atom_count"]),
        "edges": edge_list,
        "territories": sorted(territories.values(), key=lambda t: t["slug"]),
    }


def save_territory(slug: str, payload: dict) -> tuple[dict | None, str | None, int]:
    """강역 저장 — 백엔드 유일의 쓰기 경로. 반환 (저장 결과, 오류, HTTP 상태).

    검증이 곧 원칙이다: 링 3점 미만 거부, 근거(basis) 없는 판본 거부, rev 가 낮으면
    409(다른 창에서 먼저 저장했다 — 덮어쓰지 않는다). features 가 비면 파일을 지운다.
    """
    if not _SLUG_RE.match(slug or ""):
        return None, "잘못된 슬러그", 400
    if not isinstance(payload, dict):
        return None, "본문이 JSON 객체가 아니다", 400
    p = TERRITORY_DIR / f"{slug}.geojson"
    cur_rev = 0
    if p.exists():
        try:
            cur_rev = json.loads(p.read_text(encoding="utf-8")).get("rev") or 1
        except (ValueError, OSError):
            cur_rev = 1
    base = payload.get("rev") or 0
    if cur_rev and base != cur_rev:
        return None, f"rev 충돌 — 파일은 {cur_rev}, 요청은 {base}. 다시 불러와 병합하라.", 409

    feats_in = payload.get("features")
    if not isinstance(feats_in, list):
        return None, "features 배열이 없다", 400
    if not feats_in:                      # 판본 전부 삭제 = 강역 제거
        if p.exists():
            p.unlink()
        return {"slug": slug, "deleted": True}, None, 200

    feats_out: list[dict] = []
    for i, f in enumerate(feats_in):
        g = (f or {}).get("geometry") or {}
        props = (f or {}).get("properties") or {}
        gtype = g.get("type")
        coords = g.get("coordinates")
        if gtype == "Polygon":
            polys = [coords]
        elif gtype == "MultiPolygon":
            polys = coords
        else:
            return None, f"판본 {i + 1}: geometry 는 Polygon/MultiPolygon 이어야 한다", 400
        clean_polys = []
        for poly in polys or []:
            clean_rings = []
            for ring in poly or []:
                pts = []
                for pt in ring or []:
                    try:
                        lng, lat = float(pt[0]), float(pt[1])
                    except (TypeError, ValueError, IndexError):
                        return None, f"판본 {i + 1}: 좌표가 숫자가 아니다", 400
                    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                        return None, f"판본 {i + 1}: 좌표 범위 밖 ({lng}, {lat})", 400
                    pts.append([round(lng, 4), round(lat, 4)])
                if pts and pts[0] != pts[-1]:
                    pts.append(list(pts[0]))          # GeoJSON 링은 닫혀 있어야 한다
                if len(pts) < 4:                       # 닫힘 포함 4 = 실꼭짓점 3
                    return None, f"판본 {i + 1}: 링은 꼭짓점 3개 이상이어야 한다", 400
                clean_rings.append(pts)
            if clean_rings:
                clean_polys.append(clean_rings)
        if not clean_polys:
            return None, f"판본 {i + 1}: 링이 없다", 400
        claim = str(props.get("claim") or "").strip()
        basis = str(props.get("basis") or "").strip()
        if not claim:
            return None, f"판본 {i + 1}: 판본 이름(claim)이 없다", 400
        if not basis:
            return None, f"판본 {i + 1}: 근거(basis)가 없다 — 한 줄이라도 남겨라", 400
        per = props.get("period")
        if per is not None:
            if not (isinstance(per, list) and len(per) == 2
                    and all(v is None or isinstance(v, int) for v in per)):
                return None, f"판본 {i + 1}: period 는 [시작, 끝] 정수(또는 null)여야 한다", 400
        feats_out.append({
            "type": "Feature",
            "geometry": {"type": "MultiPolygon", "coordinates": clean_polys},
            "properties": {
                "claim": claim[:60], "period": per, "basis": basis[:500],
                "disputed": bool(props.get("disputed")),
                "drawn_by": str(props.get("drawn_by") or "user")[:40],
                "drawn_at": str(props.get("drawn_at") or time.strftime("%Y-%m-%d"))[:10],
            },
        })

    TERRITORY_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"type": "FeatureCollection", "slug": slug,
           "label": str(payload.get("label") or slug)[:80],
           "rev": cur_rev + 1, "features": feats_out}
    tmp = p.with_suffix(".geojson.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return doc, None, 200


def era_index() -> dict[str, dict]:
    """원자 → 시대 층 배정(패스C). 상세 패널이 원자마다 시대를 붙일 때 쓴다."""
    return _load_sidecar(PERIODS_JSON, "atom_era", {})


def sphere_index() -> dict[str, dict]:
    return _load_sidecar(PERIODS_JSON, "spheres", {})


# ── 상세 조회 (지연 로드) ────────────────────────────────────────
def atom_detail(atom_id: str) -> dict | None:
    """원자 md 의 JSON 프론트매터를 읽는다 — body 는 index.json 에 없다."""
    p = KNOW / "atoms" / f"{atom_id}.md"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    try:
        _, fm, _ = text.split("---", 2)
        return json.loads(fm)
    except (ValueError, json.JSONDecodeError):
        return None


def find_report(name: str) -> Path | None:
    """보고서는 하위 폴더(크로아시아 등)에 있을 수 있다 — 재귀로 찾는다."""
    if not name.endswith(".html") or "/" in name or "\\" in name or ".." in name:
        return None
    direct = REPORT_DIR / name
    if direct.exists():
        return direct
    for p in REPORT_DIR.rglob(name):
        return p
    return None
