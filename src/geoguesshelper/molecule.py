"""분자(molecule) — 원자 임베딩 → 거리 그래프 → 최대 클리크 → 개념 문서.

원자(atom)는 "하나의 사실"이다. 그런데 사실 여러 개가 서로 가까이 모여 있으면, 그 모임
자체가 어느 원자에도 적혀 있지 않은 **개념 하나**를 가리킬 때가 있다.
예: [프랑수아 1세, 성채 설계, 이탈리아 예술가 초빙] → 어느 원자에도 없는 '다빈치'.
그 개념을 원자가 아닌 **분자(molecule)** 라 부르고, 이 모듈이 그것을 찍어낸다.

정의(사용자 요구 그대로):
  · 원자마다 임베딩 벡터를 구하고 코사인 거리 D = 1 − cos 를 잰다.
  · 전 쌍 거리의 하위 --pct 퍼센타일을 "가깝다"의 임계로 삼는다(시점·원자 수에 따라 조절).
  · 임계 그래프의 **최대 클리크**(모든 쌍이 서로 가까운 집합)가 분자다 — A–B, B–C, C–D,
    A–C 가 가까워도 A–D 가 멀면 D 는 빠지고 {A,B,C} 만 분자가 된다.
  · 원자 수가 바뀌면 거리 분포도 바뀌므로 분자는 **시점별 스냅샷**이다. 분자 id 는 구성
    원자 집합의 해시(내용 주소)라 같은 구성이면 시점이 달라도 같은 id 다.

등급: 분자는 기계 산출물이다. 기획(docs/plan/atom-dialogue_260906.html §5·§8)에 따라
사람 서명 '주장'보다도 낮은 **hypothesis** 로만 저장되며 원자(사실)와 섞이지 않는다.
원자 .md 는 절대 건드리지 않는다 — 원자→분자 백링크는 molecule/index.json 의 맵으로만.

저장 구조 (docs/knowledge/molecule/):
    embeddings.npz            원자 임베딩(ids, vectors, atom_updated — 증분 갱신 근거)
    snapshots/<ts>_p<pct>_k<knn>_s<min>.json   시점별 분자 스냅샷
    index.json                최신 스냅샷 포인터 · 분자 목록 · atom→molecules 맵
    mol_<hash12>.md           분자 문서(JSON 프론트매터 + 본문, status=hypothesis)
    links.jsonl               atom–molecule 커넥션(slug/title 일치)
    README.md                 개념·파라미터·재실행 방법

실행(시스템 Python — numpy·networkx·sentence_transformers 는 uv 환경에 없다):
    HF_HUB_OFFLINE=1 PYTHONPATH=src python -m geoguesshelper.molecule embed
    PYTHONPATH=src python -m geoguesshelper.molecule build --pct 0.5 --knn 10 --min-size 4
    PYTHONPATH=src python -m geoguesshelper.molecule name --limit 60 --budget 8
    PYTHONPATH=src python -m geoguesshelper.molecule reify
numpy/networkx/sentence_transformers 는 함수 안에서 lazy import 하고, 없으면 안내 후 종료.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import llm
from .config import Settings, load_settings
from .knowledge import Atom, Store, slug

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
STATUS = "hypothesis"          # 기계 산출 = 사람 승인 전. 항상 이 값.
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")
_ATM_RE = re.compile(r"atm_[0-9a-f]{12}")


# ── 경로 ─────────────────────────────────────────────────────────
class MolDir:
    def __init__(self, knowledge_dir: Path) -> None:
        self.root = Path(knowledge_dir) / "molecule"
        self.snapshots = self.root / "snapshots"
        self.embeddings = self.root / "embeddings.npz"
        self.index = self.root / "index.json"
        self.links = self.root / "links.jsonl"
        self.readme = self.root / "README.md"
        self.last_atom_updated: dict[str, float] | None = None   # _load_embeddings 가 채운다(npz 의 atom_updated)

    def doc(self, mol_id: str) -> Path:
        return self.root / f"{mol_id}.md"

    def ensure(self) -> None:
        self.snapshots.mkdir(parents=True, exist_ok=True)

    def load_index(self) -> dict:
        if self.index.exists():
            try:
                d = json.loads(self.index.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    d.setdefault("molecules", {})
                    d.setdefault("atom_to_molecules", {})
                    return d
            except Exception:  # noqa: BLE001
                pass
        return {"molecules": {}, "atom_to_molecules": {}, "spent_usd": 0.0}

    def save_index(self, idx: dict) -> None:
        self.ensure()
        idx["updated"] = time.time()
        tmp = self.index.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.index)


def _mol_dir(settings: Settings) -> MolDir:
    return MolDir(settings.knowledge_dir)


def _need(mod: str, hint: str):
    try:
        return __import__(mod)
    except ModuleNotFoundError:
        sys.exit(
            f"[molecule] `{mod}` 이(가) 없습니다. {hint}\n"
            "  이 명령은 uv 가상환경이 아니라 시스템 Python 으로 실행해야 합니다:\n"
            "  PYTHONPATH=src python -m geoguesshelper.molecule …"
        )


def _ts() -> str:
    return time.strftime("%y%m%d_%H%M%S")


def _load_atoms(settings: Settings) -> dict[str, Atom]:
    st = Store(settings.knowledge_dir)
    return {a.id: a for a in st.all_atoms()}


def atom_text(a: Atom) -> str:
    """임베딩 입력. layer/category 접두 + 제목 — 본문 + 태그 + 엔티티."""
    head = f"[{a.layer}/{a.category or 'other'}]"
    tags = " ".join(f"#{t}" for t in a.tags)
    ents = " ".join(f"@{e}" for e in a.entities)
    return f"{head} {a.title} — {a.body}" + (f" {tags}" if tags else "") + (f" {ents}" if ents else "")


# ── 1. embed ─────────────────────────────────────────────────────
def cmd_embed(settings: Settings, *, batch_size: int = 64, force: bool = False, device: str | None = None) -> dict:
    """원자 전부를 임베딩해 embeddings.npz 로. 이미 있고 updated 가 같은 원자는 재사용."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")   # 회사망 TLS 가로채기 — 캐시만 쓴다
    np = _need("numpy", "시스템 Python 에는 있습니다.")
    md = _mol_dir(settings)
    md.ensure()
    atoms = _load_atoms(settings)
    ids = sorted(atoms)
    if not ids:
        sys.exit("[molecule] 원자가 없습니다.")

    prev_vec: dict[str, Any] = {}
    prev_upd: dict[str, float] = {}
    if md.embeddings.exists() and not force:
        try:
            # NpzFile 은 파일 핸들을 쥔다 — 닫지 않으면 Windows 에서 아래 os.replace 가 실패한다.
            with np.load(md.embeddings) as z:
                if str(z["model"]) == EMBED_MODEL:
                    vecs = z["vectors"]
                    upds = z["atom_updated"]
                    for i, aid in enumerate(z["ids"].tolist()):
                        prev_vec[aid] = vecs[i]
                        prev_upd[aid] = float(upds[i])
        except Exception as exc:  # noqa: BLE001
            print(f"[molecule] 기존 embeddings.npz 를 읽지 못해 전부 다시 계산합니다: {exc}")

    todo = [aid for aid in ids if not (aid in prev_vec and abs(prev_upd.get(aid, -1) - float(atoms[aid].updated)) < 1e-6)]
    reused = len(ids) - len(todo)
    print(f"[embed] 원자 {len(ids)}개 · 재사용 {reused} · 새로 계산 {len(todo)} · 모델 {EMBED_MODEL}")

    vectors: dict[str, Any] = dict(prev_vec)
    if todo:
        _need("sentence_transformers", "시스템 Python 에는 있습니다(HF 캐시의 mpnet 사용).")
        import torch  # type: ignore
        from sentence_transformers import SentenceTransformer  # type: ignore

        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        t0 = time.time()
        model = SentenceTransformer(EMBED_MODEL, device=dev)
        print(f"[embed] 모델 로드 {time.time() - t0:.1f}s · device={dev} · dim={model.get_sentence_embedding_dimension()}")
        texts = [atom_text(atoms[aid]) for aid in todo]
        t0 = time.time()
        vec = model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=len(texts) > 200)
        print(f"[embed] 인코딩 {len(texts)}건 {time.time() - t0:.1f}s")
        for aid, v in zip(todo, vec):
            vectors[aid] = v.astype("float32")

    V = np.stack([vectors[aid] for aid in ids]).astype("float32")
    upd = np.array([float(atoms[aid].updated) for aid in ids], dtype="float64")
    tmp = md.embeddings.with_name("embeddings.tmp.npz")   # savez 는 .npz 로 끝나지 않으면 덧붙인다
    np.savez(tmp, ids=np.array(ids, dtype=str), vectors=V, model=np.array(EMBED_MODEL),
             created=np.array(time.time()), atom_updated=upd)
    os.replace(tmp, md.embeddings)
    print(f"[embed] 저장 {md.embeddings} · shape={V.shape}")
    return {"n": len(ids), "reused": reused, "computed": len(todo), "path": str(md.embeddings)}


