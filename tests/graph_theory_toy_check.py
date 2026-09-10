"""graph-theory-foundations_260909 의 장난감 그래프 손계산을 코드로 고정한다 — 문서의 모든 숫자 예시는 이 스크립트가 낸다.

문서 docs/theory/graph-theory-foundations_260909.html §2(핵심 대상 8)·§3(후보 4)의 숫자는 여기서 나온 값이며,
값이 바뀌면 문서도 틀린 것이다(writing-craft_260909 원칙 7·10: 수식마다 숫자, 숫자마다 출처).
실행: PYTHONUTF8=1 python tests/graph_theory_toy_check.py  (시스템 Python — numpy·scipy·networkx)
"""
from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")

import numpy as np
import networkx as nx
from scipy.optimize import linear_sum_assignment

FAILS = 0
N = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILS, N
    N += 1
    if not cond:
        FAILS += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def close(a, b, tol=1e-3) -> bool:
    return abs(float(a) - float(b)) <= tol


# ---- §2.1 그래프·차수·인접행렬 / §2.2 연결요소 / §2.3 극대 클리크 / §3 k-core·k-truss : G1 --------------------
print("== G1: V={A..F}, E={AB,AC,BC,CD,DE} ==")
G1 = nx.Graph(); G1.add_nodes_from("ABCDEF"); G1.add_edges_from([("A","B"),("A","C"),("B","C"),("C","D"),("D","E")])
deg = dict(G1.degree())
check("차수 A2 B2 C3 D2 E1 F0", deg == {"A":2,"B":2,"C":3,"D":2,"E":1,"F":0}, str(deg))
check("악수 정리 Σdeg = 2|E| = 10", sum(deg.values()) == 2 * G1.number_of_edges() == 10)
A = nx.to_numpy_array(G1, nodelist=list("ABCDEF"), dtype=int)
check("인접행렬 대칭·대각 0", (A == A.T).all() and (np.diag(A) == 0).all())
check("(A²)의 대각 = 차수", list(np.diag(A @ A)) == [2,2,3,2,1,0])
check("(A²)[A,B] = 1 (길이 2 경로 A–C–B 하나)", (A @ A)[0, 1] == 1)
check("trace(A³)/6 = 삼각형 1개", close(np.trace(A @ A @ A) / 6, 1.0))
comps = sorted(sorted(c) for c in nx.connected_components(G1))
check("연결요소 2개 {A..E},{F}", comps == [["A","B","C","D","E"], ["F"]], str(comps))
cl = sorted(sorted(c) for c in nx.find_cliques(G1))
check("극대 클리크 {ABC},{CD},{DE},{F}", cl == [["A","B","C"],["C","D"],["D","E"],["F"]], str(cl))
check("min_size 3 → {A,B,C} 하나", [c for c in cl if len(c) >= 3] == [["A","B","C"]])
check("{A,B,C,D} 는 클리크 아님(A–D·B–D 없음)", not (G1.has_edge("A","D") or G1.has_edge("B","D")))
check("오일러 표수 V−E+F = 6−5+1 = 2 = β0(2) − β1(0)", 6 - 5 + 1 == 2 == len(comps) - 0)
core = nx.core_number(G1)
check("core number A,B,C=2 · D,E=1 · F=0", core == {"A":2,"B":2,"C":2,"D":1,"E":1,"F":0}, str(core))
tr = sorted(tuple(sorted(e)) for e in nx.k_truss(G1, 3).edges())
check("3-truss 엣지 = 삼각형 {AB,AC,BC} (= 3-클리크 멤버십)", tr == [("A","B"),("A","C"),("B","C")], str(tr))

# ---- §2.2 union-find : 근중복 쌍 → 전이 묶음 ------------------------------------------------------------
print("== union-find on dup pairs ==")
pairs = [("a1","a2",0.08),("a2","a3",0.09),("a5","a6",0.07)]
parent: dict[str, str] = {}


def find(x):
    parent.setdefault(x, x)
    while parent[x] != x:
        parent[x] = parent[parent[x]]; x = parent[x]
    return x


for a, b, _ in pairs:
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb
groups = {}
for x in list(parent):
    groups.setdefault(find(x), set()).add(x)
gs = sorted(sorted(g) for g in groups.values())
check("묶음 {a1,a2,a3},{a5,a6} — 3쌍 → 2묶음", gs == [["a1","a2","a3"], ["a5","a6"]], str(gs))
check("a1–a3 은 직접 쌍이 아님 → transitive_only", ("a1","a3") not in {(p[0], p[1]) for p in pairs})

