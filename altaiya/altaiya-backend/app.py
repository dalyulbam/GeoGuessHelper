"""ALTAIYA 백엔드 — 아틀라스 그래프 API (읽기 전용).

포트 8901 (env ALTAIYA_API_PORT). 프론트(8902)와 분리된 두 번째 서버다.
본체 GeoGuessHelper 서버(8777대)와 독립 — docs/knowledge 를 읽기만 한다.
실행: uv run --project E:\\GeoguessHelper python altaiya\\altaiya-backend\\app.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

import atlas

app = FastAPI(title="ALTAIYA", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # 로컬 도구 — 프론트가 다른 포트에서 fetch 한다
    allow_methods=["GET"],
    allow_headers=["*"],
)

_ATLAS: dict = {}


def _atlas() -> dict:
    global _ATLAS
    if not _ATLAS:
        _ATLAS = atlas.build()
    return _ATLAS


@app.get("/api/health")
def health():
    return {"ok": True, "service": "altaiya"}


@app.get("/api/atlas")
def get_atlas():
    return JSONResponse(_atlas())


@app.get("/api/rebuild")
def rebuild():
    """지식이 늘었을 때(새 보고서 → 원자) 다시 조립한다."""
    global _ATLAS
    _ATLAS = atlas.build()
    return {"ok": True, "meta": _ATLAS["meta"]}


@app.get("/api/actor/{slug}")
def actor_detail(slug: str):
    data = _atlas()
    actor = next((a for a in data["actors"] if a["slug"] == slug), None)
    if actor is None:
        return JSONResponse({"error": "unknown actor"}, status_code=404)
    relations = []
    for e in data["edges"]:
        if slug in (e["src"], e["dst"]):
            other = e["dst"] if e["src"] == slug else e["src"]
            other_a = next((a for a in data["actors"] if a["slug"] == other), None)
            relations.append({**e, "other": other,
                              "other_label": other_a["label"] if other_a else other,
                              "other_type": other_a["type"] if other_a else "place"})
    relations.sort(key=lambda r: -r["strength"])
    atoms = [a for a in (atlas.atom_detail(aid) for aid in actor["atoms"]) if a]
    eras = atlas.era_index()
    spheres = atlas.sphere_index()

    def era_of(aid: str) -> dict | None:
        """원자가 앉은 시대 층 — 사료권마다 눈금이 달라서 층 라벨을 함께 실어 보낸다."""
        ae = eras.get(aid)
        if not ae:
            return None
        sp = spheres.get(ae["sphere"]) or {}
        st = next((x for x in (sp.get("strata") or []) if x["key"] == ae["era"]), None)
        return {"sphere": ae["sphere"], "sphere_label": sp.get("label_ko"),
                "era": ae["era"], "by": ae.get("by"),
                "label": (st or {}).get("label_ko") or ("무시기" if ae["era"] == "atemporal" else ae["era"]),
                "span": [(st or {}).get("start"), (st or {}).get("end")] if st else None}

    sphere = spheres.get(actor.get("sphere") or "")
    return {"actor": actor, "relations": relations, "sphere": sphere,
            "atoms": [{"id": a["id"], "title": a.get("title"), "layer": a.get("layer"),
                       "scope": a.get("scope"), "body": a.get("body"),
                       "period": [a.get("period_start"), a.get("period_end")],
                       "era": era_of(a["id"]),
                       "reports": a.get("reports") or [], "sources": a.get("sources") or []}
                      for a in atoms]}


@app.get("/api/spheres")
def spheres():
    """사료권과 그 시대 층위 — 뷰어의 시대 독이 쓴다."""
    return {"spheres": _atlas().get("spheres") or []}


@app.get("/api/placeless")
def placeless():
    """무장소 행위자 — 지도에 앵커가 없는 것들(세계 패턴·인물·반대급부).

    패스D(원자 전방위 확장)가 만든 "소비에트 조립식 주거 가족" 같은 개념은 한 좌표에
    속하지 않는다. 지도에는 못 올리지만 백과·검색·관계에서는 1급이라, 뷰어가 따로
    묶어 보여줄 수 있도록 축(worldwide/people/counterpart)별로 갈라 싣는다.
    """
    data = _atlas()
    by_axis: dict[str, list] = {"worldwide": [], "people": [], "counterpart": [], "other": []}
    idx = atlas._load_index()
    atoms = idx.get("atoms") or {}
    for a in data["actors"]:
        if not a.get("placeless"):
            continue
        tags: set[str] = set()
        for aid in a["atoms"]:
            tags.update((atoms.get(aid) or {}).get("tags") or [])
        axis = ("people" if a["type"] == "person" else
                "worldwide" if "x-worldwide" in tags else
                "counterpart" if "x-counterpart" in tags else
                "people" if "x-people" in tags else "other")
        by_axis[axis].append({k: a[k] for k in
                              ("slug", "label", "label_en", "type", "scope",
                               "atom_count", "degree", "themes", "period")})
    for v in by_axis.values():
        v.sort(key=lambda x: (-x["degree"], -x["atom_count"]))
    return {"total": sum(len(v) for v in by_axis.values()), "axes": by_axis}


@app.get("/api/atom/{atom_id}")
def atom(atom_id: str):
    a = atlas.atom_detail(atom_id)
    if a is None:
        return JSONResponse({"error": "unknown atom"}, status_code=404)
    return a


@app.get("/reports/{name}")
def report_file(name: str):
    p = atlas.find_report(name)
    if p is None:
        return PlainTextResponse("report not found", status_code=404)
    return FileResponse(p, media_type="text/html")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("ALTAIYA_API_PORT", "8901"))
    print(f"\n  == ALTAIYA backend ==============================")
    print(f"   API      : http://localhost:{port}/api/atlas")
    print(f"   지식     : {atlas.KNOW}")
    meta = _atlas()["meta"]
    print(f"   조립     : 행위자 {meta['actor_total']} · 관계 {meta['edge_total']} · 원자 {meta['atom_total']}")
    print(f"  =================================================\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