# ── 2. build ─────────────────────────────────────────────────────
@dataclass
class Molecule:
    id: str
    atoms: list[str]
    n: int
    cohesion: float          # 평균 내부 거리(낮을수록 응집 강함)
    diameter: float          # 최대 내부 거리
    merged_from: int = 1     # 이 분자로 합쳐진(union) 또는 흡수된(absorb) 클리크 수. 1 = 순수 클리크
    periphery: list[str] = field(default_factory=list)   # absorb 모드: 흡수된 클리크에만 있던 원자(멤버 아님)
    layers: dict[str, int] = field(default_factory=dict)
    titles: list[str] = field(default_factory=list)


def mol_id_of(atom_ids: list[str]) -> str:
    return "mol_" + hashlib.sha1(",".join(sorted(atom_ids)).encode("utf-8")).hexdigest()[:12]


def _load_embeddings(md: MolDir):
    np = _need("numpy", "시스템 Python 에는 있습니다.")
    if not md.embeddings.exists():
        sys.exit("[molecule] embeddings.npz 가 없습니다. 먼저 `embed` 를 실행하세요.")
    with np.load(md.embeddings) as z:
        ids = z["ids"].tolist()
        V = z["vectors"].astype("float32")
        model = str(z["model"])
        # 임베딩 시점의 원자 updated — build 가 현재 원자와 대조해 "본문은 바뀌었는데 벡터는 옛것"을 잡는다(260908)
        upd = z["atom_updated"].tolist() if "atom_updated" in z.files else None
    md.last_atom_updated = dict(zip(ids, upd)) if upd is not None else None
    return ids, V, model


def _distances(V):
    import numpy as np

    # float32 곱 → float64 로 올려 판정·분위수·직렬화를 한 dtype 으로 통일한다(브라우저 Number 와 같은 비교).
    # float32 배열 vs Python float 임계 비교는 NumPy 가 임계를 float32 로 내려 경계 쌍이 갈릴 수 있었다(codex 2차 260908).
    D = (1.0 - (V @ V.T)).astype("float64")
    np.fill_diagonal(D, 0.0)
    return np.clip(D, 0.0, 2.0)


def _hist_text(vals, bins=20, lo=0.0, hi=1.0, width=48) -> str:
    import numpy as np

    h, edges = np.histogram(vals, bins=bins, range=(lo, hi))
    mx = max(1, int(h.max()))
    lines = []
    for c, a, b in zip(h, edges[:-1], edges[1:]):
        bar = "█" * int(round(width * c / mx))
        lines.append(f"  {a:4.2f}–{b:4.2f} {c:8d} {bar}")
    return "\n".join(lines)


def _graph_for(D, *, pct: float, knn: int, mutual: bool):
    """임계(하위 pct 퍼센타일) ∩ kNN 후보 → networkx 그래프. 반환 (G, threshold, upper)."""
    import numpy as np
    nx = _need("networkx", "시스템 Python 에는 있습니다.")

    n = D.shape[0]
    iu = np.triu_indices(n, 1)
    upper = D[iu]
    thr = float(np.percentile(upper, pct))
    k = max(1, min(knn, n - 1))
    # 자기 자신을 뺀 상위 k 이웃
    Dm = D.copy()
    np.fill_diagonal(Dm, np.inf)
    nn = np.argpartition(Dm, k, axis=1)[:, :k]
    knn_mask = np.zeros((n, n), dtype=bool)
    knn_mask[np.repeat(np.arange(n), k), nn.ravel()] = True
    knn_mask = (knn_mask & knn_mask.T) if mutual else (knn_mask | knn_mask.T)
    mask = knn_mask & (D <= thr)
    np.fill_diagonal(mask, False)
    ei, ej = np.nonzero(np.triu(mask, 1))
    G = nx.Graph()
    G.add_nodes_from(range(n))
    G.add_edges_from((int(i), int(j), {"d": float(D[i, j])}) for i, j in zip(ei, ej))
    return G, thr, upper


def _clique_stats(D, idx: list[int]) -> tuple[float, float]:
    import numpy as np

    if len(idx) < 2:
        return 0.0, 0.0
    sub = D[np.ix_(idx, idx)]
    vals = sub[np.triu_indices(len(idx), 1)]
    return float(vals.mean()), float(vals.max())


MAX_CLIQUES = 200_000      # 열거 상한 — 병적으로 조밀한 그래프(완전 다부 그래프 등)에서 극대 클리크 수가
MAX_CLIQUE_SECONDS = 60.0  # 지수적으로 폭발하는 것에 대한 방어선(codex 감사 260908) — knn 상한이 없어 발생 가능


