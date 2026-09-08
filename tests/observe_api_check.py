"""관측소 백엔드 검증 — `uv run python tests/observe_api_check.py` (명세 §6.5).

altaiya 백엔드 앱을 TestClient 로 띄워 /api/observe/* 라우트 전부가 200(또는 명시된 404 JSON)을 내고,
계기판 숫자가 index.json 을 직접 집계한 값과 1의 오차도 없이 같은지(V2) 확인한다.
사이드카(observe/·corrections/)가 없어도 500 이 나면 안 된다 — 있으면 있는 대로, 없으면 빈 구조.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "altaiya" / "altaiya-backend"))

from fastapi.testclient import TestClient  # noqa: E402

import app as altaiya_app  # noqa: E402
import atlas  # noqa: E402

KNOW = atlas.KNOW
client = TestClient(altaiya_app.app)
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ok  " if cond else "  FAIL") + " " + msg)
    if not cond:
        fails.append(msg)


def get(path: str, expect=(200,)):
    t = time.time()
    r = client.get(path)
    dt = (time.time() - t) * 1000
    body = None
    try:
        body = r.json()
    except ValueError:
        pass
    check(r.status_code in expect and body is not None, f"{r.status_code} {len(r.content):>8}B {dt:6.0f}ms {path}")
    return r.status_code, body


print(f"지식: {KNOW}")
idx = json.loads((KNOW / "index.json").read_text(encoding="utf-8"))
atoms = idx.get("atoms") or {}
n_atoms = len(atoms)
n_coords = sum(1 for a in atoms.values() if isinstance(a.get("lat"), (int, float)) and isinstance(a.get("lng"), (int, float)))
n_cat = sum(1 for a in atoms.values() if a.get("category"))
by_origin: dict[str, int] = {}
for a in atoms.values():
    o = a.get("origin") or ("report" if a.get("reports") else "expansion" if any(str(t).startswith("x-") for t in a.get("tags") or []) else "wiki")
    by_origin[o] = by_origin.get(o, 0) + 1
have_observe = (KNOW / "observe" / "atom_meta.json").exists()
have_corr = (KNOW / "corrections" / "corrections.jsonl").exists()
print(f"index 원자 {n_atoms} · 좌표 {n_coords} · 범주 {n_cat} · 경로 {by_origin} · observe {have_observe} · corrections {have_corr}\n")

print("1) 라우트 전부 200 / 404-JSON")
_, summary = get("/api/observe/summary")
_, series = get("/api/observe/series")
_, atoms_res = get("/api/observe/atoms")
first_id = next(iter(atoms))
_, atom = get(f"/api/observe/atom/{first_id}")
get("/api/observe/atom/atm_000000000000", expect=(404,))
get("/api/observe/atom/../etc", expect=(404,))
st_layout, _ = get("/api/observe/layout", expect=(200, 404))
_, graph = get("/api/observe/graph")
_, graph15 = get("/api/observe/graph?k=15")
_, mols = get("/api/observe/molecules")
mol_id = (mols.get("molecules") or [{}])[0].get("id")
if mol_id:
    _, mol = get(f"/api/observe/molecule/{mol_id}")
else:
    mol = None
    print("  skip 분자 없음")
get("/api/observe/molecule/mol_000000000000", expect=(404,))
_, corr = get("/api/observe/corrections")
corr_jobs = corr.get("jobs") or []
if corr_jobs:
    get(f"/api/observe/correction/{corr_jobs[0]}")
get("/api/observe/correction/nope", expect=(404,))
_, queues = get("/api/observe/queues")
_, recall = get("/api/observe/recall_log?limit=20")

print("\n2) 정합(V2) — 계기판 숫자 = index.json 직접 집계")
c = summary["counts"]
check(c["atoms"] == n_atoms, f"summary.counts.atoms {c['atoms']} == index {n_atoms}")
check(c["with_coords"] == n_coords, f"with_coords {c['with_coords']} == {n_coords}")
check(c["categorized"] == n_cat, f"categorized {c['categorized']} == {n_cat}")
check(summary["by_origin"] == dict(sorted(by_origin.items(), key=lambda kv: -kv[1])), f"by_origin {summary['by_origin']}")
check(sum(summary["by_origin"].values()) == n_atoms, "경로 합 == 원자 수")
check(atoms_res["n"] == n_atoms and len(atoms_res["atoms"]) == n_atoms, f"/atoms 행 {atoms_res['n']} == {n_atoms}")
check(len(summary["signals"]) == 10, f"신호 {len(summary['signals'])}개 == 10")
check(set(summary["times"]) >= {"index_mtime", "snapshot", "observe_built"}, "세 시각 키")
check(isinstance(summary["available"], dict), "available 사전")
row0 = atoms_res["atoms"][0]
need_cols = {"id", "title", "layer", "scope", "category", "origin", "kind", "status", "tier", "has_coords", "lat", "lng", "n_sources",
             "n_reports", "created", "updated", "uses", "hits", "misses", "nn1", "dup_of", "molecules", "periphery_of", "recalls",
             "list_like", "confusions", "tags", "entities"}
check(need_cols <= set(row0), f"/atoms 열 {sorted(need_cols - set(row0)) or '전부'}")
check(all(len(r["tags"]) <= 6 and len(r["entities"]) <= 4 for r in atoms_res["atoms"]), "tags ≤6 · entities ≤4")

print("\n3) 원자 서랍 · 분자 상세 모양")
for k in ("md_body", "neighbors", "molecules", "recalls", "evidence", "corrections", "report_links", "capture", "origin", "kind", "status"):
    check(k in atom, f"atom.{k}")
check(len(atom["neighbors"]) <= 10, "이웃 ≤10")
if mol:
    for k in ("doc_md", "members", "periphery", "distance_matrix", "missing_atoms", "lineage"):
        check(k in mol, f"molecule.{k}")
    if mol.get("distance_matrix") is not None:
        n = len(mol["members"])
        check(len(mol["distance_matrix"]) == n and all(len(r) == n for r in mol["distance_matrix"]), f"거리 행렬 {n}×{n}")
        thr = mols.get("threshold") or 1
        offdiag = [v for i, r in enumerate(mol["distance_matrix"]) for j, v in enumerate(r) if i != j]
        check(all(v is not None and v <= thr + 1e-6 for v in offdiag), f"멤버 쌍 전부 임계({thr}) 아래(클리크)")

print("\n4) graph — 스냅샷 knn 으로 걸린 엣지")
if graph.get("available"):
    check(graph["k"] == (graph.get("params") or {}).get("knn", graph["k"]), f"graph.k {graph['k']} == 스냅샷 knn")
    check(len(graph["mutual_edges"]) <= len(graph15["mutual_edges"]), f"k={graph['k']} 엣지 {len(graph['mutual_edges'])} ≤ k=15 엣지 {len(graph15['mutual_edges'])}")
    check(len(graph["quantiles"]) >= 2, f"분위표 {len(graph['quantiles'])}")
    check(all(len(e) == 3 for e in graph["mutual_edges"][:50]), "엣지 [i,j,d] 3-튜플")
else:
    print("  skip neighbors.json 없음 — available:false 로 200 (정상 저하)")
    check(graph.get("available") is False and "molecules" in graph, "graph 저하 구조")

print("\n5) 큐 · 정정 · 로그")
for k in ("dups", "unnamed", "uncategorized", "list_like", "missing_watch", "retracted", "costs", "stale"):
    check(k in queues, f"queues.{k}")
check(queues["uncategorized"]["n"] == n_atoms - n_cat, f"미분류 {queues['uncategorized']['n']} == {n_atoms - n_cat}")
check(any(x.get("total") for x in queues["costs"]), "비용 합계 행")
check("rows" in corr and "learning" in corr and "readme_md" in corr, "corrections 키")
if have_corr:
    check(len(corr["rows"]) == summary["counts"]["corrections"] == summary["corrections"]["n"], f"정정 원장 {len(corr['rows'])}줄 일치")
check("rows" in recall and "total" in recall, "recall_log 키")
check(st_layout == (200 if (KNOW / "observe" / "layout.json").exists() else 404), f"layout {st_layout} ↔ 파일 존재 {(KNOW / 'observe' / 'layout.json').exists()}")

print("\n6) 정적 화면")
for p in ("/observe.html", "/observe.js", "/observe.css", "/clique.js"):
    r = client.get(p)
    check(r.status_code == 200 and len(r.content) > 1000, f"{r.status_code} {len(r.content):>8}B {p}")
html = client.get("/observe.html").text
for pane in ("pane-dash", "pane-atoms", "pane-embed", "pane-mols", "pane-work"):
    check(f'id="{pane}"' in html, f"다섯 탭 DOM · {pane}")
check("사람 승인 없음" in html, "계기판 한 줄 설명(사람 승인 없음)")
check('id="dlg"' in html, "대화 서랍 DOM")

print("\n7) 캐시 — 같은 요청 두 번째가 더 빠르거나 같다(mtime 캐시)")
t = time.time(); client.get("/api/observe/atoms"); a1 = time.time() - t
t = time.time(); client.get("/api/observe/atoms"); a2 = time.time() - t
print(f"  /atoms 1회 {a1 * 1000:.0f}ms → 2회 {a2 * 1000:.0f}ms")

print()
if fails:
    print(f"실패 {len(fails)}건:")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("전부 통과")
