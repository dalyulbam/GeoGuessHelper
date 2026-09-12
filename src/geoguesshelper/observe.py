"""관측(observe) — 지식 저장소의 **파생 사실**을 사이드카로 만든다. LLM 0 · 원자 무변경.

관측소(docs/plan/atom-observatory_260907.html)는 "저장되고 있다·쓰이고 있다"를 보여주는 화면이다.
그 화면이 읽을 재료를 이 모듈이 만든다 — 원자 파일·분자 문서·index.json 은 **읽기만** 하고,
산출은 전부 docs/knowledge/observe/ 아래에만 쓴다(구현 명세 docs/plan/impl-spec_260907.md §5).

왜 원자 md 에 쓰지 않고 따로 두나: 여기서 만드는 것은 파생(최근접 거리·중복 후보·분자 소속·
회상 횟수·2D 좌표)이지 사실이 아니다. 파생을 사실 옆에 쓰면 원자의 내용 주소(해시)가 흔들리고,
임베딩이 바뀔 때마다 1,297개 파일이 갱신된다. 사이드카는 통째로 지워도 잃는 것이 없다.

산출(docs/knowledge/observe/):
    atom_meta.json   원자별 파생 — origin·has_coords·n_sources·list_like·nn1·dup_of·molecules·recalls·correction
    growth.json      일별 신규 원자(경로별) — created 로 소급 재구성
    series.jsonl     빌드마다 한 줄(추가 전용) — 집계 + 신호 10개
    layout.json      임베딩 2D 투영(umap → tsne → pca) · 이전 레이아웃에 프로크루스테스 정렬
    neighbors.json   상위 k 이웃 · 상호 kNN 엣지 · 전 쌍 거리 분위표 201개(pct → ε)
    dups.json        거리 < τ 쌍(근중복 후보 — 판정은 사람이 한다) + union-find 전이 묶음(groups, 260908)
    link_candidates.json  상호 kNN 그래프의 공통 이웃(Adamic-Adar) 링크 후보 — refs 자동 제안 재료(260908)
    lineage.json     스냅샷 간 분자 계보(persist/continue/born/dissolved/merged/split, 헝가리안 최적매칭 260908) + --sweep 지속성
    README.md        파일 설명·스키마·재실행법·이번 빌드 요약

실행(시스템 Python — numpy·scikit-learn 은 uv 환경에 없다):
    PYTHONUTF8=1 PYTHONPATH=src python -m geoguesshelper.observe build [--sweep] [--no-layout] [--tau-dup 0.10] [--k 15]
uv 환경(numpy 없음)에서도 돈다 — layout/neighbors/dups 만 건너뛰고 나머지(atom_meta·growth·series·lineage)는
만든다(단계적 저하). 어느 경로에서도 docs/knowledge/atoms/·molecule/·index.json 에는 쓰지 않는다 —
그래서 knowledge.Store 를 쓰지 않는다(Store.index() 는 캐시가 없으면 index.json 을 **쓴다**).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

SWEEP_PCTS = (0.25, 0.5, 1.0, 2.0, 3.0)
ORIGINS = ("report", "expansion", "wiki", "correction", "dialogue", "baseline")
# 목록형(list_like) 판정 — 기획 §8 list-dominance. 경로=위키 + 제목의 복수 정치체 어휘. 1차 추정 217개.
_LIST_LIKE_RE = re.compile(
    r"\b(list|lists|dynasties|kingdoms|states|empires|rulers|monarchs|polities|chiefdoms|republics|"
    r"duchies|regimes|principalities)\b",
    re.I,
)
_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.S)


# ── 경로 ─────────────────────────────────────────────────────────
class ObsDir:
    def __init__(self, knowledge_dir: Path) -> None:
        self.know = Path(knowledge_dir)
        self.root = self.know / "observe"
        self.atom_meta = self.root / "atom_meta.json"
        self.growth = self.root / "growth.json"
        self.series = self.root / "series.jsonl"
        self.layout = self.root / "layout.json"
        self.neighbors = self.root / "neighbors.json"
        self.dups = self.root / "dups.json"
        self.link_candidates = self.root / "link_candidates.json"
        self.lineage = self.root / "lineage.json"
        self.readme = self.root / "README.md"
        # 읽기만 하는 곳
        self.index = self.know / "index.json"
        self.atoms_dir = self.know / "atoms"
        self.mol = self.know / "molecule"
        self.baseline = self.know / "baseline"
        self.corrections = self.know / "corrections"
        self.recall_log = self.know / "recall_log.jsonl"


# ── 읽기 도우미 ───────────────────────────────────────────────────
def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _read_jsonl(path: Path) -> list[dict]:
    """한 줄씩. 깨진 줄(다른 프로세스가 쓰는 중인 마지막 줄 등)은 건너뛴다 — 로그는 동시 쓰기 대상이다."""
    rows: list[dict] = []
    if not path.exists():
        return rows
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            rows.append(d)
    return rows


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _day(ts) -> str | None:
    t = _f(ts)
    return time.strftime("%Y-%m-%d", time.localtime(t)) if t else None


def _iso(ts) -> str | None:
    t = _f(ts)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else None


def _write_json(path: Path, obj: Any, *, indent: int | None = None) -> None:
    """tmp + replace — 뷰어가 읽는 도중 반쯤 쓰인 파일을 보지 않도록."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if indent is None:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=indent)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── 원자 index ────────────────────────────────────────────────────
def _load_index(od: ObsDir) -> tuple[dict, float]:
    """index.json 의 atoms 맵과 그 mtime. 없으면 원자 파일에서 **메모리에만** 재구축한다
    (knowledge.Store.index() 는 재구축 결과를 파일로 쓰므로 여기서는 쓰지 않는다)."""
    idx = _read_json(od.index)
    if isinstance(idx, dict) and isinstance(idx.get("atoms"), dict):
        return idx, od.index.stat().st_mtime
    atoms: dict[str, dict] = {}
    latest = 0.0
    for p in sorted(od.atoms_dir.glob("atm_*.md")) if od.atoms_dir.exists() else []:
        try:
            m = _FM_RE.match(p.read_text(encoding="utf-8"))
            fm = json.loads(m.group(1)) if m else None
        except Exception:  # noqa: BLE001
            fm = None
        if isinstance(fm, dict) and fm.get("id"):
            fm.pop("body", None)
            atoms[fm["id"]] = fm
            latest = max(latest, p.stat().st_mtime)
    return {"atoms": atoms}, latest


def origin_of(meta: dict) -> str:
    """원자 출처 경로. 새 원자는 `origin` 필드(명세 §1.1), 구 원자는 §1.6 규칙 —
    reports 비어 있지 않음 → report · 태그 `x-` 접두 → expansion · 나머지(위키 인제스트) → wiki.
    규칙이 report 를 먼저 보므로 reports 와 x- 태그를 둘 다 가진 원자(1개)는 report 로 간다 —
    그래서 expansion 은 체크포인트 expansion.json 의 created=617 이 아니라 616 으로 집계된다."""
    o = meta.get("origin")
    if o in ORIGINS:
        return o
    if meta.get("reports"):
        return "report"
    if any(str(t).startswith("x-") for t in (meta.get("tags") or [])):
        return "expansion"
    return "wiki"


def is_list_like(meta: dict, origin: str) -> bool:
    return origin == "wiki" and bool(_LIST_LIKE_RE.search(meta.get("title") or ""))


# ── 분자 쪽 ──────────────────────────────────────────────────────
def _load_molecules(od: ObsDir) -> tuple[dict, list[dict]]:
    mi = _read_json(od.mol / "index.json", {}) or {}
    if not isinstance(mi, dict):
        mi = {}
    mols = mi.get("molecules") if isinstance(mi.get("molecules"), dict) else {}
    latest = [m for m in mols.values() if isinstance(m, dict) and m.get("in_latest") and m.get("id")]
    return mi, latest


def _snapshots(od: ObsDir) -> list[tuple[str, dict]]:
    """스냅샷 파일을 created(없으면 mtime) 순으로. 파일명 접두가 %y%m%d_%H%M%S 라 이름순과 같다."""
    out: list[tuple[float, str, dict]] = []
    sd = od.mol / "snapshots"
    for p in sorted(sd.glob("*.json")) if sd.exists() else []:
        d = _read_json(p)
        if isinstance(d, dict) and isinstance(d.get("molecules"), list):
            out.append((_f(d.get("created")) or p.stat().st_mtime, p.name, d))
    out.sort(key=lambda t: (t[0], t[1]))
    return [(name, d) for _, name, d in out]


def _doc_grounded(od: ObsDir, mol_id: str) -> bool | None:
    """mol_*.md 의 '## 검색 근거' 절에 진짜 근거 불릿이 있는가. 문서가 없으면 None.
    (search_findings 는 프론트매터에 없고 본문 절로만 남는다 — molecule._write_doc 참고.)"""
    p = od.mol / f"{mol_id}.md"
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^## 검색 근거\r?\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        return False
    return any(ln.startswith("- ") and not ln.startswith("- (") for ln in m.group(1).splitlines())


# ── 정정·회상·기준선·잡 ───────────────────────────────────────────
def _load_corrections(od: ObsDir) -> tuple[list[dict], list[dict]]:
    """corr_<job>.json 들과 corrections.jsonl 줄. WP-A2 가 동시에 쓰고 있을 수 있다 — 깨진 것은 건너뛴다."""
    files: list[dict] = []
    if od.corrections.exists():
        for p in sorted(od.corrections.glob("corr_*.json")):
            d = _read_json(p)
            if isinstance(d, dict):
                files.append(d)
    return files, _read_jsonl(od.corrections / "corrections.jsonl")