def _molecules_from(G, D, ids, atoms: dict[str, Atom], *, min_size: int, merge: float,
                    merge_mode: str = "absorb") -> tuple[list[Molecule], int, bool]:
    """최대 클리크(크기 ≥ min_size) → 겹치는 것(자카드 ≥ merge) 처리.

    merge_mode:
      absorb — 응집 강한 클리크부터 채택하고, 이미 채택된 분자와 자카드 ≥ merge 로 겹치는 클리크는
               새 분자로 세우지 않는다(흡수). 흡수된 클리크에만 있던 원자는 periphery 로 남긴다.
               모든 분자가 **엄격한 클리크**로 유지된다(diameter ≤ 임계 — "A–D 가 멀면 D 배제" 그대로).
      union  — 자카드 ≥ merge 인 클리크를 union-find 로 합친다. 합친 분자는 클리크가 아닐 수 있다
               (먼 쌍이 섞임 → diameter > 임계). 낮은 merge 에서는 연쇄 병합으로 거대 분자가 생긴다.

    극대 클리크 수는 최악의 경우 지수적이다(예: 완전 다부 그래프). knn 이 크면(CLI 에 상한 없음) 발산할 수
    있어(codex 감사 260908 — 60개 20-파트 그래프로 재현: 3^20개) MAX_CLIQUES/MAX_CLIQUE_SECONDS 에서 열거를
    끊는다. 끊었으면 세 번째 반환값이 True — 호출자는 이 스냅샷을 "완전하지 않다"고 표시해야 한다.
    """
    import networkx as nx
    import numpy as np

    t0 = time.time()
    cliques: list[list[int]] = []
    truncated = False
    n_enumerated = 0
    for c in nx.find_cliques(G):
        n_enumerated += 1
        if len(c) >= min_size:
            cliques.append(sorted(c))
        # 상한을 "넘길 때"만 끊는다 — 정확히 상한만큼 있는 그래프를 불완전으로 오판하지 않기 위해(codex 2차 260908).
        # 상한은 열거 전체(n_enumerated) 기준이고, 뒤따르는 absorb/union 은 채택 클리크 수에 비례해 따로 시간을 쓴다.
        if n_enumerated > MAX_CLIQUES or (time.time() - t0) > MAX_CLIQUE_SECONDS:
            truncated = True
            if len(c) >= min_size:
                cliques.pop()
            break
    n_raw = len(cliques)

    if merge_mode == "absorb":
        scored = sorted(((_clique_stats(D, c)[0], -len(c), c) for c in cliques), key=lambda t: (t[0], t[1], t[2]))
        kept: list[dict] = []
        for coh, _, c in scored:
            cs = set(c)
            best = None
            best_j = 0.0
            if merge > 0:
                for k in kept:
                    j = len(cs & k["members"]) / len(cs | k["members"])
                    if j >= merge and j > best_j:
                        best, best_j = k, j
            if best is None:
                kept.append({"members": cs, "periphery": set(), "count": 1})
            else:
                best["periphery"] |= cs - best["members"]
                best["count"] += 1
        out: list[Molecule] = []
        for k in kept:
            idx = sorted(k["members"])
            coh, diam = _clique_stats(D, idx)
            aids = [ids[i] for i in idx]
            layers: dict[str, int] = {}
            for aid in aids:
                a = atoms.get(aid)
                if a:
                    layers[a.layer] = layers.get(a.layer, 0) + 1
            out.append(Molecule(
                id=mol_id_of(aids), atoms=aids, n=len(aids), cohesion=round(coh, 4), diameter=round(diam, 4),
                merged_from=k["count"], periphery=[ids[i] for i in sorted(k["periphery"])],
                layers=dict(sorted(layers.items())),
                titles=[(atoms[aid].title if aid in atoms else aid) for aid in aids],
            ))
        out.sort(key=lambda m: (m.cohesion, -m.n, m.id))
        return out, n_raw, truncated

    parent = list(range(len(cliques)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    if merge > 0 and cliques:
        by_node: dict[int, list[int]] = {}
        for ci, c in enumerate(cliques):
            for v in c:
                by_node.setdefault(v, []).append(ci)
        seen: set[tuple[int, int]] = set()
        sets = [set(c) for c in cliques]
        for members in by_node.values():
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    a, b = members[x], members[y]
                    key = (a, b) if a < b else (b, a)
                    if key in seen:
                        continue
                    seen.add(key)
                    j = len(sets[a] & sets[b]) / len(sets[a] | sets[b])
                    if j >= merge:
                        ra, rb = find(a), find(b)
                        if ra != rb:
                            parent[rb] = ra
    groups: dict[int, set[int]] = {}
    counts: dict[int, int] = {}
    for ci, c in enumerate(cliques):
        r = find(ci)
        groups.setdefault(r, set()).update(c)
        counts[r] = counts.get(r, 0) + 1

    out = []
    for r, nodes in groups.items():
        idx = sorted(nodes)
        coh, diam = _clique_stats(D, idx)
        aids = [ids[i] for i in idx]
        layers = {}
        for aid in aids:
            a = atoms.get(aid)
            if a:
                layers[a.layer] = layers.get(a.layer, 0) + 1
        out.append(Molecule(
            id=mol_id_of(aids), atoms=aids, n=len(aids), cohesion=round(coh, 4), diameter=round(diam, 4),
            merged_from=counts[r], layers=dict(sorted(layers.items())),
            titles=[(atoms[aid].title if aid in atoms else aid) for aid in aids],
        ))
    out.sort(key=lambda m: (m.cohesion, -m.n, m.id))
    return out, n_raw, truncated


def cmd_build(settings: Settings, *, pct: float = 0.5, knn: int = 10, min_size: int = 4,
              merge: float = 0.5, merge_mode: str = "absorb", mutual: bool = True,
              sweep: list[float] | None = None, dry_run: bool = False) -> dict:
    """거리 그래프 → 분자 스냅샷. --sweep 로 pct 후보를 비교만 할 수도 있다."""
    import numpy as np

    md = _mol_dir(settings)
    ids, V, model = _load_embeddings(md)
    atoms = _load_atoms(settings)
    missing = [i for i in ids if i not in atoms]
    if missing:
        print(f"[build] 경고: 임베딩엔 있으나 원자 파일이 없는 id {len(missing)}개 — `embed` 재실행 권장")
    # 임베딩 이후 본문이 바뀐 원자(P2 병합이 본문을 교체하는 경로 등) — 옛 벡터로 현재 원자를 묶게 된다(codex 감사 260908 #7).
    # 정책: 막지 않고 경고 + 스냅샷에 수를 기록한다(관측소 stale 신호의 재료). 새 원자(npz 에 없음)는 unembedded 로 따로 센다.
    stale: list[str] = []
    unverifiable: list[str] = []          # updated 를 숫자로 못 읽은 원자 — "최신 확인"으로 세지 않는다
    stale_checked = md.last_atom_updated is not None
    if stale_checked:
        for aid in ids:
            a = atoms.get(aid)
            if a is None:
                continue
            try:
                cur = float(a.updated)
            except (TypeError, ValueError):
                unverifiable.append(aid)
                continue
            if abs(cur - float(md.last_atom_updated.get(aid, -1))) > 1e-6:
                stale.append(aid)
    else:
        print("[build] 경고: embeddings.npz 에 atom_updated 가 없다(구버전) — 본문 변경 여부를 확인할 수 없다. `embed` 재실행 권장")
    ids_set = set(ids)
    unembedded = [aid for aid in atoms if aid not in ids_set]
    if stale or unembedded or unverifiable:
        print(f"[build] 경고: 임베딩 이후 본문이 바뀐 원자 {len(stale)}개 · 확인 불가 {len(unverifiable)}개 · 임베딩 없는 새 원자 "
              f"{len(unembedded)}개 — 이 스냅샷은 옛 벡터/부분 집합으로 만들어진다. `embed` 재실행 권장")
    n = len(ids)
    D = _distances(V)
    iu = np.triu_indices(n, 1)
    upper = D[iu]
    pcts = {str(p): round(float(np.percentile(upper, p)), 4) for p in (1, 2, 5, 10, 25, 50)}
    print(f"[build] 원자 {n} · 쌍 {len(upper):,} · 모델 {model}")
    print(f"[build] 거리 분포(코사인 거리 1−cos): mean={upper.mean():.4f} std={upper.std():.4f} "
          f"min={upper.min():.4f} max={upper.max():.4f}")
    print("[build] 퍼센타일: " + " ".join(f"p{k}={v}" for k, v in pcts.items()))
    print("[build] 히스토그램(전 쌍 거리):\n" + _hist_text(upper))

    if sweep:
        print(f"\n[build] --sweep 비교 (knn={knn} {'mutual' if mutual else 'union'}, min_size={min_size}, "
              f"merge={merge} {merge_mode})")
        print(f"  {'pct':>5} {'thr':>7} {'edges':>7} {'deg~':>5} {'cliques':>7} {'mols':>5} {'cover':>5} {'max':>4} "
              f"{'d>thr':>5}  size-dist")
        rows = []
        for p in sweep:
            G, thr, _ = _graph_for(D, pct=p, knn=knn, mutual=mutual)
            mols, n_raw, trunc = _molecules_from(G, D, ids, atoms, min_size=min_size, merge=merge, merge_mode=merge_mode)
            cover = len({a for m in mols for a in m.atoms})
            dist: dict[int, int] = {}
            for m in mols:
                dist[m.n] = dist.get(m.n, 0) + 1
            deg = (2 * G.number_of_edges() / n) if n else 0
            over = sum(1 for m in mols if m.diameter > thr + 1e-9)
            row = {"pct": p, "threshold": round(thr, 4), "edges": G.number_of_edges(), "avg_degree": round(deg, 2),
                   "cliques": n_raw, "molecules": len(mols), "covered_atoms": cover, "diameter_over_thr": over,
                   "max_size": max([m.n for m in mols], default=0), "size_dist": dict(sorted(dist.items())),
                   "truncated": trunc}
            rows.append(row)
            print(f"  {p:5.2f} {thr:7.4f} {G.number_of_edges():7d} {deg:5.1f} {n_raw:7d} {len(mols):5d} {cover:5d} "
                  f"{row['max_size']:4d} {over:5d}  {row['size_dist']}" + ("  ⚠ 열거 중단(MAX_CLIQUES/시간 초과)" if trunc else ""))
        return {"sweep": rows, "percentiles": pcts}

    G, thr, _ = _graph_for(D, pct=pct, knn=knn, mutual=mutual)
    mols, n_raw, truncated = _molecules_from(G, D, ids, atoms, min_size=min_size, merge=merge, merge_mode=merge_mode)
    degs = [d for _, d in G.degree()]
    size_dist: dict[int, int] = {}
    for m in mols:
        size_dist[m.n] = size_dist.get(m.n, 0) + 1
    cover = len({a for m in mols for a in m.atoms})
    print(f"\n[build] pct={pct} → threshold={thr:.4f} · knn={knn}({'mutual' if mutual else 'union'}) · "
          f"edges={G.number_of_edges()} · degree mean={np.mean(degs):.2f} max={max(degs) if degs else 0}")
    over = sum(1 for m in mols if m.diameter > thr + 1e-9)
    print(f"[build] 최대 클리크(≥{min_size})={n_raw} → {merge_mode}(jaccard≥{merge}) 후 분자={len(mols)} · "
          f"포함 원자={cover}/{n} · diameter>임계 {over}개 · 크기 분포={dict(sorted(size_dist.items()))}"
          + ("\n[build] ⚠ 클리크 열거가 MAX_CLIQUES/MAX_CLIQUE_SECONDS 에서 중단됐다 — 이 스냅샷은 완전하지 않다. "
             "knn/pct 를 낮추고 다시 빌드하라." if truncated else ""))
    if dry_run:
        return {"molecules": len(mols), "threshold": thr, "truncated": truncated}

    params = {"pct": pct, "knn": knn, "min_size": min_size, "merge": merge, "merge_mode": merge_mode, "mutual": mutual}
    snap_name = f"{_ts()}_p{pct:g}_k{knn}_s{min_size}.json"
    snap = {
        # threshold 는 반올림하지 않는다 — 판정용 값(observe 의 below_eps·뷰어의 snapshot_threshold)이 6자리 반올림값을
        # 쓰면 원래 임계와 반올림 임계 사이 거리가 뒤집힌다(codex 2차 260908). 표시는 읽는 쪽이 반올림한다.
        "created": time.time(), "params": params, "model": model, "threshold": float(thr),
        "n_atoms": n, "n_edges": G.number_of_edges(), "n_cliques_raw": n_raw, "n_molecules": len(mols),
        "truncated": truncated, "stale_checked": stale_checked,
        "stale_embeddings": (len(stale) if stale_checked else None), "unverifiable_updated": len(unverifiable),
        "missing_atoms_in_npz": len(missing),
        "unembedded_atoms": len(unembedded), "covered_atoms": cover, "distance_percentiles": pcts,
        "distance_stats": {"mean": round(float(upper.mean()), 4), "std": round(float(upper.std()), 4)},
        "size_dist": {str(k): v for k, v in sorted(size_dist.items())},
        "molecules": [asdict(m) for m in mols],
    }
    md.ensure()
    (md.snapshots / snap_name).write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")

    # index: 최신 스냅샷의 분자를 in_latest=True 로, 예전 것은 False 로(이름·문서는 보존)
    idx = md.load_index()
    for m in idx["molecules"].values():
        m["in_latest"] = False
    a2m: dict[str, list[str]] = {}
    for m in mols:
        ent = idx["molecules"].setdefault(m.id, {"id": m.id, "named": False})
        ent.update({"atoms": m.atoms, "n": m.n, "cohesion": m.cohesion, "diameter": m.diameter,
                    "merged_from": m.merged_from, "periphery": m.periphery, "layers": m.layers,
                    "titles": m.titles, "in_latest": True, "snapshot": snap_name})
        ent.setdefault("atom_links", [])
        for aid in m.atoms:
            a2m.setdefault(aid, []).append(m.id)
    idx.update({"latest_snapshot": f"snapshots/{snap_name}", "params": params, "model": model,
                "threshold": float(thr), "n_atoms": n, "n_molecules": len(mols), "truncated": truncated,
                "stale_embeddings": (len(stale) if stale_checked else None),
                "distance_percentiles": pcts, "atom_to_molecules": a2m})
    md.save_index(idx)
    print(f"[build] 스냅샷 {md.snapshots / snap_name}\n[build] index {md.index}")
    return {"snapshot": snap_name, "molecules": len(mols), "threshold": thr, "size_dist": size_dist,
            "truncated": truncated, "stale_embeddings": (len(stale) if stale_checked else None)}


# ── 3. name (LLM) ────────────────────────────────────────────────
_DOC_TOOL = {
    "name": "molecule_doc",
    "description": "Write the concept document for one molecule (a cluster of mutually-close knowledge atoms).",
    "input_schema": {
        "type": "object",
        "required": ["name_en", "name_ko", "slug", "one_line", "summary", "why_these_cohere",
                     "characteristics", "related_concepts", "search_findings", "missing_atoms", "confidence"],
        "properties": {
            "name_en": {"type": "string", "description": "Concept name in English (a place, person, movement, industry, period, phenomenon). NOT a list of the members."},
            "name_ko": {"type": "string", "description": "Same concept in Korean."},
            "slug": {"type": "string", "description": "a-z0-9 and hyphens only, from name_en (e.g. leonardo-da-vinci)."},
            "one_line": {"type": "string", "description": "One Korean sentence: what this molecule is."},
            "summary": {"type": "string", "description": "3-6 Korean sentences. Cite member atoms inline as [[atm_...]]. Proper nouns may stay in English."},
            "why_these_cohere": {"type": "string", "description": "What is the shared concept — the intersection / hub the members orbit. Korean, 2-4 sentences."},
            "characteristics": {"type": "array", "items": {"type": "string"}, "description": "3-6 short Korean bullets: traits of the group's knowledge."},
            "related_concepts": {"type": "array", "items": {"type": "string"}, "description": "External concept names (English), 3-8."},
            "search_findings": {
                "type": "array",
                "items": {"type": "object", "required": ["claim", "url"],
                          "properties": {"claim": {"type": "string"}, "url": {"type": "string"}}},
                "description": "Facts NOT in the member atoms, each with the web_search url that supports it. Empty array if no search was made.",
            },
            "missing_atoms": {"type": "array", "items": {"type": "string"},
                              "description": "Concrete concepts that should exist as atoms to complete this molecule but do not yet (the 'da Vinci' case: the hub itself). Empty if the hub is already a member."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
}

_NAME_SYSTEM = (
    "You are a knowledge-graph curator for a geography research tool. You receive one 'molecule': a small "
    "cluster of knowledge atoms that an embedding model found mutually close (every pair is close). Your job "
    "is to identify the single CONCEPT that explains why they cohere — the intersection, the hub they orbit — "
    "and to write the molecule document by calling molecule_doc exactly once.\n\n"
    "RULES\n"
    "- CLOSED WORLD. The member atoms are your evidence. You may run at most 2 web_search queries to CONFIRM "
    "or NAME the concept; anything you learn there that is not in the atoms goes ONLY into search_findings "
    "with its url — never into summary as a bare fact.\n"
    "- The name is a CONCEPT (place, person, dynasty, movement, industry, trade network, period, phenomenon), "
    "not an enumeration of the members. Example: atoms about François I, château design and Italian artists "
    "→ 'Italian Renaissance import to the Loire' or 'Leonardo da Vinci at Amboise', not 'François I, "
    "architecture and Italy'.\n"
    "- missing_atoms: if the hub concept itself has no atom among the members (the da Vinci case), name it "
    "concretely so it can be created later; leave empty if the hub is already a member.\n"
    "- Cite member atoms inline as [[atm_id]] in summary. Do not cite ids that are not members.\n"
    "- Bilingual: name_en + name_ko. one_line/summary/why_these_cohere/characteristics in Korean (proper nouns "
    "may stay in English); related_concepts in English. Call the cluster '분자(molecule)' and its members "
    "'원자(atom)' in Korean prose.\n"
    "- Array fields (characteristics, related_concepts, missing_atoms, search_findings) must be real JSON arrays, "
    "never a single string.\n"
    "- Never invent facts; if the members do not really share a concept, say so in why_these_cohere and set "
    "confidence low (< 0.4)."
)


def _atoms_block(mol: dict, atoms: dict[str, Atom]) -> str:
    lines = []
    for aid in mol["atoms"]:
        a = atoms.get(aid)
        if not a:
            lines.append(f"- [[{aid}]] (missing atom file)")
            continue
        period = ""
        if a.period_start is not None or a.period_end is not None:
            period = f" period={a.period_start or '?'}..{a.period_end or '?'}"
        lines.append(
            f"- [[{a.id}]] ({a.layer}/{a.category or 'other'}/{a.scope}{period})\n"
            f"  title: {a.title}\n  body: {a.body}\n"
            f"  tags: {' '.join('#' + t for t in a.tags)}\n  entities: {' '.join('@' + e for e in a.entities)}"
        )
    return "\n".join(lines)


_ARRAY_FIELDS = ("characteristics", "related_concepts", "missing_atoms", "search_findings")
_STRING_FIELDS = ("name_en", "name_ko", "slug", "one_line", "summary", "why_these_cohere")
# 검증(260907)에서 추가로 확인된 누출 형태: summary 문자열 끝에 '<parameter name="why_these_cohere">…' 가
# 딸려 오고 why_these_cohere 필드는 비어 있는 경우 2건 → <parameter 도 깨짐으로 본다.
_CORRUPT_RE = re.compile(
    r"<(?:value|item|!\[CDATA\[)|</?(?:characteristics|related_concepts|missing_atoms|search_findings)|</?parameter\b"
)
_PARAM_RE = re.compile(r'<parameter\s+name="(\w+)"\s*>(.*?)(?=<parameter\s+name=|</parameter>|\Z)', re.S)


def _as_list(v) -> list[str]:
    """배열이어야 할 필드가 문자열로 오는 경우를 복구.

    실측(260907, sonnet-5 · thinking off · tool_choice auto): 배열 필드가 '<value>a</value><value>b</value>'
    나 '<item>…</item>' 문자열로 오고, 그 뒤에 '</characteristics><related_concepts>[…]' 처럼 **다음 필드들이
    통째로 딸려 오는** 경우가 34건 중 9건. 여기서는 항목만 뽑고, 딸려 온 필드는 _rescue_leaked 가 되살린다.
    """
    if v is None:
        return []
    if isinstance(v, (list, tuple)) and len(v) >= 3 and all(len(str(x).strip()) <= 1 for x in v):
        # 문자열이 글자 단위로 쪼개진 리스트('<![CDATA[]]>' → ['<','!','[',…]) — 검증에서 2건 발견.
        # 다시 이어 붙여 문자열 경로로 보낸다(마커만 남으면 빈 리스트가 된다).
        v = "".join(str(x) for x in v)
    if isinstance(v, (list, tuple)):
        items = [str(x) for x in v]
    else:
        s = str(v)
        s = re.split(r"</(?:characteristics|related_concepts|missing_atoms|search_findings)>", s, maxsplit=1)[0]
        if "<value>" in s or "<item>" in s:
            items = re.findall(r"<(?:value|item)>(.*?)</(?:value|item)>", s, re.S) or re.split(r"</?(?:value|item)>", s)
        elif s.strip().startswith("["):
            try:
                items = [str(x) for x in json.loads(s)]
            except Exception:  # noqa: BLE001
                items = re.split(r"\r?\n", s)
        else:
            items = re.split(r"\r?\n|(?<=[.。])\s*[-•]\s+|^\s*[-•]\s+", s, flags=re.M)
    out = []
    for x in items:
        x = re.sub(r"</?(?:value|item)>|<!\[CDATA\[|\]\]>", "", x).strip().lstrip("-•* ").strip()
        if x and not x.startswith("<"):
            out.append(x)
    return out


def _rescue_leaked(d: dict) -> None:
    """어떤 필드 문자열 속에 '<related_concepts>[…]</related_concepts>' 처럼 딸려 온 다른 필드를 되살린다."""
    blobs = [str(v) for v in d.values() if isinstance(v, str) and _CORRUPT_RE.search(v)]
    for blob in blobs:
        for k in _ARRAY_FIELDS:
            if d.get(k) not in (None, "", [], {}) and not isinstance(d.get(k), str):
                continue
            m = re.search(rf"<{k}[^>]*>(.*?)</{k}>", blob, re.S)
            if not m:
                continue
            raw = m.group(1).strip()
            try:
                d[k] = json.loads(raw)
            except Exception:  # noqa: BLE001
                d[k] = raw
        # '<parameter name="k">…' 형태로 딸려 온 필드(문자열·배열 모두) — 비어 있는 필드만 되살린다.
        for k, raw in _PARAM_RE.findall(blob):
            if k not in _ARRAY_FIELDS and k not in _STRING_FIELDS and k != "confidence":
                continue
            if d.get(k) not in (None, "", [], {}):
                continue
            raw = raw.strip()
            if k in _ARRAY_FIELDS:
                try:
                    d[k] = json.loads(raw)
                except Exception:  # noqa: BLE001
                    d[k] = raw
            else:
                d[k] = raw
    # 누출 원본 필드에서 딸려 온 꼬리('<parameter …' 이후)와 닫는 태그를 잘라 낸다.
    for k, v in list(d.items()):
        if isinstance(v, str) and "<parameter" in v:
            d[k] = re.split(r"<parameter\s+name=", v, maxsplit=1)[0].rstrip()
        if isinstance(d.get(k), str) and "</parameter>" in d[k]:
            d[k] = d[k].replace("</parameter>", "").rstrip()


def is_corrupt(data: dict | None) -> bool:
    """도구 입력이 'XML 누출' 형태로 깨졌는가(배열 필드가 문자열, 또는 <value>/<item> 마커)."""
    if not data:
        return True
    for k in _ARRAY_FIELDS:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return True
    return any(isinstance(v, str) and _CORRUPT_RE.search(v) for v in data.values())


def _clean_doc(data: dict, members: list[str]) -> dict:
    d = dict(data or {})
    d["parse_rescued"] = is_corrupt(d)
    if d["parse_rescued"]:
        _rescue_leaked(d)
    d["name_en"] = html.unescape(str(d.get("name_en") or "")).strip()[:120]   # 모델이 '&amp;' 를 내기도 한다
    d["name_ko"] = html.unescape(str(d.get("name_ko") or "")).strip()[:120]
    s = slug(str(d.get("slug") or d["name_en"]))
    d["slug"] = s if _SLUG_OK.match(s) else slug(d["name_en"])
    for k in ("one_line", "summary", "why_these_cohere"):
        d[k] = html.unescape(str(d.get(k) or "")).strip()
    for k in ("characteristics", "related_concepts", "missing_atoms"):
        d[k] = [html.unescape(x) for x in _as_list(d.get(k))][:12]
    sf = []
    raw_sf = d.get("search_findings") or []
    if isinstance(raw_sf, str):
        try:
            raw_sf = json.loads(raw_sf)
        except Exception:  # noqa: BLE001
            raw_sf = [{"claim": x, "url": ""} for x in _as_list(raw_sf)]
    for f in raw_sf if isinstance(raw_sf, list) else []:
        if isinstance(f, dict) and f.get("claim"):
            sf.append({"claim": str(f["claim"]).strip(), "url": str(f.get("url") or "").strip()})
        elif isinstance(f, str) and f.strip():
            sf.append({"claim": f.strip(), "url": ""})
    d["search_findings"] = sf[:8]
    try:
        d["confidence"] = max(0.0, min(1.0, float(d.get("confidence", 0.5))))
    except (TypeError, ValueError):
        d["confidence"] = 0.5
    cited = set(_ATM_RE.findall(d["summary"] + d["why_these_cohere"]))
    d["foreign_citations"] = sorted(cited - set(members))   # 멤버 아닌 인용 — 표시만 한다
    return d


def _name_one(settings: Settings, mol: dict, atoms: dict[str, Atom], *, searches: int, deadline_s: float) -> dict:
    """분자 1개 이름 붙이기. 반환 {doc, cost_usd, model, web_searches, retries, error}."""
    periphery = [aid for aid in (mol.get("periphery") or []) if aid in atoms][:8]
    peri_block = ""
    if periphery:
        # 흡수된 이웃 클리크의 원자 — 경계 판단용 맥락일 뿐 멤버가 아니다(인용 금지).
        peri_block = (
            "\n\nPERIPHERY (NOT members — atoms that are close to most but not all members; context only, "
            "do NOT cite them):\n" + "\n".join(f"- {atoms[a].title}" for a in periphery)
        )
    user = (
        f"MOLECULE {mol['id']} — {mol['n']} atoms, cohesion={mol['cohesion']} (mean cosine distance), "
        f"diameter={mol['diameter']}, layers={json.dumps(mol.get('layers') or {})}\n\n"
        f"MEMBER ATOMS (the closed world):\n{_atoms_block(mol, atoms)}{peri_block}\n\n"
        "Identify the shared concept and call molecule_doc once."
    )
    out = {"doc": None, "cost_usd": 0.0, "model": None, "web_searches": 0, "retries": 0, "error": None}
    attempts = []
    if searches > 0:
        attempts.append(([{"type": "web_search_20260209", "name": "web_search", "max_uses": searches}, _DOC_TOOL],
                         {"type": "auto"}))
    attempts.append(([_DOC_TOOL], {"type": "tool", "name": "molecule_doc"}))
    for i, (tools, choice) in enumerate(attempts):
        try:
            resp = llm.call(settings, system=_NAME_SYSTEM, messages=[{"role": "user", "content": user}],
                            tools=tools, tool_choice=choice, max_tokens=6000, effort="medium",
                            deadline_s=deadline_s, role="fact")
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            out["retries"] = i
            continue
        out["cost_usd"] += llm.spend(resp)
        out["model"] = llm.used_model(resp)
        u = getattr(resp, "usage", None)
        out["web_searches"] += getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
        data = llm.tool_input(resp, "molecule_doc")
        if data and is_corrupt(data) and i + 1 < len(attempts):
            # 배열 필드가 문자열/XML 마커로 깨졌다 — 다음 시도(강제 tool_choice, web_search 없음)로.
            # 마지막 시도였다면 아래에서 복구 파서로 살린다(parse_rescued=True 로 표시).
            out["error"] = "corrupt tool input (array fields as string / <value> markers)"
            out["retries"] = i + 1
            continue
        if data:
            out["doc"] = _clean_doc(data, mol["atoms"])
            out["retries"] = i
            out["error"] = None
            break
        out["error"] = "no molecule_doc tool_use in response"
        out["retries"] = i
    return out


def _write_doc(md: MolDir, mol: dict, doc: dict, atoms: dict[str, Atom], *, cost: float, model: str | None,
               web_searches: int, retries: int, created: float | None = None) -> Path:
    fm = {
        "id": mol["id"], "name_en": doc["name_en"], "name_ko": doc["name_ko"], "slug": doc["slug"],
        "atoms": mol["atoms"], "n": mol["n"], "cohesion": mol["cohesion"], "diameter": mol["diameter"],
        "merged_from": mol.get("merged_from", 1), "periphery": mol.get("periphery") or [],
        "layers": mol.get("layers") or {},
        "snapshot": mol.get("snapshot"), "params": mol.get("params"), "status": STATUS,
        "confidence": doc["confidence"], "atom_links": mol.get("atom_links") or [],
        "missing_atoms": doc["missing_atoms"], "related_concepts": doc["related_concepts"],
        "foreign_citations": doc.get("foreign_citations") or [],
        "parse_rescued": bool(doc.get("parse_rescued")),
        "model": model, "web_searches": web_searches, "retries": retries,
        "created": created or time.time(), "cost_usd": round(cost, 4),
    }
    p = mol.get("params") or {}
    members = "\n".join(
        f"- [[{aid}]] **{atoms[aid].title}** ({atoms[aid].layer}/{atoms[aid].scope})" if aid in atoms
        else f"- [[{aid}]]" for aid in mol["atoms"]
    )
    links = _links_block(mol.get("atom_links") or [], mol.get("atom_link_how") or {}, atoms)
    peri = "\n".join(
        f"- [[{aid}]] {atoms[aid].title}" if aid in atoms else f"- [[{aid}]]" for aid in (mol.get("periphery") or [])
    )
    body = (
        f"---\n{json.dumps(fm, ensure_ascii=False, indent=2, sort_keys=True)}\n---\n\n"
        f"# {doc['name_ko']} · {doc['name_en']}\n\n"
        f"`{mol['id']}` · 원자 {mol['n']}개 · cohesion {mol['cohesion']} · diameter {mol['diameter']} · "
        f"스냅샷 `{mol.get('snapshot')}` (pct={p.get('pct')}, knn={p.get('knn')}, min={p.get('min_size')}) · "
        f"**등급: {STATUS}** (기계 산출 — 사람 승인 전) · 신뢰도 {doc['confidence']:.2f}\n\n"
        f"> {doc['one_line']}\n\n"
        f"## 요약\n{doc['summary']}\n\n"
        f"## 왜 결합하는가\n{doc['why_these_cohere']}\n\n"
        + ("## 특징\n" + "\n".join(f"- {c}" for c in doc["characteristics"]) + "\n\n" if doc["characteristics"] else "")
        + ("## 관련 개념\n" + "\n".join(f"- {c}" for c in doc["related_concepts"]) + "\n\n" if doc["related_concepts"] else "")
        + ("## 검색 근거\n" + "\n".join(f"- {f['claim']}" + (f" — <{f['url']}>" if f["url"] else "") for f in doc["search_findings"]) + "\n\n"
           if doc["search_findings"] else "## 검색 근거\n- (웹 검색 근거 없음 — 멤버 원자만으로 작성)\n\n")
        + ("## 빠진 원자(가설)\n" + "\n".join(f"- {m}" for m in doc["missing_atoms"]) + "\n\n"
           if doc["missing_atoms"] else "## 빠진 원자(가설)\n- (중심 개념이 이미 멤버에 있음)\n\n")
        + f"## 원자 연결\n{links}\n\n"
        + (f"## 멤버 밖 인용(검토 필요)\n" + "\n".join(f"- [[{c}]]" for c in fm["foreign_citations"]) + "\n\n"
           if fm["foreign_citations"] else "")
        + f"## 멤버 원자\n{members}\n"
        + (f"\n## 주변 원자(흡수된 이웃 클리크 — 멤버 아님)\n{peri}\n" if peri else "")
    )
    path = md.doc(mol["id"])
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    return path


def _links_block(links: list[str], how: dict[str, str], atoms: dict[str, Atom], cap: int = 40) -> str:
    if not links:
        return "- (일치하는 원자 없음 — 정상. `reify` 로 재검사)"
    lines = []
    for aid in links[:cap]:
        t = atoms[aid].title if aid in atoms else ""
        lines.append(f"- [[{aid}]] {t} ({how.get(aid, 'match')})")
    if len(links) > cap:
        lines.append(f"- … 외 {len(links) - cap}개(프론트매터 atom_links 참조)")
    return "\n".join(lines)


def _pick_for_naming(cands: list[dict], named_already: list[dict], *, limit: int, order: str, overlap: int) -> tuple[list[dict], list[tuple[str, str]]]:
    """이름 붙일 분자 고르기. order=cohesion: 응집 강한 순 그대로. order=diverse: 같은 순서로 훑되,
    이미 고른(또는 이미 이름 있는) 분자와 멤버 원자를 overlap 개 이상 공유하면 이번엔 건너뛴다 —
    같은 원자 무리를 두고 미세하게 다른 클리크들('청 왕조 목록' 6종)에 예산을 겹쳐 쓰지 않기 위해.
    반환 (선택, [(건너뛴 id, 이유)])."""
    cands = sorted(cands, key=lambda m: (m.get("cohesion", 1.0), -m.get("n", 0), m["id"]))
    if order != "diverse":
        return cands[:limit], []
    chosen: list[dict] = []
    skipped: list[tuple[str, str]] = []
    taken = [set(m["atoms"]) for m in named_already]
    for m in cands:
        if len(chosen) >= limit:
            break
        ms = set(m["atoms"])
        hit = next((i for i, t in enumerate(taken) if len(ms & t) >= overlap), None)
        if hit is not None:
            skipped.append((m["id"], f"deferred: shares ≥{overlap} atoms with an already-named molecule"))
            continue
        chosen.append(m)
        taken.append(ms)
    return chosen, skipped


def cmd_name(settings: Settings, *, limit: int = 60, budget_usd: float = 8.0, searches: int = 2,
             parallel: int = 4, force: bool = False, deadline_s: float = 180.0, only: list[str] | None = None,
             order: str = "diverse", overlap: int = 2) -> dict:
    """cohesion 낮은(응집 강한) 순으로 limit 개까지, 총비용 budget 안에서 이름·문서를 만든다."""
    md = _mol_dir(settings)
    idx = md.load_index()
    if not idx.get("latest_snapshot"):
        sys.exit("[molecule] index 에 스냅샷이 없습니다. 먼저 `build` 를 실행하세요.")
    atoms = _load_atoms(settings)
    params = idx.get("params") or {}
    latest = [m for m in idx["molecules"].values() if m.get("in_latest")]
    cands = list(latest)
    if only:
        cands = [m for m in cands if m["id"] in set(only)]
    if not force:
        cands = [m for m in cands if not m.get("named")]
    named_already = [] if (force or only) else [m for m in latest if m.get("named")]
    cands, skipped = _pick_for_naming(cands, named_already, limit=limit, order=order, overlap=overlap)
    for mid, why in skipped:
        idx["molecules"][mid].setdefault("skip_reason", why)
    print(f"[name] 대상 {len(cands)}개 (limit={limit}, order={order}, overlap≥{overlap} 건너뜀 {len(skipped)}, "
          f"budget=${budget_usd}, searches={searches}, parallel={parallel})")
    spent_run = 0.0
    named = failed = retried = 0
    stopped = False
    for b in range(0, len(cands), max(1, parallel)):
        batch = cands[b:b + max(1, parallel)]
        if spent_run >= budget_usd:
            stopped = True
            break
        tasks = []
        for m in batch:
            m["params"] = params
            tasks.append(lambda mm=m: _name_one(settings, mm, atoms, searches=searches, deadline_s=deadline_s))
        results = llm.gather(tasks, settings, limit=parallel)
        for m, r in zip(batch, results):
            if isinstance(r, Exception):
                r = {"doc": None, "cost_usd": 0.0, "model": None, "web_searches": 0, "retries": 0,
                     "error": f"{type(r).__name__}: {r}"}
            spent_run += r["cost_usd"]
            retried += 1 if r["retries"] else 0
            if not r["doc"]:
                failed += 1
                m.update({"named": False, "error": r["error"], "cost_usd": round(m.get("cost_usd", 0.0) + r["cost_usd"], 4)})
                print(f"  ✗ {m['id']} n={m['n']} — {r['error']}")
                continue
            doc = r["doc"]
            m.update({"named": True, "name_en": doc["name_en"], "name_ko": doc["name_ko"], "slug": doc["slug"],
                      "confidence": doc["confidence"], "missing_atoms": doc["missing_atoms"],
                      "doc": f"{m['id']}.md", "model": r["model"], "web_searches": r["web_searches"],
                      "retries": r["retries"], "cost_usd": round(m.get("cost_usd", 0.0) + r["cost_usd"], 4),
                      "named_at": time.time(), "error": None})
            m.pop("skip_reason", None)
            m["_doc"] = doc   # reify 가 이어서 쓰도록 메모리에만
            _write_doc(md, m, doc, atoms, cost=m["cost_usd"], model=r["model"],
                       web_searches=r["web_searches"], retries=r["retries"])
            named += 1
            print(f"  ✓ {m['id']} n={m['n']} coh={m['cohesion']} ${r['cost_usd']:.3f} ws={r['web_searches']} "
                  f"→ {doc['name_ko']} / {doc['name_en']} [{doc['slug']}] conf={doc['confidence']:.2f}")
        for m in batch:
            m.pop("_doc", None)
        idx["spent_usd"] = round(float(idx.get("spent_usd", 0.0)) + sum(
            (r["cost_usd"] if isinstance(r, dict) else 0.0) for r in results), 4)
        md.save_index(idx)   # 배치마다 저장 — 중간에 끊겨도 진행분은 남는다
    if stopped:
        print(f"[name] 예산 ${budget_usd} 도달 — 중단. 남은 분자는 named=false 로 남습니다.")
    print(f"[name] 완료 {named} · 실패 {failed} · 재시도 {retried} · 이번 실행 ${spent_run:.3f} · 누적 ${idx['spent_usd']:.3f}")
    return {"named": named, "failed": failed, "retried": retried, "spent_usd": round(spent_run, 4), "stopped": stopped}


# ── 4. reify — atom–molecule 커넥션 ──────────────────────────────
def _read_doc(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not m:
        return {}, text
    try:
        return json.loads(m.group(1)), text[m.end():]
    except Exception:  # noqa: BLE001
        return {}, text


def _doc_from_md(fm: dict, body: str) -> dict:
    """mol_*.md 본문에서 name 단계 결과를 되살린다(reify 가 문서를 다시 쓰기 위해)."""
    def sec(title: str) -> str:
        m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", body, re.S | re.M)
        return (m.group(1).strip() if m else "")

    def bullets(title: str) -> list[str]:
        return [ln[2:].strip() for ln in sec(title).splitlines() if ln.startswith("- ") and not ln.startswith("- (")]

    one = re.search(r"^> (.+)$", body, re.M)
    sf = []
    for ln in bullets("검색 근거"):
        mm = re.match(r"^(.*?)(?: — <([^>]+)>)?$", ln)
        if mm:
            sf.append({"claim": mm.group(1).strip(), "url": (mm.group(2) or "").strip()})
    u = html.unescape
    return {
        "name_en": u(fm.get("name_en", "")), "name_ko": u(fm.get("name_ko", "")), "slug": fm.get("slug", ""),
        "one_line": one.group(1).strip() if one else "", "summary": sec("요약"), "why_these_cohere": sec("왜 결합하는가"),
        "characteristics": _as_list(bullets("특징")),   # 글자 단위로 쪼개진 옛 문서는 여기서 비워진다
        "related_concepts": [u(x) for x in (fm.get("related_concepts") or bullets("관련 개념"))],
        "search_findings": sf, "missing_atoms": [u(x) for x in (fm.get("missing_atoms") or bullets("빠진 원자(가설)"))],
        "confidence": float(fm.get("confidence", 0.5) or 0.5), "foreign_citations": fm.get("foreign_citations") or [],
        "parse_rescued": bool(fm.get("parse_rescued")),
    }


def cmd_reify(settings: Settings) -> dict:
    """이름 붙은 분자마다: 지식 index 의 entities/tags 키 또는 원자 title 슬러그가 분자 slug
    (또는 name_en 슬러그)와 일치하는 원자 → atom_links. missing_atoms 슬러그가 엔티티로 이미
    있으면('다빈치가 나중에 원자로 발견되면') missing-atom-match 로 기록한다."""
    md = _mol_dir(settings)
    idx = md.load_index()
    atoms = _load_atoms(settings)
    # 지식 index.json 은 읽지도 않는다 — Store.index() 는 캐시가 없으면 재구축해 **파일을 쓴다**.
    # 이 모듈은 docs/knowledge/molecule/ 밖에 아무것도 쓰지 않아야 하므로 원자 파일에서 직접 맵을 만든다.
    kidx: dict[str, dict] = {"atoms": {}, "entities": {}, "tags": {}}
    title_slug: dict[str, list[str]] = {}
    for aid, a in atoms.items():
        kidx["atoms"][aid] = {"title": a.title}
        for e in a.entities:
            kidx["entities"].setdefault(slug(e), []).append(aid)
        for t in a.tags:
            kidx["tags"].setdefault(slug(t), []).append(aid)
        title_slug.setdefault(slug(a.title), []).append(aid)

    now = time.time()
    records: list[dict] = []
    n_mols = 0
    for m in idx["molecules"].values():
        if not m.get("named") or not md.doc(m["id"]).exists():
            continue
        n_mols += 1
        members = set(m["atoms"])
        keys = {m.get("slug") or "", slug(m.get("name_en") or "")} - {"", "unknown"}
        links: dict[str, str] = {}

        def add(aid: str, how: str, key: str):
            if aid in members or aid in links or aid not in kidx.get("atoms", {}):
                return
            links[aid] = f"{how}:{key}"     # 어느 키로 걸렸는지 같이 남긴다(검토용)

        for key in sorted(keys):
            for aid in kidx.get("entities", {}).get(key, []):
                add(aid, "slug-match", key)
            for aid in kidx.get("tags", {}).get(key, []):
                add(aid, "slug-match", key)
            for aid in title_slug.get(key, []):
                add(aid, "title-match", key)
        for miss in m.get("missing_atoms") or []:
            # 한국어 설명이 섞인 항목은 slug() 가 라틴 토큰만 남긴다("Dalmatia 식생 …" → "dalmatia").
            # 그래서 넓은 엔티티에 걸릴 수 있다 — how 에 키를 붙여 두므로 검토 때 바로 보인다.
            ms = slug(miss)
            if ms in ("", "unknown"):
                continue
            for aid in kidx.get("entities", {}).get(ms, []):
                add(aid, "missing-atom-match", ms)
            for aid in title_slug.get(ms, []):
                add(aid, "missing-atom-match", ms)
        ordered = sorted(links)
        m["atom_links"] = ordered
        m["atom_link_how"] = {aid: links[aid] for aid in ordered}
        for aid in ordered:
            how, _, key = links[aid].partition(":")
            records.append({"molecule": m["id"], "atom": aid, "how": how, "key": key, "at": now})
        # 문서 재작성(프론트매터 atom_links + '원자 연결' 절)
        fm, body = _read_doc(md.doc(m["id"]))
        doc = _doc_from_md(fm, body)
        m.setdefault("params", fm.get("params") or idx.get("params"))
        _write_doc(md, m, doc, atoms, cost=float(fm.get("cost_usd") or m.get("cost_usd") or 0.0),
                   model=fm.get("model") or m.get("model"), web_searches=int(fm.get("web_searches") or 0),
                   retries=int(fm.get("retries") or 0), created=fm.get("created"))
        if ordered:
            print(f"  {m['id']} [{m.get('slug')}] ← {len(ordered)} atoms "
                  f"({', '.join(sorted({links[a] for a in ordered}))})")
    # 스냅샷에는 있으나 이번에 검사하지 않은(이름 없는) 분자는 atom_links 를 건드리지 않는다.
    md.ensure()
    md.links.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    idx["links"] = {"n": len(records), "molecules_with_links": len({r['molecule'] for r in records}), "at": now}
    md.save_index(idx)
    print(f"[reify] 분자 {n_mols}개 검사 · 링크 {len(records)}건 · 링크 있는 분자 {idx['links']['molecules_with_links']} → {md.links}")
    return idx["links"]


# ── CLI ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="python -m geoguesshelper.molecule",
                                 description="원자 임베딩 → 거리 그래프 → 분자(molecule) 스냅샷·문서")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("embed", help="원자 임베딩(증분) → molecule/embeddings.npz")
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--force", action="store_true", help="전부 다시 계산")
    p.add_argument("--device", default=None, help="cuda|cpu (기본: cuda 가능하면 cuda)")

    p = sub.add_parser("build", help="거리 그래프 → 최대 클리크 → 분자 스냅샷")
    p.add_argument("--pct", type=float, default=0.5,
                   help="가깝다의 임계 = 전 쌍 거리 하위 pct 퍼센타일 (README 채택값 0.5; 2.0 은 분자 220개)")
    p.add_argument("--knn", type=int, default=10, help="후보 쌍을 각 노드 상위 k 이웃으로 제한")
    p.add_argument("--min-size", type=int, default=4, help="분자 최소 크기(클리크). 3 이면 수천 개가 나온다(README)")
    p.add_argument("--merge", type=float, default=0.5, help="겹치는 클리크 처리 자카드 임계(0=처리 안 함)")
    p.add_argument("--merge-mode", choices=("absorb", "union"), default="absorb",
                   help="absorb=겹치는 클리크는 흡수(분자는 항상 엄격한 클리크) · union=합집합(먼 쌍 섞일 수 있음)")
    p.add_argument("--mutual", action=argparse.BooleanOptionalAction, default=True,
                   help="kNN 을 상호(mutual)로. --no-mutual 이면 한쪽만 이웃이어도 후보")
    p.add_argument("--sweep", default=None, help="비교만: 쉼표 구분 pct 목록 (예 1.0,2.0,3.0). 저장 안 함")
    p.add_argument("--dry-run", action="store_true", help="저장 없이 요약만")

    p = sub.add_parser("name", help="LLM 으로 분자 이름·문서 작성(cohesion 강한 순, 예산 상한)")
    p.add_argument("--limit", type=int, default=60)
    p.add_argument("--budget", type=float, default=8.0, help="이번 실행 총비용 상한(USD)")
    p.add_argument("--searches", type=int, default=2, help="분자당 web_search 최대 횟수(0=검색 없음)")
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--deadline", type=float, default=180.0, help="호출당 벽시계 상한(초)")
    p.add_argument("--force", action="store_true", help="이미 이름 붙은 것도 다시")
    p.add_argument("--only", action="append", default=[], help="특정 mol_id 만(반복 가능)")
    p.add_argument("--order", choices=("diverse", "cohesion"), default="diverse",
                   help="diverse=응집 순이되 이미 이름 붙은 분자와 원자를 --overlap 개 이상 공유하면 건너뜀 · cohesion=응집 순 그대로")
    p.add_argument("--overlap", type=int, default=2, help="diverse 에서 '겹친다'로 볼 공유 원자 수")

    sub.add_parser("reify", help="atom–molecule 커넥션(slug/title 일치) 재계산 → links.jsonl")

    a = ap.parse_args(argv)
    settings = load_settings()
    if a.cmd == "embed":
        cmd_embed(settings, batch_size=a.batch_size, force=a.force, device=a.device)
    elif a.cmd == "build":
        sweep = [float(x) for x in a.sweep.split(",") if x.strip()] if a.sweep else None
        cmd_build(settings, pct=a.pct, knn=a.knn, min_size=a.min_size, merge=a.merge, merge_mode=a.merge_mode,
                  mutual=a.mutual, sweep=sweep, dry_run=a.dry_run)
    elif a.cmd == "name":
        cmd_name(settings, limit=a.limit, budget_usd=a.budget, searches=a.searches, parallel=a.parallel,
                 force=a.force, deadline_s=a.deadline, only=a.only or None, order=a.order, overlap=a.overlap)
    elif a.cmd == "reify":
        cmd_reify(settings)


if __name__ == "__main__":
    main()
