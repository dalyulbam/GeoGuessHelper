"""그래프이론 기반 수정(260908)의 경계 조건 — 작은 fixture 로 독립 검사.

codex 2차 검토가 "현재 데이터 회귀 검사(observe_check.py)는 수정 2~8 의 경계 조건을 검증하지 않는다"고 지적해
만들었다. 실행: PYTHONUTF8=1 PYTHONPATH=src python tests/graph_fixes_check.py  (시스템 Python — numpy·scipy·networkx)
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("PYTHONUTF8", "1")

FAILS = 0
N = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILS, N
    N += 1
    if not cond:
        FAILS += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    from geoguesshelper import observe as O
    from geoguesshelper import molecule as M

    print("① 계보 continue 매칭 — 헝가리안(전역 최적) · 벌점 · 그리디 저하")
    # codex 2차 반례: 22개 사슬. 고정 NO_MATCH=10 이면 21개+강제1개(10.14) < 22개 완전매칭(10.92) 라 하나를 버렸다.
    A = {f"A{i}": set(range(100 * i, 100 * i + 300)) for i in range(22)}
    B = {f"B{i}": set(range(100 * i + 1, 100 * i + 301)) for i in range(21)}
    B["B21"] = set(range(-99, 201))
    cont, matched, method = O._continue_matches(list(A), list(B), A, B)
    check("22개 사슬 반례 — 완전 매칭 22개 유지", len(cont) == 22 and len(matched) == 22, f"{len(cont)} · {method}")
    check("매칭이 1:1 (old·new 중복 없음)", len({c[0] for c in cont}) == len(cont) == len({c[1] for c in cont}))
    # 경쟁 반례(1차): 두 new 가 같은 old 를 최고로 고르는 경우
    A2 = {"A1": {"a", "b", "c", "d"}, "A2": {"a", "b", "e", "f", "g", "h"}}
    B2 = {"B1": {"a", "b", "c", "d", "x"}, "B2": {"a", "b", "e", "f", "g", "y"}}
    cont2, _, _ = O._continue_matches(list(A2), list(B2), A2, B2)
    check("경쟁 반례 — B1↔A1, B2↔A2 둘 다 살림", sorted((c[0], c[1]) for c in cont2) == [("A1", "B1"), ("A2", "B2")], str(cont2))
    # 그리디 저하 경로 — 이미 매칭된 old 는 제외(1:1 불변식)
    A3 = {"A": {"a", "b", "c", "d"}}
    B3 = {"B1": {"a", "b", "c", "e"}, "B2": {"a", "b", "c", "f"}}
    cont3, matched3 = O._continue_matches_greedy(list(A3), list(B3), A3, B3)
    check("그리디 저하 — 같은 old 로 두 new 가 continue 되지 않는다", len(cont3) == 1 and matched3 == {"A"}, str(cont3))
    check("후보 없음 → method none", O._continue_matches([], ["B"], {}, {"B": {"a"}})[2] == "none")

    print("② 근중복 전이 묶음(union-find)")
    pairs = [{"a": "A", "b": "B", "d": 0.05}, {"a": "B", "b": "C", "d": 0.09}, {"a": "X", "b": "Y", "d": 0.02}]
    groups = O._dup_groups(pairs)
    g3 = next(g for g in groups if g["n"] == 3)
    check("A~B, B~C → {A,B,C} 한 묶음 · 전이로만", g3["atoms"] == ["A", "B", "C"] and g3["direct_pairs"] == 2 and g3["transitive_only"] is True
          and g3["d_min"] == 0.05 and g3["d_max"] == 0.09, str(g3))
    check("X~Y 는 2원자 묶음 · 전이 아님 · d_min 순 정렬", groups[0]["atoms"] == ["X", "Y"] and groups[0]["transitive_only"] is False)

    print("③ 링크 후보 — Adamic-Adar · 직접 엣지 제외 · 목록형 공통 이웃 비율 · bridges")
    import numpy as np
    ids = [f"atm_{i:012x}" for i in range(6)]
    # 허브 0 이 1,2,3,4 와 연결, 1-2 직접 연결, 5 는 고립
    edges = [[0, 1, 0.1, 0, 0], [0, 2, 0.1, 1, 0], [0, 3, 0.1, 2, 0], [0, 4, 0.1, 3, 0], [1, 2, 0.2, 1, 1]]
    D = np.full((6, 6), 0.9)
    np.fill_diagonal(D, 0.0)
    D[3, 4] = D[4, 3] = 0.6   # 3–4 는 멀지만 공통 이웃(0)이 있다
    atoms = {a: {"title": f"t{i}", "layer": "history", "origin": "wiki" if i == 0 else "report"} for i, a in enumerate(ids)}
    atoms[ids[0]]["title"] = "Dynasties of somewhere"   # 허브 0 = 목록형
    r = O._link_candidates(ids, edges, atoms, D=D, eps=0.15, far_thr=0.5, top=10)
    c = {(x["a"], x["b"]): x for x in r["candidates"]}
    key = (ids[3], ids[4])
    check("직접 이어진 1-2 는 후보가 아니다", (ids[1], ids[2]) not in c)
    check("3-4 는 공통 이웃 1(허브 0)·d 0.6·below_eps False", key in c and c[key]["common_neighbors"] == 1 and c[key]["d"] == 0.6
          and c[key]["below_eps"] is False, json.dumps(c.get(key)))
    check("공통 이웃이 목록형이면 list_like_cn_share=1.0", key in c and c[key]["list_like_cn_share"] == 1.0)
    # 허브 0 의 이웃 {1,2,3,4} 에서 C(4,2)=6 쌍 중 1-2 는 직접 엣지라 제외 → 5 쌍
    check("bridges — 공통 이웃 <5 이라 비어 있다 · n_pairs_scored 5", r["bridges"] == [] and r["n_pairs_scored"] == 5, str(r["n_pairs_scored"]))
    # 공통 이웃 5개(비목록형) + 멀리 떨어진 쌍 → bridge 1개
    ids2 = [f"atm_{i:012x}" for i in range(7)]
    edges2 = [[0, k, 0.1, 0, 0] for k in range(2, 7)] + [[1, k, 0.1, 0, 0] for k in range(2, 7)]
    D2 = np.full((7, 7), 0.3)
    np.fill_diagonal(D2, 0.0)
    D2[0, 1] = D2[1, 0] = 0.7
    atoms2 = {a: {"title": f"t{i}", "layer": "culture", "origin": "report"} for i, a in enumerate(ids2)}
    r2 = O._link_candidates(ids2, edges2, atoms2, D=D2, eps=0.15, far_thr=0.5, top=10)
    check("공통 이웃 5·d 0.7 → bridge 1개 · common_ids 5개 · bridge = score×d",
          len(r2["bridges"]) == 1 and r2["bridges"][0]["common_neighbors"] == 5 and len(r2["bridges"][0]["common_ids"]) == 5
          and abs(r2["bridges"][0]["bridge"] - round(r2["bridges"][0]["score"] * 0.7, 4)) < 2e-4, json.dumps(r2["bridges"][:1])[:200])

    print("④ 클리크 열거 상한 — 정확히 상한이면 완전, 넘기면 truncated")
    import networkx as nx
    from geoguesshelper.knowledge import Atom
    G = nx.Graph()
    for k in range(5):                                   # 4-클리크 5개(서로 떨어짐)
        base = 4 * k
        G.add_edges_from((base + i, base + j) for i in range(4) for j in range(i + 1, 4))
    Dg = np.full((20, 20), 0.9)
    np.fill_diagonal(Dg, 0.0)
    for i, j in G.edges:
        Dg[i, j] = Dg[j, i] = 0.1
    gids = [f"atm_{i:012x}" for i in range(20)]
    gatoms = {a: Atom(id=a, layer="history", scope="region", title=a, body="", tags=[], entities=[]) for a in gids}
    saved = M.MAX_CLIQUES
    try:
        M.MAX_CLIQUES = 5
        mols, n_raw, trunc = M._molecules_from(G, Dg, gids, gatoms, min_size=4, merge=0.5)
        check("정확히 상한(5)이면 truncated=False · 5개", trunc is False and n_raw == 5, f"{n_raw} {trunc}")
        M.MAX_CLIQUES = 3
        mols, n_raw, trunc = M._molecules_from(G, Dg, gids, gatoms, min_size=4, merge=0.5)
        check("상한(3)을 넘기면 truncated=True · 채택 3개(초과분 버림)", trunc is True and n_raw == 3, f"{n_raw} {trunc}")
    finally:
        M.MAX_CLIQUES = saved

    print("⑤ 임베딩 stale 검사 — atom_updated 없는 구버전 npz 는 '확인 불가'")
    with tempfile.TemporaryDirectory() as td:
        md = M.MolDir(Path(td))
        md.ensure()
        np.savez(md.embeddings, ids=np.array(["atm_a", "atm_b"]), vectors=np.eye(2, dtype="float32"), model=np.array("m"))
        ids_, V_, model_ = M._load_embeddings(md)
        check("구버전 npz → last_atom_updated None", md.last_atom_updated is None and ids_ == ["atm_a", "atm_b"])
        np.savez(md.embeddings, ids=np.array(["atm_a", "atm_b"]), vectors=np.eye(2, dtype="float32"), model=np.array("m"),
                 atom_updated=np.array([1.0, 2.0]))
        M._load_embeddings(md)
        check("atom_updated 있으면 dict", md.last_atom_updated == {"atm_a": 1.0, "atm_b": 2.0})

    print("⑥ 거리 행렬 dtype — 판정·직렬화가 float64 로 통일")
    V = np.random.default_rng(0).normal(size=(5, 8)).astype("float32")
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    check("observe._distances → float64", O._distances(np, V).dtype == np.float64)
    check("molecule._distances → float64", M._distances(V).dtype == np.float64)

    print("⑦ 증거 채점 멱등성 — 같은 잡·같은 원자·같은 판정은 한 번만")
    from geoguesshelper import knowledge as K
    from geoguesshelper.config import load_settings
    with tempfile.TemporaryDirectory() as td:
        s = dataclasses.replace(load_settings(), knowledge_dir=Path(td))
        (Path(td) / "atoms").mkdir(parents=True, exist_ok=True)
        st = K.store_for(s)
        a = Atom(id="atm_0123456789ab", layer="history", scope="region", title="t", body="b", tags=["x"], entities=["e"])
        st.save(a)
        r1 = K.record_evidence(s, hits=["atm_0123456789ab"], ctx={"job": "job_test"}, note="1")
        r2 = K.record_evidence(s, hits=["atm_0123456789ab"], ctx={"job": "job_test"}, note="2")
        r3 = K.record_evidence(s, hits=["atm_0123456789ab"], ctx={"job": "job_other"}, note="3")
        cur = st.load("atm_0123456789ab")
        check("같은 잡 두 번 → hits 1 · 두 번째는 skipped", cur.hits == 2 and len(r1["changed"]) == 1 and r2["skipped"] == [{"atom": "atm_0123456789ab", "event": "hit"}]
              and len(r3["changed"]) == 1, f"hits={cur.hits} r2={r2['skipped']}")

    print("⑧ PARTIAL 재개 판정")
    from geoguesshelper import correction as C
    good = {"status": "PARTIAL", "correction": {"summary": "s", "discriminators": []}, "verdict": {"country": "hit"},
            "blind": {"final": {"x": 1}}}
    check("판단이 온전한 PARTIAL → 재개 가능", C._resumable(good) is True)
    check("OK 기록은 재개 대상 아님", C._resumable({**good, "status": "OK"}) is False)
    check("정정 호출이 없으면 재개 불가", C._resumable({**good, "correction": {"error": "x"}}) is False)
    check("None → False", C._resumable(None) is False)

    print(f"\n{'PASS' if not FAILS else 'FAIL'} — 검사 {N}건 · 실패 {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