def _hit(v) -> float | None:
    """country_hit 류 — bool 또는 'hit|miss|unknown' 두 표기를 모두 받는다."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().lower()
    return 1.0 if s == "hit" else 0.0 if s == "miss" else None


def _corrections_summary(files: list[dict], rows: list[dict]) -> dict:
    recs = rows
    if not recs and files:
        # 시계열 파일이 아직 없으면 잡 파일에서 같은 모양을 만든다.
        recs = []
        for d in files:
            v = d.get("verdict") if isinstance(d.get("verdict"), dict) else {}
            p2 = ((d.get("blind") or {}).get("phase2") or {}) if isinstance(d.get("blind"), dict) else {}
            recs.append({"at": d.get("created"), "job_id": d.get("job_id"), "country_hit": v.get("country"),
                         "region_hit": v.get("region"), "error_km": v.get("error_km"),
                         "revised": p2.get("revised_from") if isinstance(p2, dict) else None,
                         "cost_usd": d.get("cost_usd")})
    ch = [x for x in (_hit(r.get("country_hit")) for r in recs) if x is not None]
    rh = [x for x in (_hit(r.get("region_hit")) for r in recs) if x is not None]
    errs = sorted(x for x in (_f(r.get("error_km")) for r in recs) if x is not None)
    med = None
    if errs:
        mid = len(errs) // 2
        med = errs[mid] if len(errs) % 2 else (errs[mid - 1] + errs[mid]) / 2
    return {
        "n": len(recs),
        "country_hit_rate": round(sum(ch) / len(ch), 3) if ch else None,
        "region_hit_rate": round(sum(rh) / len(rh), 3) if rh else None,
        "median_error_km": round(med, 2) if med is not None else None,
        "revised": sum(1 for r in recs if r.get("revised")),
        "cost_usd": round(sum(_f(r.get("cost_usd")) or 0.0 for r in recs), 4),
        "recent": [{"at": r.get("at"), "job_id": r.get("job_id"), "truth": r.get("truth_country") or r.get("truth_iso"),
                    "blind": r.get("blind_country") or r.get("blind_iso"), "country_hit": r.get("country_hit"),
                    "error_km": r.get("error_km")} for r in recs[-5:]],
    }


def _correction_index(files: list[dict]) -> dict[str, dict]:
    """원자 → {job_id, n}: 어느 정정 잡이 이 원자를 만들었거나 병합했나(역색인). 여러 잡이면 마지막 잡 + 횟수."""
    out: dict[str, dict] = {}
    for d in sorted(files, key=lambda x: _f(x.get("created")) or 0.0):
        atoms = d.get("atoms") if isinstance(d.get("atoms"), dict) else {}
        for key in ("created", "merged"):
            for aid in atoms.get(key) or []:
                if isinstance(aid, str):
                    e = out.setdefault(aid, {"job_id": d.get("job_id"), "n": 0})
                    e["job_id"] = d.get("job_id")
                    e["n"] += 1
    return out


def _recall_agg(rows: list[dict]) -> tuple[dict[str, dict], dict]:
    """recall_log.jsonl → 원자별 {n, last, mean_rank} + 로그 전체 요약(기간·줄 수)."""
    agg: dict[str, dict] = {}
    ats: list[float] = []
    for r in rows:
        aid = r.get("atom")
        if not isinstance(aid, str):
            continue
        at = _f(r.get("at"))
        if at:
            ats.append(at)
        e = agg.setdefault(aid, {"n": 0, "last": 0.0, "rs": 0.0, "rn": 0})
        e["n"] += 1
        e["last"] = max(e["last"], at or 0.0)
        rk = _f(r.get("rank"))
        if rk is not None:
            e["rs"] += rk
            e["rn"] += 1
    out = {aid: {"n": e["n"], "last": e["last"] or None,
                 "mean_rank": round(e["rs"] / e["rn"], 2) if e["rn"] else None} for aid, e in agg.items()}
    info = {"rows": len(rows), "first": min(ats) if ats else None, "last": max(ats) if ats else None,
            "span_days": round((max(ats) - min(ats)) / 86400.0, 2) if ats else None}
    return out, info


def _tiers(od: ObsDir) -> tuple[dict | None, dict]:
    """baseline/tiers.json → summary{unaided,aided,blind-only} + 샌드박스 aided 비율 재료."""
    t = _read_json(od.baseline / "tiers.json")
    if not isinstance(t, dict):
        return None, {}
    summary = t.get("summary") if isinstance(t.get("summary"), dict) else {}
    tiers = t.get("tiers") if isinstance(t.get("tiers"), dict) else {}
    total = sum(len(v) for v in tiers.values() if isinstance(v, list)) or sum(int(v or 0) for v in summary.values())
    aided = len(tiers.get("aided") or []) if tiers else int(summary.get("aided") or 0)
    return {k: int(summary.get(k) or 0) for k in ("unaided", "aided", "blind-only")}, {"aided": aided, "total": total}


def _jobs_merge(jobs_dir: Path, last: int = 10) -> dict:
    """jobs.jsonl 의 result.knowledge{created, merged} — 같은 잡 id 는 뒤 줄이 최신. 최근 last 개."""
    by: dict[str, dict] = {}
    for r in _read_jsonl(jobs_dir / "jobs.jsonl"):
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        k = res.get("knowledge")
        if isinstance(k, dict) and r.get("id"):
            by[str(r["id"])] = k
    recent = list(by.values())[-last:]
    return {"n_jobs": len(recent),
            "created": sum(int(_f(k.get("created")) or 0) for k in recent),
            "merged": sum(int(_f(k.get("merged")) or 0) for k in recent)}


def _spent(od: ObsDir, mol_index: dict, corr: dict) -> dict:
    exp = _read_json(od.know / "expansion.json", {}) or {}
    wik = _read_json(od.know / "wiki.json", {}) or {}
    base = 0.0
    runs = od.baseline / "runs"
    for p in sorted(runs.glob("*.json")) if runs.exists() else []:
        d = _read_json(p)
        if isinstance(d, dict):
            base += _f(d.get("cost_usd")) or 0.0
    return {"expansion": round(_f(exp.get("cost_usd")) or 0.0, 4) if isinstance(exp, dict) else 0.0,
            "wiki": round(_f(wik.get("cost_usd")) or 0.0, 4) if isinstance(wik, dict) else 0.0,
            "molecule": round(_f(mol_index.get("spent_usd")) or 0.0, 4),
            "baseline": round(base, 4),
            "corrections": corr.get("cost_usd") or 0.0}


# ── 임베딩 (numpy 가 있을 때만) ───────────────────────────────────
def _numpy():
    try:
        import numpy as np  # type: ignore
        return np
    except Exception:  # noqa: BLE001
        return None


def _load_embeddings(od: ObsDir, np) -> dict | None:
    p = od.mol / "embeddings.npz"
    if not p.exists():
        return None
    with np.load(p) as z:
        ids = [str(x) for x in z["ids"].tolist()]
        V = z["vectors"].astype("float32")
        model = str(z["model"]) if "model" in z.files else ""
        created = float(z["created"]) if "created" in z.files else None
    return {"ids": ids, "V": V, "model": model, "created": created}


def _distances(np, V):
    # V 는 float32 — 곱은 float32 로 하고 결과를 float64 로 올린다. 판정(D ≤ ε)·분위수·직렬화를 전부 float64 로
    # 통일해야 브라우저(Number = float64) 비교와 같아진다. float32 배열에 Python float 임계를 비교하면 NumPy 가
    # 임계를 float32 로 내려 비교해 브라우저와 경계 쌍이 갈릴 수 있었다(codex 2차 260908). molecule.py 도 같다.
    D = (1.0 - V @ V.T).astype("float64")      # L2 정규화 벡터 → 1 − cos
    np.fill_diagonal(D, 0.0)
    return np.clip(D, 0.0, 2.0)


def _knn(np, D, k: int):
    """각 행의 상위 k 이웃(거리 오름차순)과 상호 kNN 마스크."""
    n = D.shape[0]
    k = max(1, min(k, n - 1))
    Dm = D.copy()
    np.fill_diagonal(Dm, np.inf)
    part = np.argpartition(Dm, k - 1, axis=1)[:, :k]
    rows = np.arange(n)[:, None]
    order = np.argsort(Dm[rows, part], axis=1)
    nn = part[rows, order]
    mask = np.zeros((n, n), dtype=bool)
    mask[np.repeat(np.arange(n), k), nn.ravel()] = True
    return nn, (mask & mask.T), k


def _procrustes(np, Y_all, Q, P):
    """공통점 Q(새) 를 P(이전) 에 맞추는 회전·반전·스케일·평행이동을 구해 Y_all 전체에 적용.
    반환 (정렬된 좌표, disparity = 잔차²/‖P‖²)."""
    muQ, muP = Q.mean(axis=0), P.mean(axis=0)
    Qc, Pc = Q - muQ, P - muP
    nQ, nP = float(np.linalg.norm(Qc)), float(np.linalg.norm(Pc))
    if nQ < 1e-12 or nP < 1e-12:
        return Y_all, None
    U, S, Vt = np.linalg.svd(Qc.T @ Pc)
    R = U @ Vt                       # 반전 허용 — t-SNE 는 실행마다 좌우가 뒤집힐 수 있다
    s = float(S.sum()) / (nQ ** 2)
    fit = s * (Qc @ R) + muP
    disparity = float(((fit - Pc - muP) ** 2).sum() / (nP ** 2))
    return s * ((Y_all - muQ) @ R) + muP, round(disparity, 6)


def _layout(np, V, ids: list[str], prev: dict | None, log) -> dict:
    """2D 투영. umap → sklearn TSNE(init=pca) → PCA. 이전 layout.json 이 있으면 공통 원자로 프로크루스테스
    정렬해 화면에서 점이 튀지 않게 한다. 좌표는 마지막에 [0,1] 로 정규화한다(정렬 뒤 min-max — 이전과
    수치가 완전히 같지는 않지만 배치가 같다)."""
    n = len(ids)
    Y = None
    method = None
    try:
        import umap  # type: ignore

        Y = umap.UMAP(n_neighbors=15, min_dist=0.1, random_state=0).fit_transform(V)
        method = "umap"
    except Exception:  # noqa: BLE001 — umap-learn 은 이 머신에 없다(명세). 있으면 그대로 쓴다.
        Y = None
    if Y is None:
        try:
            from sklearn.manifold import TSNE  # type: ignore

            perp = max(2.0, min(30.0, (n - 1) / 3.0))
            Y = TSNE(n_components=2, init="pca", random_state=0, perplexity=perp).fit_transform(V)
            method = "tsne"
        except Exception as exc:  # noqa: BLE001
            log(f"[layout] TSNE 를 쓸 수 없어 PCA 로 내려갑니다: {exc}")
            Y = None
    if Y is None:
        Xc = V - V.mean(axis=0)
        _, _, Vt = np.linalg.svd(Xc, full_matrices=False)
        Y = Xc @ Vt[:2].T
        method = "pca"
    Y = np.asarray(Y, dtype="float64")
    aligned, disparity, ref = False, None, None
    if isinstance(prev, dict) and isinstance(prev.get("points"), list):
        pp = {p[0]: (float(p[1]), float(p[2])) for p in prev["points"] if isinstance(p, list) and len(p) == 3}
        common = [i for i, aid in enumerate(ids) if aid in pp]
        if len(common) >= 3:
            P = np.array([pp[ids[i]] for i in common])
            Y, disparity = _procrustes(np, Y, Y[common], P)
            aligned, ref = True, prev.get("fitted_at")
    mn = Y.min(axis=0)
    span = np.maximum(Y.max(axis=0) - mn, 1e-9)
    Y01 = (Y - mn) / span
    return {"method": method, "fitted_at": time.time(), "n": n, "ref_layout": ref, "aligned": aligned,
            "disparity": disparity,
            "points": [[aid, round(float(x), 4), round(float(y), 4)] for aid, (x, y) in zip(ids, Y01)]}


def _sweep(np, ids: list[str], V, index_atoms: dict, mol_index: dict, log) -> tuple[dict | None, str]:
    """pct 0.25·0.5·1·2·3 에서의 분자 id 집합 — molecule.py 의 빌드 내부 함수(_distances·_graph_for·
    _molecules_from)를 **저장 없이** 재사용한다(cmd_build 는 저장 경로와 sweep 출력이 섞여 있어 그대로 못 쓴다).
    파라미터는 최신 index 의 params(knn·min_size·merge·merge_mode·mutual)를 따른다 — 그래야 pct 0.5 집합이
    스냅샷과 같아진다(검증 260907: 120/120 일치)."""
    try:
        from . import molecule as M
        from .knowledge import Atom
    except Exception as exc:  # noqa: BLE001
        return None, f"molecule 모듈을 불러올 수 없음: {exc}"
    try:
        import networkx  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        return None, "networkx 없음(시스템 Python 에는 있다)"
    params = mol_index.get("params") if isinstance(mol_index.get("params"), dict) else {}
    knn = int(params.get("knn") or 10)
    min_size = int(params.get("min_size") or 4)
    merge = float(params.get("merge")) if params.get("merge") is not None else 0.5
    mode = params.get("merge_mode") or "absorb"
    mutual = bool(params.get("mutual", True))
    allowed = set(Atom.__dataclass_fields__) - {"body"}  # type: ignore[attr-defined]
    atoms = {}
    for aid, m in index_atoms.items():
        try:
            atoms[aid] = Atom(**{k: v for k, v in m.items() if k in allowed}, body="")
        except TypeError:
            continue
    D = M._distances(V)
    out: dict[str, list[str]] = {}
    thr: dict[str, float] = {}
    trunc: dict[str, bool] = {}
    t0 = time.time()
    for p in SWEEP_PCTS:
        G, t, _ = M._graph_for(D, pct=p, knn=knn, mutual=mutual)
        mols, _, truncated = M._molecules_from(G, D, ids, atoms, min_size=min_size, merge=merge, merge_mode=mode)
        out[str(p)] = sorted(m.id for m in mols)
        thr[str(p)] = float(t)
        trunc[str(p)] = bool(truncated)   # 열거가 끊긴 pct 의 집합은 불완전 — 계보 지속성에서 "없음"으로 확정하면 안 된다
    log(f"[sweep] pct {', '.join(f'{p}→{len(out[str(p)])}' for p in SWEEP_PCTS)} 분자 · {time.time() - t0:.1f}s"
        + (f" · ⚠ 열거 중단 pct {[p for p, v in trunc.items() if v]}" if any(trunc.values()) else ""))
    return {"pcts": out, "thresholds": thr, "truncated": trunc,
            "params": {"knn": knn, "min_size": min_size, "merge": merge, "merge_mode": mode, "mutual": mutual}}, ""


# ── 근중복 전이 묶음·링크 후보(그래프이론 260908) ──────────────────────
def _dup_groups(pairs: list[dict]) -> list[dict]:
    """근중복 '쌍' 목록을 전이적 연결요소(union-find)로 묶어 검토 단위를 만든다 — A~B, B~C 가 각각 쌍으로만
    보이면 사람이 B 를 두 번 검토하고 A~C 차이를 직접 맞춰봐야 한다(codex 감사 260908, low). pairs 자체는
    그대로 둔다 — "판정은 사람" 원칙, dups.json 은 지우지 않는다(§ dups.json 문서). 이건 보조 뷰일 뿐 그룹을
    "같은 사실"로 확정하지 않는다 — 그룹 안에서도 직접 후보 엣지와 임계 밖(전이로만 묶인) 쌍은 다르다."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        ra, rb = find(p["a"]), find(p["b"])
        if ra != rb:
            parent[ra] = rb
    members: dict[str, set[str]] = {}
    for aid in parent:
        members.setdefault(find(aid), set()).add(aid)
    groups: list[dict] = []
    for ids in members.values():
        if len(ids) < 2:
            continue
        gpairs = [p for p in pairs if p["a"] in ids and p["b"] in ids]
        direct_edges = len(gpairs)
        max_edges = len(ids) * (len(ids) - 1) // 2
        groups.append({
            "atoms": sorted(ids), "n": len(ids), "direct_pairs": direct_edges,
            "transitive_only": direct_edges < max_edges,
            "d_min": round(min(p["d"] for p in gpairs), 4), "d_max": round(max(p["d"] for p in gpairs), 4),
        })
    groups.sort(key=lambda g: (g["d_min"], -g["n"]))
    return groups


