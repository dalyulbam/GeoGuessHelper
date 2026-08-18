"""지식 저장소(docs/knowledge) → 아틀라스 그래프 조립.

읽기 전용이다 — 원자·엔티티 파일을 절대 수정하지 않는다.
엣지는 원자의 엔티티 동시 서술에서만 만들고, 전 엣지가 출처 원자 id 를 인용한다
(인용 없는 엣지 금지 — 기획서 v2 원칙).
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

_SCOPE_ORDER = {"point": 0, "city": 1, "region": 2, "country": 3, "polity": 4, "period": 2, "global": 5}
# 렌더용 영향 반경(km) — knowledge.py 의 _SCOPE_RADIUS_KM 과 같은 눈금
_SCOPE_RADIUS = {"point": 1, "city": 25, "region": 200, "country": 1200, "polity": 2000, "period": 0, "global": 0}


def _load_index() -> dict:
    return json.loads((KNOW / "index.json").read_text(encoding="utf-8"))


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

    # ── 행위자 ──────────────────────────────────────────────────
    actors: dict[str, dict] = {}
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

        atype = rules.classify(slug)
        themes = {}
        for th in rules.THEMES:
            w = rules.THEME_MATRIX[th]
            score = sum(w.get(a.get("layer", ""), 0.0) for a in my_atoms)
            score += rules.TYPE_THEME_BOOST.get(atype, {}).get(th, 0.0)
            themes[th] = round(score, 2)

        actors[slug] = {
            "slug": slug,
            "label": rules.label_of(slug),
            "type": atype,
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

    # ── 엣지: 원자 하나가 행위자 2+ 를 함께 서술하면 그 사이를 잇는다 ──
    edges: dict[tuple[str, str], dict] = {}
    for aid, a in atoms.items():
        ents = [e for e in (a.get("entities") or []) if e in actors]
        if len(ents) < 2:
            continue
        for s, d in combinations(sorted(set(ents)), 2):
            key = (s, d)
            e = edges.get(key)
            if e is None:
                e = edges[key] = {"src": s, "dst": d, "atoms": [], "layers": {}}
            e["atoms"].append(aid)
            lay = a.get("layer", "?")
            e["layers"][lay] = e["layers"].get(lay, 0) + 1

    edge_list = []
    for (s, d), e in edges.items():
        dominant = max(e["layers"], key=e["layers"].get)
        themes = {}
        for th in rules.THEMES:
            w = rules.THEME_MATRIX[th]
            themes[th] = round(max(w.get(lay, 0.0) for lay in e["layers"]), 2)
        edge_list.append({
            "src": s, "dst": d,
            "atoms": e["atoms"],
            "strength": len(e["atoms"]),
            "layer": dominant,
            "layers": e["layers"],
            "themes": themes,
        })
        actors[s]["degree"] += 1
        actors[d]["degree"] += 1

    edge_list.sort(key=lambda e: -e["strength"])

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
            "note": "엣지 = 원자 동시 서술(전 엣지 원자 인용). 타입·테마는 휴리스틱 근사 — LLM 정밀 추출(기획서 패스 A·B)은 후속.",
        },
        "actors": sorted(actors.values(), key=lambda x: -x["atom_count"]),
        "edges": edge_list,
    }


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
