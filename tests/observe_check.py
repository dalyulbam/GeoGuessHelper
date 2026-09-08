"""관측 사이드카 독립 검증 — index.json · embeddings.npz · molecule/index.json 에서 **직접 다시 세어**
observe/ 산출(series 마지막 줄 · dups · atom_meta · neighbors · layout · lineage · growth)과 대조한다.

왜 따로 세나: 관측소의 모든 숫자는 "파일에서 재현 가능"해야 한다(기획 V2·V6). 빌드 코드가 자기 결과를
자기 규칙으로 검사하면 규칙의 오해가 그대로 통과한다. 이 스크립트는 observe.py 를 import 하지 않고
명세(impl-spec_260907.md §1.6·§5.2)의 규칙만으로 다시 계산한다.

실행(시스템 Python — numpy 가 있어야 npz 항목까지 검사한다. 없으면 그 항목은 SKIP):
    PYTHONUTF8=1 python tests/observe_check.py
빌드 직후에 돌려야 한다 — 다른 에이전트가 원자를 더 쌓아 index.json 이 바뀌었으면 원자 수 비교는 STALE 로 표시한다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOW = ROOT / "docs" / "knowledge"
OBS = KNOW / "observe"
MOL = KNOW / "molecule"

# 명세 §1.6 / §5.2 규칙 — observe.py 와 무관하게 여기서 다시 적는다.
ORIGINS = ("report", "expansion", "wiki", "correction", "dialogue", "baseline")
LIST_RE = re.compile(r"\b(list|lists|dynasties|kingdoms|states|empires|rulers|monarchs|polities|chiefdoms|republics|"
                     r"duchies|regimes|principalities)\b", re.I)
# 기획서(260907) 실측 참조값 — 그 시점의 1,297 원자 기준. 이후 정정·대화 원자가 늘면 report/expansion/wiki 는
# 그대로이고 새 경로가 더해진다. expansion 617 은 체크포인트 수치이고 규칙 적재 결과는 616(양쪽 태그 가진 원자 1개).
REF = {"report": 351, "expansion": 616, "wiki": 330, "categorized_legacy": 53, "with_coords_legacy": 506,
       "list_like": 217, "molecules": 120, "named": 62, "wiki_only_molecules": 64, "wiki_only_named": 27,
       "missing_atoms": 137, "links": 19, "dup_0.10": 19, "dup_0.12": 41, "dup_0.15": 98}
EXPECT_ON = {"uncategorized", "dup-candidates", "unnamed-backlog", "list-dominance", "no-grounding"}

results: list[tuple[str, str, str]] = []


def rec(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"{status:5} {name}" + (f"  — {detail}" if detail else ""))


def check(name: str, ok: bool, detail: str = "") -> None:
    rec("PASS" if ok else "FAIL", name, detail)


def origin(m: dict) -> str:
    if m.get("origin") in ORIGINS:
        return m["origin"]
    if m.get("reports"):
        return "report"
    if any(str(t).startswith("x-") for t in (m.get("tags") or [])):
        return "expansion"
    return "wiki"


def jl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    if not (OBS / "series.jsonl").exists():
        rec("FAIL", "observe/series.jsonl 존재", "먼저 observe build")
        return 1
    rows = jl(OBS / "series.jsonl")
    last = rows[-1]
    idx = json.loads((KNOW / "index.json").read_text(encoding="utf-8"))["atoms"]
    idx_mtime = os.path.getmtime(KNOW / "index.json")
    fresh = abs(idx_mtime - float(last.get("index_mtime") or 0)) < 1e-6
    if not fresh:
        rec("WARN", "index.json 이 빌드 뒤에 바뀌었다", f"빌드 {time.strftime('%H:%M:%S', time.localtime(last['index_mtime']))} · 지금 "
            f"{time.strftime('%H:%M:%S', time.localtime(idx_mtime))} — 원자 수 비교는 STALE 로 표시")

    def cmp(name, mine, theirs):
        ok = mine == theirs
        if ok or fresh:
            check(name, ok, f"재계산 {mine} · series {theirs}")
        else:
            rec("STALE", name, f"재계산 {mine} · series {theirs} (index 변경 후)")

    # ── 1. index.json 집계 ↔ series 마지막 줄 ──
    n = len(idx)
    orig = Counter(origin(m) for m in idx.values())
    cmp("atoms", n, last["atoms"])
    cmp("by_origin", dict(orig), dict(last["by_origin"]))
    cat = [m for m in idx.values() if (m.get("category") or "").strip()]
    cmp("categorized", len(cat), last["categorized"])
    cmp("categorized_report_origin", sum(1 for m in cat if origin(m) == "report"), last["categorized_report_origin"])
    cmp("with_coords", sum(1 for m in idx.values() if m.get("lat") is not None and m.get("lng") is not None), last["with_coords"])
    cmp("no_sources", sum(1 for m in idx.values() if not m.get("sources")), last["no_sources"])
    cmp("no_period", sum(1 for m in idx.values() if m.get("period_start") is None and m.get("period_end") is None), last["no_period"])
    cmp("list_like", sum(1 for m in idx.values() if origin(m) == "wiki" and LIST_RE.search(m.get("title") or "")), last["list_like"])
    cmp("by_kind", dict(Counter((m.get("kind") or "fact") for m in idx.values())), dict(last["by_kind"]))
    cmp("by_status", dict(Counter((m.get("status") or "active") for m in idx.values())), dict(last["by_status"]))
    cmp("retracted", sum(1 for m in idx.values() if m.get("status") == "retracted"), last["retracted"])

    # ── 2. 분자 index ↔ series ──
    mi = json.loads((MOL / "index.json").read_text(encoding="utf-8"))
    latest = [m for m in mi["molecules"].values() if m.get("in_latest")]
    named = [m for m in latest if m.get("named")]

    def wiki_only(m):
        mem = [a for a in m["atoms"] if a in idx]
        return bool(mem) and all(origin(idx[a]) == "wiki" for a in mem)

    check("molecules", len(latest) == last["molecules"], f"{len(latest)} · series {last['molecules']}")
    check("named/unnamed", len(named) == last["named"] and len(latest) - len(named) == last["unnamed"],
          f"{len(named)}/{len(latest) - len(named)} · series {last['named']}/{last['unnamed']}")
    check("wiki_only_molecules", sum(map(wiki_only, latest)) == last["wiki_only_molecules"], f"{sum(map(wiki_only, latest))}")
    check("wiki_only_named", sum(map(wiki_only, named)) == last["wiki_only_named"], f"{sum(map(wiki_only, named))}")
    check("missing_atoms", sum(len(m.get("missing_atoms") or []) for m in named) == last["missing_atoms"])
    check("links", int((mi.get("links") or {}).get("n") or 0) == last["links"], f"{last['links']}")
    ng = 0
    for m in named:
        p = MOL / f"{m['id']}.md"
        if p.exists():
            sec = re.search(r"^## 검색 근거\r?\n(.*?)(?=^## |\Z)", p.read_text(encoding="utf-8"), re.S | re.M)
            if not sec or not any(ln.startswith("- ") and not ln.startswith("- (") for ln in sec.group(1).splitlines()):
                ng += 1
    check("no_grounding_docs", ng == last["no_grounding_docs"], f"{ng} · series {last['no_grounding_docs']}")

    # ── 3. atom_meta / growth ──
    am = json.loads((OBS / "atom_meta.json").read_text(encoding="utf-8"))
    check("atom_meta.n == series.atoms", am["n"] == last["atoms"] == len(am["atoms"]))
    check("atom_meta origin 합 == n", sum(Counter(a["origin"] for a in am["atoms"].values()).values()) == am["n"])
    check("atom_meta 분자 멤버 수 == 스냅샷 covered", sum(1 for a in am["atoms"].values() if a["molecules"]) ==
          len({x for m in latest for x in m["atoms"]}))
    g = json.loads((OBS / "growth.json").read_text(encoding="utf-8"))
    check("growth 합 == atoms", sum(d["total"] for d in g["days"]) == am["n"], f"{len(g['days'])}일")
    check("growth 날짜 정렬", [d["day"] for d in g["days"]] == sorted(d["day"] for d in g["days"]))

    # ── 4. npz 재계산 ↔ dups / neighbors / layout ──
    try:
        import numpy as np  # type: ignore
    except Exception:  # noqa: BLE001
        np = None
    npz = MOL / "embeddings.npz"
    if np is None or not npz.exists():
        rec("SKIP", "npz 항목(dups·neighbors·layout)", "numpy 없음" if np is None else "embeddings.npz 없음")
    else:
        with np.load(npz) as z:
            ids = [str(x) for x in z["ids"].tolist()]
            V = z["vectors"].astype("float32")
        D = 1.0 - V @ V.T
        np.fill_diagonal(D, 0.0)
        D = np.clip(D, 0.0, 2.0)
        iu = np.triu_indices(len(ids), 1)
        up = D[iu]
        tau = float(last["tau_dup"])
        dups = json.loads((OBS / "dups.json").read_text(encoding="utf-8"))
        mine = {(ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i]) for i, j in zip(*np.nonzero(np.triu(D < tau, 1)))}
        theirs = {(p["a"], p["b"]) if p["a"] < p["b"] else (p["b"], p["a"]) for p in dups["pairs"]}
        check(f"dup pairs d<{tau} 집합 일치", mine == theirs, f"{len(mine)} · dups.json {dups['n_pairs']} · series {last['dup_pairs']}")
        # 저장 d 는 4자리 반올림 — 원본 d=0.09999 도 0.1 로 저장되므로 엄격 비교는 원본 D 로, 저장값은 반올림 일치로 본다(codex 2차 260908)
        d_of = {(ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i]): float(D[i, j]) for i, j in zip(*np.nonzero(np.triu(D < tau, 1)))}
        check("dups 오름차순 · 전부 < τ(원본 D) · 저장 d = round(D,4)",
              all(dups["pairs"][i]["d"] <= dups["pairs"][i + 1]["d"] for i in range(len(dups["pairs"]) - 1))
              and all(d_of.get((p["a"], p["b"]) if p["a"] < p["b"] else (p["b"], p["a"]), tau) < tau for p in dups["pairs"])
              and all(abs(p["d"] - round(d_of.get((p["a"], p["b"]) if p["a"] < p["b"] else (p["b"], p["a"]), p["d"]), 4)) < 1e-9 for p in dups["pairs"]))
        groups = dups.get("groups") or []
        gp = {(p["a"], p["b"]) if p["a"] < p["b"] else (p["b"], p["a"]) for p in dups["pairs"]}
        check("dups.groups — 쌍의 원자 전부가 정확히 한 묶음에 · direct_pairs 일치 · transitive_only 일치",
              sum(g["n"] for g in groups) == len({a for p in gp for a in p})
              and all(g["direct_pairs"] == sum(1 for a, b in gp if a in set(g["atoms"]) and b in set(g["atoms"])) for g in groups)
              and all(g["transitive_only"] == (g["direct_pairs"] < g["n"] * (g["n"] - 1) // 2) for g in groups),
              f"묶음 {len(groups)} · 전이로만 {sum(1 for g in groups if g['transitive_only'])}")
        for t in (0.10, 0.12, 0.15):
            # 파일 값 ↔ 독립 재계산은 반드시 같아야 한다. 참조 상수(1,297 원자 시점)와의 차이는
            # 원자가 늘면 정당하게 생기므로 실패가 아니라 stale 로만 표시한다.
            mine_n = int((up < t).sum())
            check(f"기준 쌍 수 d<{t} (파일 = 재계산)", mine_n == dups["counts_at"][f"{t:.2f}"],
                  f"{mine_n} · dups.json {dups['counts_at'][f'{t:.2f}']}")
            if mine_n != REF[f"dup_{t:.2f}"]:
                rec("STALE", f"기준 쌍 수 d<{t}", f"참조 {REF[f'dup_{t:.2f}']} (1,297 원자 시점) · 지금 {mine_n}")
        in_pairs = {a for p in dups["pairs"] for a in (p["a"], p["b"])}
        check("쌍에 든 원자는 전부 dup_of 있음", all(am["atoms"][a]["dup_of"] for a in in_pairs if a in am["atoms"]), f"{len(in_pairs)}개")

        nb = json.loads((OBS / "neighbors.json").read_text(encoding="utf-8"))
        k = nb["k"]
        check("neighbors.ids == npz ids", nb["ids"] == ids)
        check("quantiles 201개 · 단조", len(nb["quantiles"]) == 201 and all(nb["quantiles"][i] <= nb["quantiles"][i + 1] for i in range(200)))
        check("quantiles[1] == pct 0.5 임계", abs(nb["quantiles"][1] - float(np.percentile(up, 0.5))) < 1e-5 and
              abs(nb["quantiles"][1] - float(mi.get("threshold") or 0)) < 1e-5, f"{nb['quantiles'][1]} · molecule index {mi.get('threshold')}")
        check(f"knn 길이 {k}", all(len(v) == k for v in nb["knn"].values()) and len(nb["knn"]) == len(ids))
        # 상위 k 재계산 — 최근접 1개와 mutual 플래그 대칭
        Dm = D.copy()
        np.fill_diagonal(Dm, np.inf)
        nn = np.argsort(Dm, axis=1)[:, :k]
        pos = {a: i for i, a in enumerate(ids)}
        ok_nn1 = all(nb["knn"][a][0][0] == ids[nn[i][0]] for i, a in enumerate(ids))
        check("knn 최근접 일치", ok_nn1)
        check("atom_meta.nn1 == knn[0]", all(am["atoms"][a]["nn1"] == {"id": nb["knn"][a][0][0], "d": nb["knn"][a][0][1]}
                                             for a in ids if a in am["atoms"]))
        sets = {a: {x[0] for x in nb["knn"][a]} for a in ids}
        mut_ok = all((x[2] == (a in sets[x[0]])) for a in ids for x in nb["knn"][a])
        check("mutual 플래그 = 상호 포함", mut_ok)
        n_mut = sum(1 for a in ids for x in nb["knn"][a] if x[2]) // 2
        check("mutual_edges 수 == 상호 쌍 수", len(nb["mutual_edges"]) == n_mut, f"{len(nb['mutual_edges'])}")
        check("mutual_edges 는 ids 인덱스(i<j) · 거리 일치",
              all(0 <= i < j < len(ids) and abs(D[i, j] - d) < 1e-3 for i, j, d, *_ in nb["mutual_edges"][:500]))
        # 뷰어 재현 규칙(기획 V-미리보기): 순위 < snapshot knn · d ≤ ε(pct) 인 엣지만 남기면 스냅샷 그래프와 같아야 한다.
        sp = nb.get("snapshot_params") or {}
        latest_snap = json.loads((MOL / "snapshots" / sorted(p.name for p in (MOL / "snapshots").glob("*.json"))[-1])
                                 .read_text(encoding="utf-8"))
        kk = int(sp.get("knn") or 10)
        eps = float(latest_snap.get("threshold") or nb["quantiles"][1])
        ranks_ok = all(nb["knn"][ids[i]][ri][0] == ids[j] and nb["knn"][ids[j]][rj][0] == ids[i]
                       for i, j, d, ri, rj in nb["mutual_edges"])
        check("mutual_edges 순위(ri,rj) 가 knn 순서와 일치", ranks_ok)
        # 뷰어와 같은 재료만 쓴다 — 파일의 d 와 quantiles[1](= pct 0.5 의 ε). 정밀 D 로 세면 통과하는데 파일 값으로
        # 세면 어긋나는 경계 쌍이 있으면 그것이 바로 뷰어 미리보기가 스냅샷과 달라지는 이유다.
        eps_file = nb["quantiles"][1]
        n_rep = sum(1 for i, j, d, ri, rj in nb["mutual_edges"] if max(ri, rj) < kk and d <= eps_file)
        n_exact = sum(1 for i, j, d, ri, rj in nb["mutual_edges"] if max(ri, rj) < kk and D[i, j] <= eps)
        check(f"순위<{kk} · d≤quantiles[1] 로 걸러 스냅샷 엣지 수 재현(파일 값)", n_rep == int(latest_snap.get("n_edges") or -1),
              f"파일 값 {n_rep} · 정밀 {n_exact} · 스냅샷 n_edges {latest_snap.get('n_edges')}")

        lay = json.loads((OBS / "layout.json").read_text(encoding="utf-8"))
        xs = [p[1] for p in lay["points"]]
        ys = [p[2] for p in lay["points"]]
        check("layout 점 수 == npz", lay["n"] == len(ids) == len(lay["points"]) and {p[0] for p in lay["points"]} == set(ids))
        check("layout 좌표 ∈ [0,1]", min(xs) >= 0 and max(xs) <= 1 and min(ys) >= 0 and max(ys) <= 1, f"method={lay['method']} aligned={lay['aligned']}")

    # ── 5. lineage ──
    ln = json.loads((OBS / "lineage.json").read_text(encoding="utf-8"))
    snaps = sorted(p.name for p in (MOL / "snapshots").glob("*.json"))
    check("lineage.snapshots == 스냅샷 파일", ln["snapshots"] == snaps, f"{len(snaps)}개")
    st = Counter(m["status"] for m in ln["molecules"].values())
    if len(snaps) == 1:
        check("스냅샷 1개 → 전부 born · events []", set(st) == {"born"} and ln["events"] == [], str(dict(st)))
    else:
        ev = ln["events"][-1]
        a = json.loads((MOL / "snapshots" / ev["from"]).read_text(encoding="utf-8"))
        b = json.loads((MOL / "snapshots" / ev["to"]).read_text(encoding="utf-8"))
        A = {m["id"] for m in a["molecules"]}
        B = {m["id"] for m in b["molecules"]}
        check("events 마지막 전이: persist == |A∩B|", ev["persist"] == len(A & B), f"{ev['persist']}")
        check("born+continue == |B−A| · dissolved+continue == |A−B|",
              ev["born"] + ev["continue"] == len(B - A) and ev["dissolved"] + ev["continue"] == len(A - B),
              f"born {ev['born']} continue {ev['continue']} dissolved {ev['dissolved']} merged {ev['merged']} split {ev['split']}")
        check("최신 스냅샷 밖 분자는 dissolved", all(m["status"] == "dissolved" for mid, m in ln["molecules"].items() if mid not in B)
              and all(m["status"] != "dissolved" for mid, m in ln["molecules"].items() if mid in B), str(dict(st)))
    if ln.get("sweep"):
        latest_snap = json.loads((MOL / "snapshots" / snaps[-1]).read_text(encoding="utf-8"))
        pct = str(float((latest_snap.get("params") or {}).get("pct") or 0.5))
        if pct in ln["sweep"]:
            check(f"sweep[{pct}] == 최신 스냅샷 분자 id 집합", set(ln["sweep"][pct]) == {m["id"] for m in latest_snap["molecules"]},
                  f"{len(ln['sweep'][pct])} · 스냅샷 {len(latest_snap['molecules'])}")
        check("sweep 키 5개", list(ln["sweep"].keys()) == ["0.25", "0.5", "1.0", "2.0", "3.0"], str(list(ln["sweep"].keys())))
    else:
        rec("SKIP", "sweep", ln.get("sweep_note") or "없음")

    # ── 6. 신호 ──
    sig = {s["key"]: s for s in last["signals"]}
    on = {k for k, s in sig.items() if s["on"]}
    check("신호 10개 · 필수 키", len(sig) == 10 and all({"key", "level", "value", "threshold", "on", "hint"} <= set(s) for s in sig.values()))
    check("기획의 다섯 신호 켜짐", EXPECT_ON <= on, f"켜짐 {sorted(on)}")
    check("stale-observe 꺼짐(빌드 직후)", not sig["stale-observe"]["on"])
    extra = on - EXPECT_ON
    if extra:
        rec("INFO", "다섯 외 켜진 신호", ", ".join(f"{k}: {sig[k]['hint'][:80]}" for k in sorted(extra)))

    # ── 7. 참조값 표 ──
    print("\n참조값(기획 260907, 원자 1,297 기준) ↔ 지금:")
    for k, v in (("report", orig["report"]), ("expansion", orig["expansion"]), ("wiki", orig["wiki"]),
                 ("list_like", last["list_like"]), ("molecules", last["molecules"]), ("named", last["named"]),
                 ("wiki_only_molecules", last["wiki_only_molecules"]), ("wiki_only_named", last["wiki_only_named"]),
                 ("missing_atoms", last["missing_atoms"]), ("links", last["links"]), ("dup_0.10", last["dup_pairs"])):
        print(f"  {k:22} 참조 {REF[k]:>4} · 지금 {v:>4} {'=' if REF[k] == v else '≠'}")
    print(f"  {'categorized':22} 참조 {REF['categorized_legacy']:>4}(+정정·대화 원자) · 지금 {last['categorized']:>4}")
    print(f"  {'with_coords':22} 참조 {REF['with_coords_legacy']:>4}(+정정 원자) · 지금 {last['with_coords']:>4}")

    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\n{'PASS' if not fails else 'FAIL'} — 검사 {len(results)}건 · 실패 {len(fails)} · "
          f"{sum(1 for r in results if r[0] == 'STALE')} stale · {sum(1 for r in results if r[0] == 'SKIP')} skip")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