def _link_candidates(ids: list[str], mutual_edges: list[list], atoms: dict[str, dict], *, D=None, eps: float | None = None,
                     far_thr: float | None = None, top: int = 200) -> dict:
    """공통 이웃(Adamic-Adar, Zhou·Lü·Zhang 2009 "Predicting Missing Links via Local Information") 기반 링크
    후보 — 상호 kNN 그래프에서 서로 안 이어졌지만 공통 이웃을 많이(그 이웃이 저차수일수록 더 무겁게) 공유하는
    원자 쌍을 낸다. refs(대화가 사람 승인으로 원자끼리 잇는 것)는 지금 사람이 대화 중에만 채우고 자동 후보가
    없다는 gap과, molecule-theory_260907 §3.2·§7 "다음" 5번이 남긴 숙제(임베딩 클리크로는 못 찾는 허브형
    개념 — 공통 이웃 삼각형에서 찾아야 한다)를 겨냥한다(그래프이론 리서치 260908, angle 2·3 공통 adopt).

    직접 안 이어진 모든 쌍(O(n^2))을 도는 대신 각 노드를 "공통 이웃"으로 보고 그 이웃끼리만 짝짓는다
    (O(Σ deg^2)) — 상호 kNN 은 성기므로(k=15) 원자 수천 개로 늘어도 가볍다. molecule.py 의 밀집 n×n 행렬
    할당(codex 감사 260908)과 같은 함정을 피한다.

    1차 재검토(260908, fable) 실측: 상위 200 중 130 이 한쪽 kNN15 에는 있는 "거의 이웃"이고 상위는 목록형
    위키 원자가 지배했다 — Adamic-Adar 만으로는 "가까운데 상호 kNN 컷에 걸린 쌍"이 나올 뿐, 허브형(멀지만
    공통 이웃이 잇는 쌍)은 안 나온다. 그래서 (1) 각 후보에 실제 거리 d 와 목록형 여부를 붙이고, (2) 별도로
    `bridges` — 목록형 제외 · d ≥ far_thr(pct 1.0 임계) · 공통 이웃 ≥ 5 인 쌍을 score×d 로 순위 — 를 낸다.
    bridges 가 molecule-theory §3.2 의 '부재 노드' 후보다(잠재 변수: 서로 멀지만 같은 것들과 가깝다).

    반환 {candidates:[…AA 순], bridges:[…], n_pairs_scored, params}."""
    adj: dict[int, set[int]] = {}
    for i, j, *_ in mutual_edges:
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)
    direct = {(min(i, j), max(i, j)) for i, j, *_ in mutual_edges}

    ll_cache: dict[str, bool] = {}

    def list_like(aid: str) -> bool:
        if aid not in ll_cache:
            m = atoms.get(aid) or {}
            ll_cache[aid] = is_list_like(m, origin_of(m)) if m else False
        return ll_cache[aid]

    ll_nodes = {k for k in adj if list_like(ids[k])}
    score: dict[tuple[int, int], float] = {}
    cn: dict[tuple[int, int], int] = {}
    cn_ll: dict[tuple[int, int], int] = {}      # 공통 이웃 중 목록형 원자 수 — 양 끝만 걸러선 목록형 편향이 안 빠진다(codex 2차)
    for k, neigh in adj.items():                 # 점수 누적은 Σ C(deg,2); 그 뒤의 정렬은 O(P log P) — 함수 전체는 그 합이다
        deg = len(neigh)
        if deg < 2:
            continue
        w = 1.0 / math.log(deg)
        is_ll = k in ll_nodes
        nl = sorted(neigh)
        for a in range(len(nl)):
            for b in range(a + 1, len(nl)):
                i, j = nl[a], nl[b]
                if (i, j) in direct:
                    continue
                score[(i, j)] = score.get((i, j), 0.0) + w
                cn[(i, j)] = cn.get((i, j), 0) + 1
                if is_ll:
                    cn_ll[(i, j)] = cn_ll.get((i, j), 0) + 1

    def row(i: int, j: int, sc: float, *, with_common: bool = False) -> dict:
        a, b = ids[i], ids[j]
        ma, mb = atoms.get(a) or {}, atoms.get(b) or {}
        d = float(D[i, j]) if D is not None else None
        n_cn = cn[(i, j)]
        r = {"a": a, "b": b, "score": round(sc, 4), "common_neighbors": n_cn,
             "list_like_cn_share": round(cn_ll.get((i, j), 0) / n_cn, 3) if n_cn else 0.0,
             "d": (round(d, 4) if d is not None else None),
             "below_eps": (bool(d <= eps) if (d is not None and eps is not None) else None),
             "list_like": bool(list_like(a) or list_like(b)),
             "title_a": ma.get("title") or "", "title_b": mb.get("title") or "",
             "layer_a": ma.get("layer"), "layer_b": mb.get("layer")}
        if with_common:
            r["common_ids"] = [ids[k] for k in sorted(adj[i] & adj[j])]
        return r

    ranked = sorted(score.items(), key=lambda kv: -kv[1])[:top]
    cands = [row(i, j, sc) for (i, j), sc in ranked]
    bridges: list[dict] = []
    if D is not None and far_thr is not None:
        pool = []
        for (i, j), sc in score.items():
            n_cn = cn[(i, j)]
            if n_cn < 5:
                continue
            if cn_ll.get((i, j), 0) * 2 > n_cn:            # 공통 이웃 과반이 목록형이면 그 "공통 개념"은 위키 목록일 뿐
                continue
            d = float(D[i, j])
            if d < far_thr or list_like(ids[i]) or list_like(ids[j]):
                continue
            pool.append((sc * d, i, j, sc))
        pool.sort(key=lambda t: -t[0])
        for bsc, i, j, sc in pool[:100]:
            r = row(i, j, sc, with_common=True)
            r["bridge"] = round(bsc, 4)
            bridges.append(r)
    return {"candidates": cands, "bridges": bridges, "n_pairs_scored": len(score),
            "params": {"top": top, "eps": eps, "far_thr": far_thr, "bridge_min_cn": 5,
                       "bridge_excludes_list_like": True, "bridge_max_list_like_cn_share": 0.5,
                       "note": "score×d 는 사람이 검토할 휴리스틱 — 부재 개념의 존재를 보증하지 않는다"}}