# ---- §2.5 코사인 거리 · 자카드 ------------------------------------------------------------------------
print("== cosine / jaccard ==")
u, v, w = np.array([1,2,2.]), np.array([2,1,2.]), np.array([2,-2,1.])
R = np.stack([u, v, w]); norms = np.linalg.norm(R, axis=1)
check("‖u‖=‖v‖=‖w‖=3", (norms == 3).all(), str(norms))
V = R / norms[:, None]
D = 1.0 - V @ V.T
check("u·v=8 → cos 8/9 → d(u,v)=1/9=0.1111", close(D[0,1], 1/9))
check("u·w=0 → 직교 → d(u,w)=1.0", close(D[0,2], 1.0))
check("v·w=4 → d(v,w)=5/9=0.5556", close(D[1,2], 5/9))
check("1−⟨û,v̂⟩ = ‖û−v̂‖²/2", close(D[0,1], np.sum((V[0]-V[1])**2) / 2))
M1, M2, M3 = {"a","b","c","d"}, {"b","c","d","e"}, {"a","b","x","y","z"}
jac = lambda a, b: len(a & b) / len(a | b)
check("J(M1,M2)=3/5=0.6 ≥ 0.5 → continue", close(jac(M1,M2), 0.6))
check("J(M1,M3)=2/7=0.2857 < 0.5", close(jac(M1,M3), 2/7))
check("J(M2,M3)=1/8=0.125", close(jac(M2,M3), 0.125))

# ---- §2.6 상호 kNN · 백분위 임계 : 수직선 위 6점 ---------------------------------------------------------
print("== 1D toy A0 B1 C2 D5 E6 F10, k=2 ==")
pos = {"A":0,"B":1,"C":2,"D":5,"E":6,"F":10}; ids = list(pos); n = len(ids)
Dm = np.array([[abs(pos[a]-pos[b]) for b in ids] for a in ids], float)
upper = Dm[np.triu_indices(n, 1)]
check("쌍 15개, 정렬 [1,1,1,2,3,4,4,4,5,5,5,6,8,9,10]", sorted(upper) == [1,1,1,2,3,4,4,4,5,5,5,6,8,9,10])
check("Q20 = 1.8 (위치 2.8 → 1+0.8·(2−1))", close(np.percentile(upper, 20), 1.8))
check("Q30 = 3.2 (위치 4.2 → 3+0.2·(4−3))", close(np.percentile(upper, 30), 3.2))
k = 2
Dk = Dm.copy(); np.fill_diagonal(Dk, np.inf)
part = np.argpartition(Dk, k-1, axis=1)[:, :k]
rows = np.arange(n)[:, None]; nn = part[rows, np.argsort(Dk[rows, part], axis=1)]
mask = np.zeros((n, n), bool); mask[np.repeat(np.arange(n), k), nn.ravel()] = True
mutual = mask & mask.T
lists = {ids[i]: [ids[j] for j in nn[i]] for i in range(n)}
check("kNN 목록 A[B,C] B[A,C] C[B,A] D[E,C] E[D,C] F[E,D]", lists == {"A":["B","C"],"B":["A","C"],"C":["B","A"],"D":["E","C"],"E":["D","C"],"F":["E","D"]}, str(lists))
me = sorted((ids[i], ids[j]) for i, j in zip(*np.nonzero(np.triu(mutual, 1))))
check("상호 kNN 엣지 AB AC BC DE (C–D·E–F 는 한쪽만) · F 고립", me == [("A","B"),("A","C"),("B","C"),("D","E")], str(me))


def cliques_at(p):
    m = mutual & (Dm <= np.percentile(upper, p))
    G = nx.Graph(); G.add_nodes_from(range(n)); G.add_edges_from(zip(*np.nonzero(np.triu(m, 1))))
    return sorted(sorted(ids[x] for x in c) for c in nx.find_cliques(G) if len(c) >= 3)


check("pct 20(ε=1.8): A–C(2) 탈락 → 삼각형 없음", cliques_at(20) == [])
check("pct 30(ε=3.2): {A,B,C} 분자 탄생", cliques_at(30) == [["A","B","C"]])
s = sorted(upper)
pct_of = lambda val: round(100 * next(i for i, x in enumerate(s) if x >= val) / (len(s) - 1), 1)
check("순수 ε 필트레이션: {A,B,C} 탄생 ε=2 → pct 21.4", pct_of(2) == 21.4)
check("… {A,B,C,D} 로 흡수 ε=5 → pct 57.1 (지속 구간 [2,5))", pct_of(5) == 57.1)


def maximal_at(e):
    G = nx.Graph(); G.add_nodes_from(ids)
    G.add_edges_from((ids[i], ids[j]) for i, j in zip(*np.nonzero(np.triu(Dm <= e, 1))))
    return sorted(sorted(c) for c in nx.find_cliques(G))


check("ε=5 에서 {A,B,C,D} 가 극대 — {A,B,C} 극대성 상실", ["A","B","C","D"] in maximal_at(5) and ["A","B","C"] not in maximal_at(5))

