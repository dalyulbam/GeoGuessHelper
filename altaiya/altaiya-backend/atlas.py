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


def _median_anchor(coords: list[tuple[float, float]]) -> tuple[float, float] | None:
    """중앙값 앵커 — 평균과 달리 한쪽 끝의 원자 하나가 앵커를 끌고 가지 않는다."""
    if not coords:
        return None
    lats = sorted(c[0] for c in coords)
    lngs = sorted(c[1] for c in coords)
    return statistics.median(lats), statistics.median(lngs)


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
        if anchor is None:
            continue                     # 좌표 없는 행위자는 지도에 올릴 수 없다 (meta 에 집계)

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
            "lat": round(anchor[0], 5),
            "lng": round(anchor[1], 5),
            "scope": scope,
            "influence_km": _SCOPE_RADIUS.get(scope, 0),
            "atoms": atom_ids,
            "atom_count": len(my_atoms),
            "layers": layers,
            "themes": themes,
            "reports": sorted(reports),
            "period": [p_start, p_end] if p_start is not None else None,
            "degree": 0,                 # 아래 엣지 조립에서 채움
        }

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

    edge_list.sort(key=lambda e: -e["strength"])
    linked = {(e["src"], e["dst"]) for e in edge_list} | {(e["dst"], e["src"]) for e in edge_list}
    uncovered = sum(1 for pair in comention if pair not in linked)

    dropped = len(ent_index) - len(actors)
    return {
        "meta": {
            "built": time.time(),
            "atom_total": len(atoms),
            "actor_total": len(actors),
            "actor_dropped_no_coords": dropped,
            "edge_total": len(edge_list),
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
    }


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