# ── 계보 ─────────────────────────────────────────────────────────
def _jacc(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def _continue_matches(old: list[str], new: list[str], A: dict[str, set], B: dict[str, set]) -> tuple[list[list], set[str], str]:
    """new↔old 중 자카드 ≥0.5 인 쌍을 전역 최적 1:1 매칭(헝가리안, scipy.optimize.linear_sum_assignment)한다.
    반환 (cont, matched_old, method) — method 는 "hungarian" | "greedy"(scipy 없음) | "none"(후보 없음).

    이전 구현은 new 를 순서대로 훑으며 그때까지 최고 자카드인 old 를 그리디로 선점했다 — old 를 후보에서
    빼지 않아 서로 다른 new 두 개가 같은 old 를 "최고"로 골라 하나가 유효한 매칭을 잃을 수 있었다(반례:
    old={A1,A2}, new={B1,B2} 인데 A1↔B1 자카드 0.8, A1↔B2 자카드 0.6, A2↔B2 자카드 0.55 면 그리디는
    B1,B2 모두 A1 을 고르고 A2 는 dissolved 로 잘못 떨어진다 — codex 감사 260908). 헝가리안은 비용 합(1-자카드)이
    전역 최소인 매칭을 찾으므로 이 경우 B1↔A1, B2↔A2 두 쌍을 모두 살린다.

    미매칭 벌점(NO_MATCH)은 "매칭 수 최대화가 자카드 합 최대화보다 우선"하도록 커야 한다 — 고정 10.0 이면
    22개 사슬형 반례에서 21개 매칭+강제 1개(10.14)가 22개 완전 매칭(10.92)보다 싸져 유효한 continue 하나를
    버린다(codex 2차 260908). 진짜 후보 비용은 각각 ≤0.5 이고 최대 min(|new|,|old|) 개이므로
    NO_MATCH > 0.5·min(|new|,|old|) 이면 강제 배정 하나를 줄이는 쪽이 항상 싸다 — 1+max(|new|,|old|) 로 둔다."""
    if not old or not new:
        return [], set(), "none"
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment
    except ImportError:
        cont, matched = _continue_matches_greedy(old, new, A, B)
        return cont, matched, "greedy"
    NO_MATCH = 1.0 + float(max(len(new), len(old)))
    cost = np.full((len(new), len(old)), NO_MATCH)
    jacc: dict[tuple[int, int], float] = {}
    for ni, nb in enumerate(new):
        for oi, oa in enumerate(old):
            j = _jacc(B[nb], A[oa])
            if j >= 0.5:
                cost[ni, oi] = 1.0 - j
                jacc[(ni, oi)] = j
    rows, cols = linear_sum_assignment(cost)
    cont: list[list] = []
    matched: set[str] = set()
    for ni, oi in zip(rows.tolist(), cols.tolist()):
        j = jacc.get((ni, oi))
        if j is None:               # 헝가리안이 강제로 채운, 자카드 0.5 미달인 배정 — 버린다(그 nb 는 born 후보로 남는다)
            continue
        cont.append([old[oi], new[ni], round(j, 3)])
        matched.add(old[oi])
    return cont, matched, "hungarian"


def _continue_matches_greedy(old: list[str], new: list[str], A: dict[str, set], B: dict[str, set]) -> tuple[list[list], set[str]]:
    """scipy 없을 때의 저하 경로 — 그리디(순서의존적). 이미 매칭된 old 는 후보에서 뺀다 — 예전 코드는 빼지 않아
    두 new 가 같은 old 로 continue 되고 old.next 가 덮어써지는 1:1 불변식 위반이 있었다(codex 2차 260908)."""
    cont: list[list] = []
    matched: set[str] = set()
    for nb in new:
        best, bj = None, 0.0
        for oa in old:
            if oa in matched:
                continue
            j = _jacc(B[nb], A[oa])
            if j >= 0.5 and j > bj:
                best, bj = oa, j
        if best:
            cont.append([best, nb, round(bj, 3)])
            matched.add(best)
    return cont, matched


def build_lineage(snaps: list[tuple[str, dict]], latest_mols: dict[str, dict], sweep: dict | None,
                  sweep_note: str) -> dict:
    """스냅샷 간 분자 계보(기획 §분자 관측). 분자 id 는 내용 주소라 같은 id = 같은 멤버 집합.
      persist   같은 id 가 이전·이번 스냅샷에 모두 있다
      continue  id 가 바뀌었지만 이전 스냅샷의 어느 분자와 자카드 ≥ 0.5 (멤버 하나 들고남)
      born      이번에만 · dissolved 이전에만(문서는 지우지 않는다 — 같은 구성이 돌아오면 같은 id)
      merged    새 분자 하나가 이전 분자 둘 이상의 멤버를 각각 절반 이상 품음 · split 그 반대
    스냅샷이 하나면 전부 born, events 는 빈 배열."""
    names = [n for n, _ in snaps]
    sets: list[dict[str, set[str]]] = []
    for _, d in snaps:
        sets.append({m["id"]: set(m.get("atoms") or []) for m in d["molecules"] if isinstance(m, dict) and m.get("id")})
    molecules: dict[str, dict] = {}
    for i, name in enumerate(names):
        for mid in sets[i]:
            e = molecules.setdefault(mid, {"first_seen": name, "last_seen": name, "status": "born",
                                           "prev": None, "next": None, "jaccard": None, "seen": 0})
            e["last_seen"] = name
            e["seen"] += 1
    events: list[dict] = []
    for i in range(1, len(snaps)):
        A, B = sets[i - 1], sets[i]
        persist = sorted(set(A) & set(B))
        old = [x for x in A if x not in B]
        new = [x for x in B if x not in A]
        cont, matched, method = _continue_matches(old, new, A, B)
        matched_new = {nb for _, nb, _ in cont}
        born = [nb for nb in new if nb not in matched_new]
        dissolved = [x for x in old if x not in matched]
        merged = []
        for nb in new:
            srcs = [oa for oa in old if len(B[nb] & A[oa]) >= math.ceil(len(A[oa]) / 2)]
            if len(srcs) >= 2:
                merged.append([nb, sorted(srcs)])
        split = []
        for oa in old:
            dsts = [nb for nb in new if len(B[nb] & A[oa]) >= math.ceil(len(B[nb]) / 2)]
            if len(dsts) >= 2:
                split.append([oa, sorted(dsts)])
        events.append({"from": names[i - 1], "to": names[i], "born": len(born), "dissolved": len(dissolved),
                       "persist": len(persist), "continue": len(cont), "continue_method": method,
                       "merged": len(merged), "split": len(split),
                       "ids": {"born": born, "dissolved": dissolved, "persist": persist, "continue": cont,
                               "merged": merged, "split": split}})
        for mid in persist:
            molecules[mid]["status"] = "persist"
        for oa, nb, j in cont:
            molecules[nb].update({"status": "continue", "prev": oa, "jaccard": j})
            molecules[oa]["next"] = nb
        for mid in born:
            molecules[mid]["status"] = "born"
        for mid in dissolved:
            molecules[mid]["status"] = "dissolved"
    if snaps:
        latest_ids = set(sets[-1])
        for mid, e in molecules.items():
            if mid not in latest_ids:
                e["status"] = "dissolved"
    for mid, e in molecules.items():
        m = latest_mols.get(mid) or {}
        if m.get("name_ko"):
            e["name_ko"] = m["name_ko"]
        if sweep:
            # 열거가 끊긴 pct 는 "그 pct 에서 없다"가 아니라 "모른다" — persistence 에서 제외하고 따로 표시한다
            complete = [p for p in sweep["pcts"] if not (sweep.get("truncated") or {}).get(p)]
            e["persistence"] = [p for p in complete if mid in set(sweep["pcts"][p])]
    return {"built": time.time(), "snapshots": names, "latest": names[-1] if names else None,
            "molecules": molecules, "events": events,
            "sweep": sweep["pcts"] if sweep else None,
            "sweep_thresholds": sweep["thresholds"] if sweep else None,
            "sweep_truncated": sweep.get("truncated") if sweep else None,
            "sweep_params": sweep["params"] if sweep else None,
            "sweep_note": sweep_note or ("pct 별 분자 id 집합 — molecule.py 내부 함수 재사용, 저장 없음" if sweep else
                                         "--sweep 미지정")}


# ── 신호 10개 ─────────────────────────────────────────────────────
def _signals(s: dict, *, index_mtime: float, snap_created: float | None, unembedded: int, tau: float,
             dups_top: list[dict] | None, numpy_ok: bool, tiers_aided: dict, jobs: dict, recall_info: dict,
             point_aided: tuple[int, int]) -> list[dict]:
    """기획 §8 규칙 10개. 파일에서 재현 가능해야 하므로 입력은 전부 series 줄에 있는 숫자다."""
    n = max(1, s["atoms"])
    out: list[dict] = []

    def sig(key, level, value, threshold, on, hint):
        out.append({"key": key, "level": level, "value": value, "threshold": threshold, "on": bool(on), "hint": hint})

    # 1 stale-snapshot — 분자가 원자보다 오래됐다
    if snap_created is None:
        sig("stale-snapshot", "warn", None, "index_mtime > snapshot.created", True,
            "분자 스냅샷이 없다 — embed → build (snapshot-knowledge.ps1)")
    else:
        lag = index_mtime - snap_created
        sig("stale-snapshot", "warn", round(lag), "index_mtime > snapshot.created", lag > 0,
            (f"index.json 이 스냅샷보다 {lag / 3600:.1f}시간 새롭다 · 임베딩 없는 원자 {unembedded}개 → snapshot-knowledge.ps1"
             if lag > 0 else f"스냅샷이 index 보다 {-lag / 3600:.1f}시간 새롭다 · 임베딩 없는 원자 {unembedded}개"))
    # 2 stale-observe — 빌드 시점에는 정의상 꺼져 있다. 뷰어가 index mtime 과 built 를 비교해 켠다.
    sig("stale-observe", "warn", 0, "index_mtime > observe.built", False,
        "빌드 직후라 꺼짐 — 뷰어가 index.json mtime > series.at 이면 켠다 (observe build)")
    # 3 uncategorized
    unc = 1 - s["categorized"] / n
    rep_n = s["by_origin"].get("report", 0)
    unc_rep = (1 - s["categorized_report_origin"] / rep_n) if rep_n else 0.0
    sig("uncategorized", "alert", round(unc, 4), "전체 > 0.20 또는 보고서 유래 > 0.10", unc > 0.20 or unc_rep > 0.10,
        f"범주 없음 {n - s['categorized']}/{n} ({unc:.0%}) · 보고서 유래 {rep_n - s['categorized_report_origin']}/{rep_n} "
        f"({unc_rep:.0%}) → uv run geoguesshelper categorize")
    # 4 dup-candidates
    if s["dup_pairs"] is None:
        sig("dup-candidates", "warn", None, f"d < {tau} 쌍 > 0", False,
            "계산 불가 — " + ("embeddings.npz 없음(molecule embed)" if numpy_ok else "numpy 없음(시스템 Python 으로 실행)"))
    else:
        top = dups_top[0] if dups_top else None
        sig("dup-candidates", "warn", s["dup_pairs"], f"d < {tau} 쌍 > 0", s["dup_pairs"] > 0,
            f"근중복 후보 {s['dup_pairs']}쌍 (τ={tau})" + (f" · 최근접 {top['d']:.3f} “{top['title_a'][:40]}” ↔ “{top['title_b'][:40]}”" if top else "")
            + " · 판정은 사람 — 작업대 근중복 탭")
    # 5 unnamed-backlog
    mols = s["molecules"]
    share = (s["unnamed"] / mols) if mols else 0.0
    sig("unnamed-backlog", "info", round(share, 4), "> 0.20", share > 0.20,
        f"미명명 {s['unnamed']}/{mols} · 예상 ≈ ${s['unnamed'] * 0.05:.2f} → molecule name --order cohesion")
    # 6 list-dominance
    named = s["named"]
    ld = (s["wiki_only_named"] / named) if named else 0.0
    sig("list-dominance", "warn", round(ld, 4), "> 0.40", ld > 0.40,
        f"명명 분자 중 위키 원자만으로 된 것 {s['wiki_only_named']}/{named} (전체 {s['wiki_only_molecules']}/{mols}) · "
        f"목록형 원자 {s['list_like']}개 → 분자 빌드 --exclude list_like 검토")
    # 7 no-grounding
    ng = (s["no_grounding_docs"] / named) if named else 0.0
    sig("no-grounding", "warn", round(ng, 4), "> 0.50", ng > 0.50,
        f"search_findings 빈 문서 {s['no_grounding_docs']}/{named} — 이름이 멤버 원자만으로 지어졌다(가설 등급 확인). 명명 프롬프트에 검색 1회 필수 검토")
    # 8 aided-in-p1 — 본 저장소 point 원자의 tier=aided 비율. 0 건이면 샌드박스(baseline tiers) 값으로 표기만.
    pa, pn = point_aided
    if pn and pa:
        v = pa / pn
        sig("aided-in-p1", "alert", round(v, 4), "> 0.10", v > 0.10, f"본 저장소 point 원자 {pa}/{pn} 이 tier=aided — 지도 라벨이 지점 단서로 굳고 있다")
    else:
        ta, tt = int(tiers_aided.get("aided") or 0), int(tiers_aided.get("total") or 0)
        v = (ta / tt) if tt else 0.0
        sig("aided-in-p1", "alert", round(v, 4), "> 0.10", v > 0.10,
            f"샌드박스(baseline/tiers.json) aided {ta}/{tt} ({v:.1%}) — 본 저장소 point 원자엔 tier 가 없어 샌드박스 값으로만 계산" if tt
            else "tier 정보 없음(baseline 미실행)")
    # 9 merge-rate
    cr, mg = jobs.get("created", 0), jobs.get("merged", 0)
    if jobs.get("n_jobs"):
        rate = (mg / cr) if cr else (1.0 if mg else 0.0)
        sig("merge-rate", "info", round(rate, 4), "> 0.25", rate > 0.25,
            f"최근 {jobs['n_jobs']}개 잡 merged {mg} / created {cr} — 같은 장소 반복 조사 또는 병합 임계 완화 신호")
    else:
        sig("merge-rate", "info", None, "> 0.25", False, "jobs.jsonl 에 result.knowledge 가 있는 잡이 없다")
    # 10 never-recalled
    span = recall_info.get("span_days")
    share_nr = s.get("never_recalled_share")
    if share_nr is None:
        sig("never-recalled", "info", None, "로그 ≥ 30일 · 0회 > 0.80", False, "회상 로그 없음(recall_log.jsonl) — 잡 1건 뒤 생긴다")
    else:
        long_enough = (span or 0) >= 30
        sig("never-recalled", "info", round(share_nr, 4), "로그 ≥ 30일 · 0회 > 0.80", long_enough and share_nr > 0.80,
            f"회상 0회 원자 {share_nr:.0%} · 로그 {recall_info.get('rows')}줄 · {span}일" + ("" if long_enough else " — 로그가 30일 미만이라 판정 보류"))
    return out


# ── 빌드 ─────────────────────────────────────────────────────────
def cmd_build(knowledge_dir: Path, jobs_dir: Path, *, layout: bool = True, sweep: bool = False,
              tau_dup: float = 0.10, k: int = 15, log=print) -> dict:
    t_start = time.time()
    od = ObsDir(knowledge_dir)
    od.root.mkdir(parents=True, exist_ok=True)
    idx, index_mtime = _load_index(od)
    atoms: dict[str, dict] = idx.get("atoms") or {}
    n = len(atoms)
    log(f"[observe] 원자 {n} · index {_iso(index_mtime)} · knowledge={od.know}")

    # 분자 · 스냅샷
    mol_index, latest_mols = _load_molecules(od)
    latest_by_id = {m["id"]: m for m in latest_mols}
    snaps = _snapshots(od)
    snap_name = None
    if isinstance(mol_index.get("latest_snapshot"), str):
        snap_name = Path(mol_index["latest_snapshot"]).name
    elif snaps:
        snap_name = snaps[-1][0]
    snap_created = None
    for name, d in snaps:
        if name == snap_name:
            snap_created = _f(d.get("created"))
    member_of: dict[str, list[str]] = {}
    periphery_of: dict[str, list[str]] = {}
    for m in latest_mols:
        for aid in m.get("atoms") or []:
            member_of.setdefault(aid, []).append(m["id"])
        for aid in m.get("periphery") or []:
            periphery_of.setdefault(aid, []).append(m["id"])

    # 정정·회상·기준선·잡
    corr_files, corr_rows = _load_corrections(od)
    corr_sum = _corrections_summary(corr_files, corr_rows)
    corr_idx = _correction_index(corr_files)
    recall_rows = _read_jsonl(od.recall_log)
    recalls, recall_info = _recall_agg(recall_rows)
    tiers_summary, tiers_aided = _tiers(od)
    jobs = _jobs_merge(jobs_dir)

    # 임베딩 → 거리 · kNN · 중복 · 레이아웃 (numpy 있을 때만)
    np = _numpy()
    emb = _load_embeddings(od, np) if np is not None else None
    nn1: dict[str, dict] = {}
    dup_pairs: list[dict] | None = None
    dup_groups: list[dict] | None = None
    link_candidates: dict | None = None
    neighbors_doc = None
    layout_doc = None
    counts_at: dict[str, int] = {}
    sweep_doc, sweep_note = None, ""
    if emb is None:
        why = "numpy 없음 — 시스템 Python(PYTHONPATH=src python -m geoguesshelper.observe build)" if np is None \
            else "molecule/embeddings.npz 없음 — molecule embed 먼저"
        log(f"[observe] layout/neighbors/dups 건너뜀: {why}")
        if sweep:
            sweep_note = why
    else:
        ids, V = emb["ids"], emb["V"]
        pos = {aid: i for i, aid in enumerate(ids)}
        D = _distances(np, V)
        iu = np.triu_indices(len(ids), 1)
        upper = D[iu]
        # 임계·분위표·엣지 거리 모두 반올림하지 않는다(float64 그대로, JSON 은 왕복 정밀도를 그대로 담는다) —
        # 뷰어가 `d ≤ ε` 로 스냅샷 그래프를 재현할 때 경계 쌍이 반올림 때문에 넘어가거나 빠지면 안 된다.
        # 과거엔 d 를 7자리로 반올림했는데(4자리로 줄이면 엣지 1개가 어긋났다 — 260907), 그래도 float32 값
        # 하나가 정확히 7자리 경계에 걸리면(예 0.10000008344650269 → 0.1000001) 서버 원본 D 와 비교한 결과와
        # 반올림된 사본으로 비교한 결과가 갈릴 수 있다(codex 감사 260908) — 그래서 반올림 자체를 없앤다.
        quantiles = [float(x) for x in np.percentile(upper, np.linspace(0, 100, 201))]
        pct_thr = {str(p): float(np.percentile(upper, p)) for p in SWEEP_PCTS}
        for t in (0.10, 0.12, 0.15, tau_dup):
            counts_at[f"{t:.2f}"] = int((upper < t).sum())
        nn, mutual, k_eff = _knn(np, D, k)
        knn_doc: dict[str, list] = {}
        for i, aid in enumerate(ids):
            row = []
            for j in nn[i].tolist():
                row.append([ids[j], float(D[i, j]), bool(mutual[i, j])])
            knn_doc[aid] = row
            if row:
                nn1[aid] = {"id": row[0][0], "d": row[0][1]}
        # 엣지에 양 끝의 이웃 순위(ri = j 가 i 의 몇 번째 이웃인가, 0 시작)를 함께 둔다 — 상호 kNN15 ⊇ 상호 kNN10 이라,
        # 뷰어가 스냅샷(knn=10)을 정확히 재현하려면 max(ri, rj) < 10 으로 걸러야 한다. 순위 없이는 재현이 안 된다.
        rank_of = [{int(j): r for r, j in enumerate(nn[i].tolist())} for i in range(len(ids))]
        ei, ej = np.nonzero(np.triu(mutual, 1))
        mutual_edges = [[int(i), int(j), float(D[i, j]), rank_of[i][int(j)], rank_of[j][int(i)]]
                        for i, j in zip(ei.tolist(), ej.tolist())]
        mol_params = mol_index.get("params") if isinstance(mol_index.get("params"), dict) else None
        neighbors_doc = {"model": emb["model"], "built": time.time(), "embeddings_created": emb["created"],
                         "k": k_eff, "n": len(ids), "ids": ids, "quantiles": quantiles, "pct_thresholds": pct_thr,
                         "snapshot_params": mol_params, "snapshot_threshold": _f(mol_index.get("threshold")),
                         "knn": knn_doc, "mutual_edges": mutual_edges}
        link_candidates = _link_candidates(ids, mutual_edges, atoms, D=D, eps=_f(mol_index.get("threshold")),
                                           far_thr=pct_thr.get("1.0"))
        # 근중복 — 전 쌍 중 d < τ. 거리 오름차순. 판정은 사람.
        di, dj = np.nonzero(np.triu(D < tau_dup, 1))
        pairs = []
        for i, j in zip(di.tolist(), dj.tolist()):
            a, b = ids[i], ids[j]
            ma, mb = atoms.get(a) or {}, atoms.get(b) or {}
            pairs.append({"a": a, "b": b, "d": round(float(D[i, j]), 4),
                          "title_a": ma.get("title") or "", "title_b": mb.get("title") or "",
                          "origin_a": origin_of(ma) if ma else None, "origin_b": origin_of(mb) if mb else None,
                          "layer_a": ma.get("layer"), "layer_b": mb.get("layer"),
                          "same_report": bool(set(ma.get("reports") or []) & set(mb.get("reports") or []))})
        pairs.sort(key=lambda p: (p["d"], p["a"], p["b"]))
        dup_pairs = pairs
        dup_groups = _dup_groups(pairs)
        log(f"[observe] 쌍 {len(upper):,} · 근중복 d<{tau_dup} {len(pairs)}쌍({len(dup_groups)}묶음) · "
            f"상호 kNN{k_eff} 엣지 {len(mutual_edges):,} · 링크 후보 {len(link_candidates['candidates'])} · "
            f"다리 후보 {len(link_candidates['bridges'])}")
        if layout:
            t0 = time.time()
            layout_doc = _layout(np, V, ids, _read_json(od.layout), log)
            log(f"[observe] layout {layout_doc['method']} · {time.time() - t0:.1f}s · "
                f"{'이전 레이아웃에 정렬(disparity ' + str(layout_doc['disparity']) + ')' if layout_doc['aligned'] else '첫 레이아웃'}")
        if sweep:
            sweep_doc, sweep_note = _sweep(np, ids, V, atoms, mol_index, log)
            if sweep_doc is None:
                log(f"[observe] sweep 불가: {sweep_note}")
        unembedded = sum(1 for aid in atoms if aid not in pos)
        embedded = len(pos)
    if emb is None:
        unembedded, embedded = n, 0

    # ── atom_meta ──
    meta: dict[str, dict] = {}
    by_origin: Counter = Counter()
    by_layer: Counter = Counter()
    by_scope: Counter = Counter()
    by_kind: Counter = Counter()
    by_status: Counter = Counter()
    growth: dict[str, Counter] = {}
    categorized = categorized_report = with_coords = no_sources = no_period = list_like_n = retracted = 0
    point_n = point_aided = 0
    for aid, m in atoms.items():
        o = origin_of(m)
        ll = is_list_like(m, o)
        has = m.get("lat") is not None and m.get("lng") is not None
        kind = m.get("kind") or "fact"
        status = m.get("status") or "active"
        by_origin[o] += 1
        by_layer[m.get("layer") or "?"] += 1
        by_scope[m.get("scope") or "?"] += 1
        by_kind[kind] += 1
        by_status[status] += 1
        cat = (m.get("category") or "").strip()
        if cat:
            categorized += 1
            if o == "report":
                categorized_report += 1
        with_coords += 1 if has else 0
        no_sources += 0 if m.get("sources") else 1
        no_period += 1 if (m.get("period_start") is None and m.get("period_end") is None) else 0
        list_like_n += 1 if ll else 0
        retracted += 1 if status == "retracted" else 0
        if m.get("scope") == "point":
            point_n += 1
            point_aided += 1 if m.get("tier") == "aided" else 0
        day = _day(m.get("created"))
        if day:
            growth.setdefault(day, Counter())[o] += 1
        n1 = nn1.get(aid)
        meta[aid] = {
            "origin": o, "layer": m.get("layer"), "scope": m.get("scope"), "category": cat or None,
            "kind": kind, "status": status, "tier": m.get("tier"), "has_coords": has,
            "n_sources": len(m.get("sources") or []), "n_refs": len(m.get("refs") or []),
            "n_reports": len(m.get("reports") or []), "list_like": ll,
            "nn1": n1, "dup_of": (n1["id"] if n1 and n1["d"] < tau_dup else None),
            "molecules": member_of.get(aid, []), "periphery_of": periphery_of.get(aid, []),
            "hits": int(_f(m.get("hits")) or 0), "misses": int(_f(m.get("misses")) or 0),
            "recalls": recalls.get(aid), "correction": corr_idx.get(aid),
            "created": m.get("created"), "updated": m.get("updated"),
        }
    now = time.time()
    atom_meta = {"built": now, "index_mtime": index_mtime, "n": n, "tau_dup": tau_dup, "k": k,
                 "embedded": embedded, "unembedded": unembedded, "atoms": meta}
    growth_doc = {"built": now, "days": [
        {"day": d, **{o: c.get(o, 0) for o in ORIGINS}, "total": sum(c.values())} for d, c in sorted(growth.items())]}

    # ── 분자 집계 ──
    def _wiki_only(m: dict) -> bool:
        mem = [a for a in (m.get("atoms") or []) if a in atoms]
        return bool(mem) and all(origin_of(atoms[a]) == "wiki" for a in mem)

    named = [m for m in latest_mols if m.get("named")]
    wiki_only_all = sum(1 for m in latest_mols if _wiki_only(m))
    wiki_only_named = sum(1 for m in named if _wiki_only(m))
    grounded = {m["id"]: _doc_grounded(od, m["id"]) for m in named}
    no_grounding = sum(1 for v in grounded.values() if v is False)
    missing_atoms_n = sum(len(m.get("missing_atoms") or []) for m in named)
    links_n = int((mol_index.get("links") or {}).get("n") or 0) if isinstance(mol_index.get("links"), dict) else 0
    never_share = None
    if recall_rows:
        never_share = round(1 - sum(1 for aid in atoms if aid in recalls) / n, 4) if n else None

    # ── series 한 줄 ──
    def _sorted(c: Counter) -> dict:
        return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))

    row: dict[str, Any] = {
        "at": now, "at_iso": _iso(now), "index_mtime": index_mtime, "snapshot": snap_name,
        "snapshot_created": snap_created, "atoms": n,
        "by_layer": _sorted(by_layer), "by_scope": _sorted(by_scope), "by_origin": _sorted(by_origin),
        "by_kind": _sorted(by_kind), "by_status": _sorted(by_status),
        "categorized": categorized, "categorized_report_origin": categorized_report, "with_coords": with_coords,
        "no_sources": no_sources, "no_period": no_period,
        "dup_pairs": (len(dup_pairs) if dup_pairs is not None else None), "tau_dup": tau_dup, "dup_counts_at": counts_at or None,
        "list_like": list_like_n, "molecules": len(latest_mols), "named": len(named), "unnamed": len(latest_mols) - len(named),
        "links": links_n, "wiki_only_molecules": wiki_only_all, "wiki_only_named": wiki_only_named,
        "no_grounding_docs": no_grounding, "missing_atoms": missing_atoms_n,
        "spent": _spent(od, mol_index, corr_sum),
        "corrections": {k2: corr_sum[k2] for k2 in ("n", "country_hit_rate", "region_hit_rate", "median_error_km", "revised", "cost_usd")},
        "tiers": tiers_summary, "retracted": retracted, "never_recalled_share": never_share,
        "recall_log": recall_info if recall_rows else None,
        "embedded": embedded, "unembedded": unembedded, "k": k,
        "layout": ({"method": layout_doc["method"], "aligned": layout_doc["aligned"], "disparity": layout_doc["disparity"]}
                   if layout_doc else None),
        "sweep": bool(sweep_doc),
    }
    row["signals"] = _signals(row, index_mtime=index_mtime, snap_created=snap_created, unembedded=unembedded, tau=tau_dup,
                              dups_top=dup_pairs, numpy_ok=np is not None, tiers_aided=tiers_aided, jobs=jobs,
                              recall_info=recall_info, point_aided=(point_aided, point_n))
    row["built_s"] = round(time.time() - t_start, 2)

    lineage_doc = build_lineage(snaps, latest_by_id, sweep_doc, sweep_note)

    # ── 쓰기 (observe/ 아래에만) ──
    _write_json(od.atom_meta, atom_meta)
    _write_json(od.growth, growth_doc, indent=1)
    if neighbors_doc is not None:
        _write_json(od.neighbors, neighbors_doc)
    if dup_pairs is not None:
        _write_json(od.dups, {"tau": tau_dup, "built": now, "n_pairs": len(dup_pairs), "counts_at": counts_at,
                              "pairs": dup_pairs, "groups": dup_groups}, indent=1)
    if link_candidates is not None:
        _write_json(od.link_candidates, {"built": now, "method": "adamic_adar", "n": len(link_candidates["candidates"]),
                                         "n_bridges": len(link_candidates["bridges"]), **link_candidates}, indent=1)
    if layout_doc is not None:
        _write_json(od.layout, layout_doc)
    _write_json(od.lineage, lineage_doc, indent=1)
    with od.series.open("a", encoding="utf-8") as f:       # 추가 전용 — 시계열은 덮어쓰지 않는다
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _write_text(od.readme, _readme(od, row, lineage_doc, layout_doc, neighbors_doc, dup_pairs, dup_groups,
                                   link_candidates, sweep_note, emb is not None))

    on = [s["key"] for s in row["signals"] if s["on"]]
    log(f"[observe] 신호 켜짐 {len(on)}/10: {', '.join(on) or '—'}")
    log(f"[observe] 완료 {row['built_s']}s → {od.root}")
    return row