# ---- §2.7 공통 이웃 · Adamic-Adar : 허브 h + 저차수 p ----------------------------------------------------
print("== Adamic-Adar toy ==")
H = nx.Graph(); H.add_edges_from([("h","x"),("h","y"),("h","p"),("h","q"),("h","r"),("p","x"),("p","y")])
check("차수 h5 p3 x2 y2 q1 r1 · |E|=7", dict(H.degree()) == {"h":5,"x":2,"y":2,"p":3,"q":1,"r":1} and H.number_of_edges() == 7)
aa = {(a, b): sc for a, b, sc in nx.adamic_adar_index(H, [("x","y"),("q","r"),("x","q"),("p","q")])}
check("AA(x,y) = 1/ln5 + 1/ln3 = 0.6213 + 0.9102 = 1.5315", close(aa[("x","y")], 1/math.log(5) + 1/math.log(3)) and close(aa[("x","y")], 1.5315, 1e-3))
check("AA(q,r) = 1/ln5 = 0.6213 (공통 이웃 허브 h 하나)", close(aa[("q","r")], 1/math.log(5)))
check("CN(x,y)=2 > CN(q,r)=1 · 저차수 p 의 가중 0.91 > 허브 h 의 0.62", len(list(nx.common_neighbors(H,"x","y"))) == 2 and 1/math.log(3) > 1/math.log(5))
check("x–h 는 이미 인접 → 후보 아님", H.has_edge("x","h"))

# ---- §2.8 이분 매칭 · 헝가리안 : 3×3 --------------------------------------------------------------------
print("== Hungarian 3x3 ==")
J = np.array([[0.70,0.65,0.0],[0.60,0.0,0.0],[0.0,0.0,0.55]])   # 행 new B1..B3, 열 old A1..A3


def solve(no_match):
    cost = np.where(J >= 0.5, 1 - J, no_match)
    r, c = linear_sum_assignment(cost)
    valid = [(i, j) for i, j in zip(r.tolist(), c.tolist()) if J[i, j] >= 0.5]
    return cost, valid, float(cost[r, c].sum())


cost, valid, total = solve(1 + 3)
check("NO_MATCH=1+max(3,3)=4 → B1→A2 B2→A1 B3→A3, 비용 1.20, ΣJ 1.80", valid == [(0,1),(1,0),(2,2)] and close(total, 1.2) and close(sum(J[i,j] for i,j in valid), 1.8))
matched, g = set(), []
for i in range(3):
    best, bj = None, 0.0
    for j in range(3):
        if j not in matched and J[i, j] >= 0.5 and J[i, j] > bj:
            best, bj = j, J[i, j]
    if best is not None:
        matched.add(best); g.append((i, best))
check("그리디(B1→A1 선점) → B2 후보 없음 → 매칭 2, ΣJ 1.25", g == [(0,0),(2,2)] and close(sum(J[i,j] for i,j in g), 1.25))
rr = cost - cost.min(axis=1, keepdims=True); cc = rr - rr.min(axis=0, keepdims=True)
check("행 축소 후 열 축소: 0 위치 (B1,A1)(B1,A2)(B2,A1)(B3,A3)", sorted(zip(*np.nonzero(np.isclose(cc, 0)))) == [(0,0),(0,1),(1,0),(2,2)])
_, valid03, total03 = solve(0.3)
check("NO_MATCH=0.3 이면 세 배정 모두 벌점 칸(0.9 < 1.2) → 유효 매칭 0", valid03 == [] and close(total03, 0.9))
_, valid15, _ = solve(1.5)
check("NO_MATCH=1.5 (= 0.5·min(3,3)) 부터 매칭 3 복원", valid15 == [(0,1),(1,0),(2,2)])

# ---- §3 후보: 모듈성 Q · PageRank --------------------------------------------------------------------------
print("== modularity / pagerank ==")
Q = nx.Graph(); Q.add_edges_from([("A","B"),("A","C"),("B","C"),("D","E"),("D","F"),("E","F"),("C","D")])
q2 = nx.community.modularity(Q, [{"A","B","C"},{"D","E","F"}])
check("두 삼각형+다리(m=7): Q({ABC},{DEF}) = 2·(3/7 − (7/14)²) = 0.3571", close(q2, 2*(3/7-(7/14)**2)) and close(q2, 0.3571))
check("전부 한 커뮤니티 Q=0", close(nx.community.modularity(Q, [set("ABCDEF")]), 0.0))
check("{AB},{CD},{EF} Q=0.0816 < 0.3571", close(nx.community.modularity(Q, [{"A","B"},{"C","D"},{"E","F"}]), 0.0816))
S = nx.Graph(); S.add_edges_from([("h","a"),("h","b"),("h","c")])
pr = nx.pagerank(S); ppr = nx.pagerank(S, personalization={"a":1,"h":0,"b":0,"c":0})
check("별 그래프 PR: h 0.4797 · 잎 0.1734 (손계산 q=0.133125/0.2775)", close(pr["h"], 0.4797) and close(pr["a"], 0.1734))
check("PPR(seed=a): a 0.2802 > b,c 0.1302 · h 0.4595", close(ppr["a"], 0.2802) and close(ppr["b"], 0.1302) and close(ppr["h"], 0.4595))

print(f"\n{N - FAILS}/{N} PASS · numpy {np.__version__} · networkx {nx.__version__}")
sys.exit(1 if FAILS else 0)