# ── README ───────────────────────────────────────────────────────
def _readme(od: ObsDir, row: dict, lineage: dict, layout_doc, neighbors_doc, dups, dup_groups, link_candidates,
           sweep_note: str, emb_ok: bool) -> str:
    sig_rows = "\n".join(
        f"| `{s['key']}` | {s['level']} | {'**켜짐**' if s['on'] else '꺼짐'} | {s['value']} | {s['threshold']} | {s['hint']} |"
        for s in row["signals"])
    on = [s["key"] for s in row["signals"] if s["on"]]
    ev = lineage.get("events") or []
    sweep = lineage.get("sweep")
    sweep_line = (" · ".join(f"pct {p} → {len(v)}" for p, v in sweep.items()) if sweep else f"없음 ({sweep_note or '--sweep 미지정'})")
    lay = (f"{layout_doc['method']} · {layout_doc['n']}점 · " + ("이전 레이아웃에 프로크루스테스 정렬(disparity "
           f"{layout_doc['disparity']})" if layout_doc.get("aligned") else "첫 레이아웃(정렬 기준 없음)")) if layout_doc else "이번 빌드에서 만들지 않음(--no-layout 또는 numpy/npz 없음)"
    nb = (f"k={neighbors_doc['k']} · 상호 엣지 {len(neighbors_doc['mutual_edges']):,} · 분위표 {len(neighbors_doc['quantiles'])}개"
          if neighbors_doc else "이번 빌드에서 만들지 않음")
    dup_line = (f"{len(dups)}쌍({len(dup_groups or [])}묶음, union-find) (τ={row['tau_dup']}) · 기준 {row.get('dup_counts_at')}"
               if dups is not None else "계산 불가")
    link_line = (f"Adamic-Adar 상위 {len(link_candidates['candidates'])}건 · 다리(bridge, 멀지만 공통 이웃 ≥5·목록형 제외) "
                 f"{len(link_candidates['bridges'])}건 · 점수 매긴 쌍 {link_candidates['n_pairs_scored']:,}"
                 if link_candidates is not None else "계산 불가")
    corr = row["corrections"]
    return f"""# 관측 사이드카 (observe/) — 빌드가 남기고 관측소가 읽는 것

`geoguesshelper observe build` (= `PYTHONUTF8=1 PYTHONPATH=src python -m geoguesshelper.observe build`) 가 만든다.
**LLM 호출 0 · 원자 무변경** — `atoms/`·`molecule/`·`index.json` 은 읽기만 하고, 쓰는 곳은 이 폴더뿐이다.
기획: `docs/plan/atom-observatory_260907.html` §데이터·§신호 · 명세: `docs/plan/impl-spec_260907.md` §5.

여기 있는 것은 전부 **파생**(최근접 거리·중복 후보·분자 소속·회상 횟수·2D 좌표)이지 사실이 아니다. 그래서 원자 md 에
쓰지 않고 따로 둔다 — 통째로 지워도 잃는 것이 없고, 다시 빌드하면 돌아온다(`series.jsonl` 만 추가 전용 시계열).

## 이번 빌드 — {row['at_iso']}

- 원자 **{row['atoms']}** (경로 {json.dumps(row['by_origin'], ensure_ascii=False)} · kind {json.dumps(row['by_kind'])} · status {json.dumps(row['by_status'])})
- 범주 있음 {row['categorized']} (보고서 유래 {row['categorized_report_origin']}) · 좌표 {row['with_coords']} · 출처 없음 {row['no_sources']} · 시기 없음 {row['no_period']} · 목록형 {row['list_like']} · 철회 {row['retracted']}
- 분자 {row['molecules']} (명명 {row['named']} · 미명명 {row['unnamed']} · 위키 전용 {row['wiki_only_molecules']}, 명명 중 {row['wiki_only_named']}) · 링크 {row['links']} · 빠진 원자 {row['missing_atoms']} · 근거 없는 문서 {row['no_grounding_docs']}
- 스냅샷 `{row['snapshot']}` · 임베딩 있는 원자 {row['embedded']} / 없는 원자 {row['unembedded']}
- 근중복: {dup_line}
- 링크 후보(공통 이웃, refs 자동 제안 재료): {link_line}
- 레이아웃: {lay}
- 이웃: {nb}
- 계보: 스냅샷 {len(lineage.get('snapshots') or [])}개 · 전이 {len(ev)}건 · sweep {sweep_line}
- 정정 루프: {corr['n']}건 · 국가 적중 {corr['country_hit_rate']} · 지역 적중 {corr['region_hit_rate']} · 오차 중앙값 {corr['median_error_km']} km · 2패스로 바뀐 판단 {corr['revised']}
- 비용(USD): {json.dumps(row['spent'])}
- 소요 {row['built_s']}s

### 신호 — 켜진 것 {len(on)}/10: {', '.join(f'`{k}`' for k in on) or '—'}

| 신호 | 등급 | 상태 | 값 | 임계 | 설명 |
|---|---|---|---|---|---|
{sig_rows}

`stale-observe` 는 빌드 시점엔 정의상 꺼져 있다 — 뷰어가 `index.json` mtime 과 `series` 마지막 줄 `at` 을 비교해 켠다.

## 파일

| 파일 | 내용 | 갱신 |
|---|---|---|
| `atom_meta.json` | `{{built, index_mtime, n, tau_dup, k, embedded, unembedded, atoms:{{id: {{origin, layer, scope, category, kind, status, tier, has_coords, n_sources, n_refs, n_reports, list_like, nn1:{{id,d}}\\|null, dup_of:id\\|null, molecules:[mol…], periphery_of:[mol…], hits, misses, recalls:{{n,last,mean_rank}}\\|null, correction:{{job_id,n}}\\|null, created, updated}}}}}}` | 매 빌드 |
| `growth.json` | `{{built, days:[{{day:"YYYY-MM-DD", report, expansion, wiki, correction, dialogue, baseline, total}}]}}` — `created` 를 **로컬 날짜**로 소급. 원자가 있는 날만 | 매 빌드 |
| `series.jsonl` | 빌드마다 한 줄 **추가**: `{{at, at_iso, index_mtime, snapshot, snapshot_created, atoms, by_layer, by_scope, by_origin, by_kind, by_status, categorized, categorized_report_origin, with_coords, no_sources, no_period, dup_pairs, tau_dup, dup_counts_at, list_like, molecules, named, unnamed, links, wiki_only_molecules, wiki_only_named, no_grounding_docs, missing_atoms, spent:{{expansion,wiki,molecule,baseline,corrections}}, corrections:{{n,country_hit_rate,region_hit_rate,median_error_km,revised,cost_usd}}, tiers:{{unaided,aided,blind-only}}\\|null, retracted, never_recalled_share\\|null, recall_log, embedded, unembedded, k, layout, sweep, signals:[{{key, level:info\\|warn\\|alert, value, threshold, on, hint}}], built_s}}` | 매 빌드 추가 |
| `layout.json` | `{{method:"umap\\|tsne\\|pca", fitted_at, n, ref_layout, aligned, disparity, points:[[id,x,y]]}}` — x,y ∈ [0,1]. umap 없으면 sklearn TSNE(init=pca, random_state=0, perplexity 30), 그것도 없으면 PCA. 이전 파일이 있으면 공통 원자로 프로크루스테스(회전·반전·스케일) 정렬 뒤 min-max 정규화 — 배치는 유지되지만 수치가 완전히 같지는 않다. **2D 는 안내도** — 판정은 거리 행렬로 | npz 갱신 시 |
| `neighbors.json` | `{{model, built, embeddings_created, k, n, ids:[…], quantiles:[q0..q200], pct_thresholds:{{"0.25":ε,…}}, snapshot_params:{{knn,min_size,merge,merge_mode,mutual}}, snapshot_threshold, knn:{{id:[[id,d,mutual],…]}}, mutual_edges:[[i,j,d,ri,rj],…]}}` — `quantiles[i]` = 전 쌍 코사인 거리의 i×0.5 퍼센타일(201개, 0~100%). `knn[id]` 는 거리 오름차순(= 순위). `mutual_edges` 의 i,j 는 **`ids` 배열의 인덱스**(i<j), 상호 kNN 쌍만; `ri`/`rj` 는 j 가 i 의 몇 번째 이웃인가(0 시작)와 그 반대. **스냅샷(knn=10) 재현** = `max(ri,rj) < snapshot_params.knn` 이고 `d ≤ ε` 인 엣지만 남긴 그래프의 극대 클리크(≥ min_size) — pct 0.5 에서 엣지 2,193 · 분자 120 이 나와야 한다. pct → ε 는 `quantiles[round(pct/0.5)]`(0.25 같은 중간값은 `pct_thresholds` 또는 선형 보간) | npz 갱신 시 |
| `dups.json` | `{{tau, built, n_pairs, counts_at:{{"0.10":n,"0.12":n,"0.15":n}}, pairs:[{{a,b,d,title_a,title_b,origin_a,origin_b,layer_a,layer_b,same_report}}], groups:[{{atoms:[…],n,direct_pairs,transitive_only,d_min,d_max}}]}}` pairs 는 거리 오름차순. **판정은 사람** — 이 파일은 지우지 않는다. `groups`(260908)는 pairs 를 union-find 로 묶은 전이적 검토 묶음(보조 뷰, 확정 아님) — `transitive_only` 는 그룹 안 모든 쌍이 직접 임계 이내는 아니라는 뜻 | npz 갱신 시 |
| `link_candidates.json` | `{{built, method:"adamic_adar", n, n_bridges, n_pairs_scored, params, candidates:[{{a,b,score,common_neighbors,d,below_eps,list_like,title_a,title_b,layer_a,layer_b}}], bridges:[{{…같음, bridge}}]}}` (260908) — 상호 kNN 그래프에서 직접 안 이어진 쌍 중 공통 이웃 가중합(Adamic-Adar, Zhou·Lü·Zhang 2009) 상위 200 = `refs` 자동 후보(실측: 대부분 "거의 이웃" — 상호 kNN 컷에 걸린 가까운 쌍). `bridges` 는 목록형 제외 · d ≥ pct1.0 임계 · 공통 이웃 ≥5 인 **먼** 쌍을 score×d 로 순위 — molecule-theory_260907 §3.2 "허브형 분자"(부재 노드) 후보 | npz 갱신 시 |
| `lineage.json` | `{{built, snapshots:[names 시간순], latest, molecules:{{mol:{{first_seen,last_seen,status:born\\|persist\\|continue\\|dissolved,prev,next,jaccard,seen,name_ko?,persistence?:[pcts]}}}}, events:[{{from,to,born,dissolved,persist,continue,merged,split, ids:{{born:[…],dissolved:[…],persist:[…],continue:[[prev,new,j]],merged:[[new,[prev…]]],split:[[prev,[new…]]]}}}}], sweep:{{"0.25":[ids],"0.5":[…],"1.0":[…],"2.0":[…],"3.0":[…]}}\\|null, sweep_thresholds, sweep_params, sweep_note}}` — 규칙: 같은 id = persist · 자카드 ≥ 0.5 = continue · 한쪽만 = born/dissolved · 절반 이상 겹침의 다대일/일대다 = merged/split. `continue` 는 자카드 ≥0.5 후보에 대한 **전역 최적 1:1 매칭**(헝가리안, scipy, 260908 — 이전 그리디는 두 new 가 같은 old 를 놓고 경쟁하면 하나가 next 를 잃었다). scipy 없으면 이전 그리디로 저하. 스냅샷 1개면 전부 born, events [] | 스냅샷 추가 시 |
| `README.md` | 이 문서(빌드마다 재생성) | 매 빌드 |

`.tmp` 로 쓰고 교체(원자적)한다. 뷰어는 반쯤 쓰인 파일을 보지 않는다.

## 집계 규칙(재현 가능해야 한다 — `tests/observe_check.py` 가 index.json·npz 에서 독립 재계산해 대조)

- **origin**: 원자에 `origin` 필드가 있으면 그것. 없으면(구 원자) `reports` 비어 있지 않음 → `report` · 태그에 `x-` 접두 → `expansion` · 나머지 → `wiki`.
  reports 와 x- 태그를 둘 다 가진 원자 1개가 report 로 가므로 expansion 은 체크포인트 `expansion.json.created`(617)보다 1 적다(616).
- **list_like**: origin==wiki 이고 제목이 `\\b(list|lists|dynasties|kingdoms|states|empires|rulers|monarchs|polities|chiefdoms|republics|duchies|regimes|principalities)\\b`(대소문자 무시)에 걸림.
- **categorized**: `category` 가 빈 문자열/None 이 아님. `categorized_report_origin` 은 그중 origin==report.
- **dup_pairs**: 임베딩 전 쌍 중 `1 − cos < τ`(기본 0.10). **wiki_only_molecules**: 멤버 전원이 origin==wiki 인 최신 스냅샷 분자. **no_grounding_docs**: 명명 분자 문서의 `## 검색 근거` 절에 실제 불릿이 없음.
- **corrections**: `corrections/corrections.jsonl`(없으면 `corr_*.json`) — country/region hit 은 bool 또는 hit/miss/unknown, 오차 중앙값은 null 제외.
- **recalls**: `recall_log.jsonl` 원자별 {{n, last, mean_rank}}; `never_recalled_share` = 로그가 있을 때 회상 0회 원자 비율(로그 30일 미만이면 신호는 보류).
- **sweep**: `molecule.py` 의 `_distances → _graph_for → _molecules_from` 을 저장 없이 재사용(파라미터는 `molecule/index.json` 의 `params`). pct 0.5 의 id 집합은 최신 스냅샷과 같아야 한다(검증 260907: 120/120).

## 재실행

```powershell
$env:PYTHONUTF8=1; $env:PYTHONPATH="src"
python -m geoguesshelper.observe build --sweep            # 전부(시스템 Python: numpy·scikit-learn·networkx)
python -m geoguesshelper.observe build --no-layout        # t-SNE 생략(수 초 절약)
python -m geoguesshelper.observe build --tau-dup 0.12 --k 20
uv run geoguesshelper observe build                       # uv 환경 — numpy 없으면 layout/neighbors/dups 건너뜀(단계적 저하)
.\\snapshot-knowledge.ps1 [-SkipEmbed] [-SkipSync]         # embed → molecule build --pct 0.5 → observe build → altaiya 동기화
```

원자를 새로 쌓은 날은 `snapshot-knowledge.ps1` 을 한 번 돈다(기획 결정 260907 — 스케줄러 없음). 빠뜨리면 `stale-snapshot` 이 알려준다.
"""


# ── CLI ──────────────────────────────────────────────────────────
def _settings_dirs(knowledge: str | None) -> tuple[Path, Path]:
    """knowledge_dir·jobs_dir. 본체 Settings 를 쓸 수 있으면(GEOHELPER_KNOWLEDGE 존중) 그것, 아니면 저장소 기본 경로."""
    try:
        from .config import load_settings

        s = load_settings()
        kd, jd = Path(s.knowledge_dir), Path(s.jobs_dir)
    except Exception:  # noqa: BLE001
        root = Path(__file__).resolve().parents[2]
        kd, jd = root / "docs" / "knowledge", root / "docs" / "jobs"
    if knowledge:
        kd = Path(knowledge)
        cand = kd.parent / "jobs"
        if cand.exists():
            jd = cand
    return kd, jd


def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="python -m geoguesshelper.observe",
                                 description="관측 사이드카 빌드 — docs/knowledge/observe/ (LLM 0 · 원자 무변경)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("build", help="atom_meta · growth · series · layout · neighbors · dups · lineage · README")
    p.add_argument("--no-layout", action="store_true", help="2D 투영(t-SNE) 생략")
    p.add_argument("--sweep", action="store_true", help="pct 0.25/0.5/1/2/3 분자 집합(지속성) — molecule 내부 함수 재사용, 저장 없음")
    p.add_argument("--tau-dup", type=float, default=0.10, help="근중복 후보 거리 임계(기본 0.10)")
    p.add_argument("--k", type=int, default=15, help="이웃 수(기본 15)")
    p.add_argument("--knowledge", default=None, help="지식 폴더(기본 settings.knowledge_dir = docs/knowledge)")
    sub.add_parser("help", help="이 도움말")
    a = ap.parse_args(argv)
    if a.cmd in (None, "help"):
        ap.print_help()
        print("\n시스템 Python 에서: PYTHONUTF8=1 PYTHONPATH=src python -m geoguesshelper.observe build --sweep\n"
              "산출 설명: docs/knowledge/observe/README.md")
        return
    kd, jd = _settings_dirs(a.knowledge)
    cmd_build(kd, jd, layout=not a.no_layout, sweep=a.sweep, tau_dup=a.tau_dup, k=a.k)


if __name__ == "__main__":
    main()
