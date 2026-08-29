"""지식 축적 — 조사 결과를 원자(atom)로 쪼개 쌓고, 다음 조사에서 **참조**한다.

문제: 같은 지역·같은 문명권을 여러 번 조사하면 매번 처음부터 웹 검색을 다시 한다.
"샤를마뉴 강역, 8세기 말"이라는 사실은 리포트마다 새로 조사되고 새로 서술된다.

여기서 하는 것:

  1. 조사가 끝나면 결과를 **원자**로 압축한다.
     원자 = {레이어, 범위, 제목, 본문 2~4문장, 해시태그, 엔티티, 시기, 지리셀}
     레이어는 geography / history / culture / economy / language / architecture / nature.

  2. 원자는 **내용 해시로 주소가 정해진다**(`atm_<hash12>`). 같은 사실은 두 번 저장되지
     않는다. 비슷한 사실(같은 레이어 + 엔티티 겹침 + 태그 자카드 ≥ 임계)은 새로 쓰지 않고
     기존 원자에 백링크와 태그만 병합한다.

  3. 다음 조사 **전에** recall() 이 좌표·태그로 관련 원자를 찾아 프롬프트에 넣는다.
     리서치 프롬프트는 "이건 이미 안다. 다시 검색하지 말고 [[id]] 로 참조하라. 빠진 것만
     찾아라" 로 지시한다 → 웹 검색 횟수와 출력 토큰이 줄어든다(A' 절약).

  4. 보고서는 재사용한 원자를 **본문 복제 대신 링크**로 싣는다.

  5. synthesize() 는 한 엔티티/태그에 걸린 원자 A 와, 그것을 참조한 여러 보고서의
     장소별 조사 B'·C' 를 합쳐 **종합 보고서**를 만든다. A 를 다시 조사하지 않는다.

저장 구조 (docs/knowledge/):
    atoms/<atm_id>.md      원자 1개 (JSON 프론트매터 + 본문)
    places/<geocell>.md    장소 노트 (이 셀에 걸린 원자·보고서 백링크)
    entities/<slug>.md     엔티티 노트 (샤를마뉴, 카롤링거 제국 …)
    reports/<name>.md      보고서 요약 노트
    index.json             빠른 조회용 인덱스 (없으면 파일에서 재구축)

외부 의존성 없음 — 지오해시·해시·자카드 전부 표준 라이브러리로 구현한다.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import llm
from .config import Settings

# ── 레이어 / 범위 ────────────────────────────────────────────────
LAYERS = ("geography", "history", "culture", "economy", "language", "architecture", "nature")
SCOPES = ("point", "city", "region", "country", "polity", "period", "global")

# ── 범주(category) — 닫힌 어휘. 레이어보다 가는 분류, 즉흥 확장 금지 ─────
# docs/plan/atom-density-map-detail_260829.html §taxonomy 의 표를 그대로 옮긴 것.
# 어휘 변경은 그 기획서 개정으로만 한다 — 여기서 슬러그를 늘리거나 줄이지 않는다.
CATEGORIES_BY_LAYER: dict[str, tuple[str, ...]] = {
    "economy": ("industry", "agriculture", "energy", "mining", "logistics", "retail",
                "finance", "tourism-economy", "real-estate", "listed-company"),
    "geography": ("landform", "hydrology", "climate", "coast-port", "pass-corridor",
                  "settlement-pattern"),
    "history": ("polity-rule", "war-conflict", "migration", "founding", "succession",
                "treaty-border", "memory-monument"),
    "culture": ("religion", "festival", "cuisine", "sport", "media-arts", "education",
                "demography", "politics-civic"),
    "language": ("script", "toponymy", "dialect", "signage-language"),
    "architecture": ("housing-typology", "roof-facade", "religious-building",
                      "civic-building", "urban-form", "infrastructure-built"),
    "nature": ("vegetation", "protected-area", "wildlife", "hazard"),
}
# cue = 신설 레이어가 아니다 — 지점(point) 원자에만 붙는 범주. 어느 레이어에도 얹을 수
# 있다(예: 도로 표지판은 architecture 나 geography 레이어에 category=road-signage로도
# 온다). 확대 지도의 마이크로 마커가 이 범주로 걸린다.
CUE_CATEGORIES: tuple[str, ...] = (
    "road-signage", "road-marking", "bollard-guardrail", "utility-pole", "license-plate",
    "vehicle-fleet", "street-furniture", "vegetation-cue", "soil-terrain-cue",
    "camera-coverage", "business-chain", "flag-emblem",
)
CATEGORY_OTHER = "other"
ALL_CATEGORIES: frozenset[str] = frozenset(
    {c for cats in CATEGORIES_BY_LAYER.values() for c in cats}
    | set(CUE_CATEGORIES)
    | {CATEGORY_OTHER}
)

# 범위 → 지오해시 접두 길이(이 길이만큼 일치하면 "그 지역에 해당")
_SCOPE_PRECISION = {
    "point": 7,     # ~150 m
    "city": 5,      # ~5 km
    "region": 3,    # ~156 km
    "country": 2,   # ~1250 km
    "polity": 2,
    "period": 0,    # 지리 무관
    "global": 0,
}
_SCOPE_RADIUS_KM = {
    "point": 1, "city": 25, "region": 200, "country": 1200, "polity": 2000,
    "period": 0, "global": 0,
}

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── 지오해시 ─────────────────────────────────────────────────────
def geohash(lat: float, lng: float, precision: int = 7) -> str:
    """표준 base32 지오해시. 접두사 비교만으로 '같은 동네' 판정이 된다."""
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return ""
    lat_r = [-90.0, 90.0]
    lng_r = [-180.0, 180.0]
    out: list[str] = []
    bits = 0
    nbit = 0
    even = True
    while len(out) < precision:
        if even:
            mid = (lng_r[0] + lng_r[1]) / 2
            if lng > mid:
                bits = (bits << 1) | 1
                lng_r[0] = mid
            else:
                bits <<= 1
                lng_r[1] = mid
        else:
            mid = (lat_r[0] + lat_r[1]) / 2
            if lat > mid:
                bits = (bits << 1) | 1
                lat_r[0] = mid
            else:
                bits <<= 1
                lat_r[1] = mid
        even = not even
        nbit += 1
        if nbit == 5:
            out.append(_B32[bits])
            bits = 0
            nbit = 0
    return "".join(out)


def haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    try:
        p1, p2 = math.radians(float(a_lat)), math.radians(float(b_lat))
        dp = p2 - p1
        dl = math.radians(float(b_lng) - float(a_lng))
    except (TypeError, ValueError):
        return float("inf")
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def slug(s: str) -> str:
    s = _SLUG_RE.sub("-", (s or "").strip().lower()).strip("-")
    return s[:60] or "unknown"


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def atom_id(layer: str, entities: list[str], body: str) -> str:
    """내용 주소. 같은 사실이면 같은 id → 중복 저장이 구조적으로 불가능하다."""
    basis = f"{layer}|{','.join(sorted(entities or []))}|{_norm_text(body)}"
    return "atm_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


# ── 원자 ─────────────────────────────────────────────────────────
@dataclass
class Atom:
    id: str
    layer: str
    scope: str
    title: str
    body: str
    category: str | None = None  # ALL_CATEGORIES 의 슬러그. None = 미분류(읽을 때 "other"로 취급)
    tags: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    period_start: int | None = None
    period_end: int | None = None
    lat: float | None = None
    lng: float | None = None
    heading: float | None = None  # 촬영 방위(°) — point 원자(P1 지점 단서)의 방위 화살표용
    radius_km: float = 0.0
    cell: str = ""
    lang: str = "en"
    sources: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    uses: int = 1
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def meta(self) -> dict:
        d = asdict(self)
        d.pop("body", None)
        return d

    def to_md(self) -> str:
        fm = json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)
        tags = " ".join(f"#{t}" for t in self.tags)
        refs = "\n".join(f"- [[{r}]]" for r in self.refs)
        reps = "\n".join(f"- `{r}`" for r in self.reports)
        period = ""
        if self.period_start is not None or self.period_end is not None:
            period = f"\n**시기**: {self.period_start or '?'} ~ {self.period_end or '?'}\n"
        return (
            f"---\n{fm}\n---\n\n"
            f"# {self.title}\n\n"
            f"`{self.id}` · **{self.layer}** / {self.scope}"
            + (f" · cell `{self.cell}`" if self.cell else "")
            + f"\n{period}\n{self.body}\n\n"
            + (f"**태그**: {tags}\n\n" if tags else "")
            + (f"## 참조\n{refs}\n\n" if refs else "")
            + (f"## 이 원자를 쓴 보고서\n{reps}\n" if reps else "")
        )

    @staticmethod
    def from_md(text: str) -> "Atom | None":
        if not text.startswith("---"):
            return None
        try:
            # 구분자는 줄 단위로 찾는다 — 본문/URL 속의 "---"(예: bgp-5---employment)에
            # 걸려 프론트매터가 중간에서 잘리면 안 된다.
            m = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
            if not m:
                return None
            data = json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            return None
        allowed = {f for f in Atom.__dataclass_fields__}  # type: ignore[attr-defined]
        return Atom(**{k: v for k, v in data.items() if k in allowed})


# ── 저장소 ───────────────────────────────────────────────────────
class Store:
    """파일 기반 지식 저장소. index.json 은 캐시일 뿐, 진실은 .md 파일이다."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.atoms_dir = self.root / "atoms"
        self.places_dir = self.root / "places"
        self.entities_dir = self.root / "entities"
        self.reports_dir = self.root / "reports"
        self.index_path = self.root / "index.json"
        self._index: dict | None = None

    # -- 인덱스 --------------------------------------------------
    def index(self) -> dict:
        if self._index is not None:
            return self._index
        if self.index_path.exists():
            try:
                self._index = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(self._index, dict) and "atoms" in self._index:
                    self._index.setdefault("categories", {})  # 구버전 index.json 하위호환
                    return self._index
            except Exception:  # noqa: BLE001
                pass
        self._index = self.rebuild()
        return self._index

    def rebuild(self) -> dict:
        idx: dict[str, Any] = {
            "atoms": {}, "cells": {}, "entities": {}, "tags": {}, "categories": {}, "reports": {},
        }
        if self.atoms_dir.exists():
            for p in self.atoms_dir.glob("atm_*.md"):
                a = Atom.from_md(p.read_text(encoding="utf-8"))
                if a:
                    self._index_atom(idx, a)
        self._index = idx
        self._save_index()
        return idx

    def _index_atom(self, idx: dict, a: Atom) -> None:
        idx["atoms"][a.id] = a.meta()
        if a.cell:
            idx["cells"].setdefault(a.cell[: _SCOPE_PRECISION.get(a.scope, 5)] or "*", []).append(a.id)
        for e in a.entities:
            idx["entities"].setdefault(e, [])
            if a.id not in idx["entities"][e]:
                idx["entities"][e].append(a.id)
        for t in a.tags:
            idx["tags"].setdefault(t, [])
            if a.id not in idx["tags"][t]:
                idx["tags"][t].append(a.id)
        cat = (a.category or "").strip()
        if cat:
            idx.setdefault("categories", {}).setdefault(cat, [])
            if a.id not in idx["categories"][cat]:
                idx["categories"][cat].append(a.id)

    def _save_index(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._index, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.index_path)
        except Exception:  # noqa: BLE001
            pass

    # -- 원자 --------------------------------------------------
    def path_of(self, atom_id: str) -> Path:
        return self.atoms_dir / f"{atom_id}.md"

    def load(self, atom_id: str) -> Atom | None:
        p = self.path_of(atom_id)
        if not p.exists():
            return None
        return Atom.from_md(p.read_text(encoding="utf-8"))

    def save(self, a: Atom) -> Atom:
        self.atoms_dir.mkdir(parents=True, exist_ok=True)
        a.updated = time.time()
        tmp = self.path_of(a.id).with_suffix(".md.tmp")
        tmp.write_text(a.to_md(), encoding="utf-8")
        tmp.replace(self.path_of(a.id))
        idx = self.index()
        self._index_atom(idx, a)
        self._save_index()
        return a

    def all_atoms(self) -> list[Atom]:
        out = []
        if self.atoms_dir.exists():
            for p in sorted(self.atoms_dir.glob("atm_*.md")):
                a = Atom.from_md(p.read_text(encoding="utf-8"))
                if a:
                    out.append(a)
        return out

    def stats(self) -> dict:
        idx = self.index()
        return {
            "atoms": len(idx.get("atoms", {})),
            "entities": len(idx.get("entities", {})),
            "tags": len(idx.get("tags", {})),
            "cells": len(idx.get("cells", {})),
            "reports": len(idx.get("reports", {})),
            "root": str(self.root),
        }


def store_for(settings: Settings) -> Store:
    return Store(settings.knowledge_dir)


# ── 회상(recall) — 조사 전에 이미 아는 것을 모은다 ────────────────
def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def recall(
    settings: Settings,
    *,
    lat=None,
    lng=None,
    place: dict | None = None,
    tags: list[str] | None = None,
    entities: list[str] | None = None,
    limit: int = 12,
    char_budget: int = 6000,
) -> list[Atom]:
    """좌표·태그·엔티티로 관련 원자를 점수순으로 회수한다.

    지리 매칭은 원자의 scope 가 정하는 지오해시 접두 길이로 한다 — point 원자는 같은
    150m 셀에서만, polity 원자는 대륙 규모에서 걸린다. 그래서 '샤를마뉴 강역' 같은 광역
    원자는 프랑스에서도 독일에서도 회수되지만, 특정 골목의 표지판 원자는 그 골목에서만
    회수된다.
    """
    st = store_for(settings)
    idx = st.index()
    if not idx.get("atoms"):
        return []

    q_tags = {slug(t) for t in (tags or []) if t}
    q_ents = {slug(e) for e in (entities or []) if e}
    p = place or {}
    for key in ("city", "region_or_state", "country"):
        if p.get(key):
            q_ents.add(slug(p[key]))
    q_cell = geohash(lat, lng, 7) if lat is not None and lng is not None else ""

    scored: list[tuple[float, str]] = []
    now = time.time()
    for aid, meta in idx["atoms"].items():
        scope = meta.get("scope") or "city"
        prec = _SCOPE_PRECISION.get(scope, 5)
        tag_s = _jaccard(q_tags, {slug(t) for t in (meta.get("tags") or [])})
        ent_s = _jaccard(q_ents, {slug(e) for e in (meta.get("entities") or [])})
        geo = 0.0
        if prec == 0:
            # 시기/전역 원자는 지리 조건이 없는 대신 내용 접점이 있어야만 후보가 된다.
            # 무조건 0.6 을 주면 전역 원자 수백 개가 모든 장소의 후보가 되어, 추크(스위스)
            # 보고서에 이탈리아·폴란드 원자가 '이미 확립된 사실'로 실렸다(260829 실측).
            if ent_s > 0 or tag_s >= 0.2:
                geo = 0.6
        elif q_cell and meta.get("cell"):
            if q_cell[:prec] == str(meta["cell"])[:prec]:
                geo = 1.0
            elif meta.get("lat") is not None and meta.get("radius_km"):
                d = haversine_km(lat, lng, meta.get("lat"), meta.get("lng"))
                if d <= float(meta.get("radius_km") or 0):
                    geo = 0.8
        # 지리 접점도 엔티티 접점도 없는 원자는 태그만으로 들어오지 못한다 —
        # #road-signage 같은 일반 태그가 지구 반대편 원자를 끌어오는 것을 막는다.
        if geo <= 0 and ent_s <= 0:
            continue
        age_days = max(0.0, (now - float(meta.get("updated") or now)) / 86400.0)
        score = (
            3.0 * geo
            + 2.5 * ent_s
            + 1.5 * tag_s
            + 0.4 * math.log1p(float(meta.get("uses") or 1))
            - 0.01 * min(age_days, 365)
        )
        scored.append((score, aid))

    scored.sort(reverse=True)
    out: list[Atom] = []
    used = 0
    for score, aid in scored:
        if len(out) >= limit or used >= char_budget:
            break
        a = st.load(aid)
        if not a:
            continue
        used += len(a.body) + len(a.title)
        out.append(a)
    return out


def as_prompt_block(atoms: list[Atom]) -> str:
    """회상된 원자를 리서치 프롬프트에 넣을 텍스트로."""
    if not atoms:
        return ""
    lines = []
    for a in atoms:
        period = ""
        if a.period_start is not None or a.period_end is not None:
            period = f" [{a.period_start or '?'}–{a.period_end or '?'}]"
        tags = " ".join(f"#{t}" for t in a.tags[:8])
        lines.append(f"- [[{a.id}]] ({a.layer}/{a.scope}){period} **{a.title}** — {a.body} {tags}")
    return "\n".join(lines)


# ── 적재(ingest) — 세 갈래(P1 지점 단서·P2 장소 사실·P3 프로파일 사실) ────
# docs/plan/atom-density-map-detail_260829.html §density. 한 번의 "요약" 호출이던 것을
# 재료가 다른 세 추출 패스로 가른다 — P2 만 잡 예산 안에서 동기 호출되고, P1·P3 는 캡처
# 좌표·리서치 프로파일이 실제로 있을 때만 뭔가 낳으며 예산 밖(사후 적재)에서 돈다.

# cue 범주 → layer. cue 는 "신설 레이어"가 아니라 지점 원자에만 붙는 범주라 layer 는
# 모델이 아니라 이 표가 정한다(둘 다 닫힌 어휘이므로 조합도 고정이다).
_CUE_LAYER: dict[str, str] = {
    "road-signage": "architecture", "road-marking": "architecture",
    "bollard-guardrail": "architecture", "utility-pole": "architecture",
    "street-furniture": "architecture", "camera-coverage": "architecture",
    "soil-terrain-cue": "geography", "vegetation-cue": "nature",
    "license-plate": "economy", "vehicle-fleet": "economy", "business-chain": "economy",
    "flag-emblem": "culture",
}

_PROFILE_SECTIONS = ("economy", "industry", "culture", "tourism",
                     "notable_people", "neighbor_comparison", "residents")


def _touch(a: Atom, report_file: str, tags: list[str], sources: list[str]) -> None:
    a.uses += 1
    if report_file and report_file not in a.reports:
        a.reports.append(report_file)
        a.reports = a.reports[-40:]
    if tags:
        a.tags = sorted(set(a.tags) | set(tags))[:16]
    if sources:
        a.sources = sorted(set(a.sources) | set(sources))[:16]


def _find_near_duplicate(
    st: Store, layer: str, ents: list[str], tags: list[str], scope: str, category: str | None = None,
) -> Atom | None:
    """같은 레이어 + 엔티티 겹침 + 태그 자카드 ≥ 0.6 이면 같은 사실로 본다.

    category 를 주면(§taxonomy 스키마 변경) 같은 범주일 때 더 느슨한 문턱도 인정한다 —
    범주가 이미 강한 신호이므로 태그·엔티티가 옅어도 같은 사실일 확률이 높다.
    """
    idx = st.index()
    cand: set[str] = set()
    for e in ents:
        cand.update(idx.get("entities", {}).get(e, []))
    if not cand:
        for t in tags:
            cand.update(idx.get("tags", {}).get(t, []))
    if not cand and category and category != CATEGORY_OTHER:
        cand.update(idx.get("categories", {}).get(category, []))
    best: tuple[float, str] | None = None
    for aid in cand:
        meta = idx["atoms"].get(aid) or {}
        if meta.get("layer") != layer:
            continue
        j = _jaccard(set(tags), {slug(t) for t in (meta.get("tags") or [])})
        e = _jaccard(set(ents), {slug(x) for x in (meta.get("entities") or [])})
        same_cat = bool(category) and category != CATEGORY_OTHER and meta.get("category") == category
        if j >= 0.6 or (e >= 0.5 and j >= 0.4) or (same_cat and (j >= 0.4 or e >= 0.4)):
            score = j + e + (0.1 if same_cat else 0.0)
            if best is None or score > best[0]:
                best = (score, aid)
    return st.load(best[1]) if best else None


def _find_near_duplicate_point(st: Store, category: str, cell: str, title: str) -> Atom | None:
    """point 원자 전용 — 같은 5자 셀(≈5km) + 같은 범주 + 제목 유사도 ≥ 0.8 이면 같은 사실.

    P1 지점 단서는 태그·엔티티가 옅어(대개 "이 표지판은 흰 바탕 검정 세리프" 류) 일반
    dedup 이 잘 안 걸린다. 캡처마다 원자가 늘어나므로(§density) 이 경로가 없으면 같은
    교차로를 여러 번 찍었을 때 사실상 같은 원자가 계속 새로 생긴다.
    """
    if not cell or not category:
        return None
    idx = st.index()
    cell5 = cell[:5]
    best: tuple[float, str] | None = None
    for aid in idx.get("categories", {}).get(category, []):
        meta = idx["atoms"].get(aid) or {}
        if meta.get("scope") != "point" or str(meta.get("cell") or "")[:5] != cell5:
            continue
        sim = difflib.SequenceMatcher(None, _norm_text(title), _norm_text(meta.get("title") or "")).ratio()
        if sim >= 0.8 and (best is None or sim > best[0]):
            best = (sim, aid)
    return st.load(best[1]) if best else None


# ── P1 · 지점 단서 ─────────────────────────────────────────────────
_P1_TOOL = {
    "name": "cue_atoms",
    "description": "Turn raw per-image cues/landmarks into point-scope 'cue' knowledge atoms anchored to the exact capture each was observed in.",
    "input_schema": {
        "type": "object",
        "required": ["atoms"],
        "properties": {
            "atoms": {
                "type": "array",
                "description": (
                    "0-5 atoms per image. Only concrete, TRANSFERABLE visual/physical patterns "
                    "(a sign typeface+colour scheme, a guardrail profile, a plate shape/colour, a "
                    "utility-pole hardware style) — never a bare restatement of 'this looks like "
                    "<country>'. Skip an observation if it does not fit any category below."
                ),
                "items": {
                    "type": "object",
                    "required": ["image_index", "category", "title", "body", "tags"],
                    "properties": {
                        "image_index": {
                            "type": "integer",
                            "description": "Copy this straight from the source cue/landmark's image_index.",
                        },
                        "category": {"type": "string", "enum": list(CUE_CATEGORIES)},
                        "title": {"type": "string", "description": "Short noun phrase, <=80 chars."},
                        "body": {
                            "type": "string",
                            "description": "1-3 sentences. The specific pattern itself, not the conclusion it implies.",
                        },
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "lowercase-hyphenated, 2-8."},
                        "entities": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    },
}

_P1_SYSTEM = (
    "You are a knowledge-base curator for a geography research tool. You are given RAW per-image "
    "observations (cues/landmarks) that a vision model already made about GeoGuessr street-view "
    "captures. Your job is to restructure the reusable ones into 'cue' knowledge atoms — small, "
    "point-scope facts anchored to the exact capture they came from.\n\n"
    "RULES\n"
    "- Write in English. Do not invent anything beyond what the raw observation already states — "
    "you are restructuring, not re-analyzing the image.\n"
    "- Every atom needs `image_index` copied from its source observation, and a `category` from "
    "the CLOSED list. If nothing genuinely fits, drop that observation — do not force it.\n"
    "- Prefer the specific, transferable pattern over the conclusion: 'chevron-striped bollards, "
    "black-on-yellow, spaced ~15m' is a cue atom. 'This is rural France' is not — that is the "
    f"chain's job, not this one's.\n- category vocabulary: {', '.join(CUE_CATEGORIES)}."
)


def _ingest_p1(
    settings: Settings, *, analysis: dict | None, image_panos: dict | None, report_file: str = "",
) -> dict:
    """P1 — 캡처별 cues[]/landmarks[](image_index 있는 것) → cue 범주 point 원자.

    좌표는 그 캡처의 실측 pano 좌표에서만 온다(image_panos 에 없으면 그 관찰은 만들지
    않는다) — LLM 지오코딩 금지 원칙은 P1 에서도 그대로다.
    """
    if not settings.knowledge_enabled:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "disabled"}
    top = analysis if isinstance(analysis, dict) else {}
    a = top.get("analysis") if isinstance(top.get("analysis"), dict) else {}
    images = top.get("images") or []
    image_panos = image_panos or top.get("image_panos") or {}
    cues = [c for c in (a.get("cues") or []) if isinstance(c, dict) and c.get("image_index") is not None]
    marks = [m for m in (a.get("landmarks") or []) if isinstance(m, dict) and m.get("image_index") is not None]
    if not cues and not marks:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "no-per-image-observations"}
    if not image_panos:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "no-pano-coords"}

    material = (
        [{"kind": "cue", "image_index": c["image_index"], "category_hint": c.get("category"),
          "observation": c.get("observation"), "weight": c.get("weight")} for c in cues]
        + [{"kind": "landmark", "image_index": m["image_index"], "name": m.get("name"),
            "type": m.get("type")} for m in marks]
    )
    user = (
        f"IMAGES: {len(images)} captures, valid image_index range 0..{max(0, len(images) - 1)}.\n"
        f"RAW OBSERVATIONS:\n{json.dumps(material, ensure_ascii=False, default=str)[:16000]}\n\n"
        "Call cue_atoms once."
    )
    try:
        resp = llm.call(
            settings, system=_P1_SYSTEM, messages=[{"role": "user", "content": user}],
            tools=[_P1_TOOL], tool_choice={"type": "tool", "name": "cue_atoms"},
            max_tokens=4000, effort=settings.knowledge_effort,
            deadline_s=settings.knowledge_timeout_s, role="fact",
        )
    except llm.LLMUnavailable as exc:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "error": str(exc)}

    cost = llm.spend(resp)
    data = llm.tool_input(resp, "cue_atoms") or {}
    st = store_for(settings)
    created = merged = 0
    saved: list[Atom] = []
    per_image: dict[int, int] = {}

    for raw in data.get("atoms") or []:
        if not isinstance(raw, dict) or len(saved) >= settings.knowledge_p1_max_atoms:
            continue
        try:
            idx_i = int(raw.get("image_index"))
        except (TypeError, ValueError):
            continue
        if idx_i < 0 or idx_i >= len(images) or per_image.get(idx_i, 0) >= settings.knowledge_p1_max_per_image:
            continue
        body = (raw.get("body") or "").strip()
        title = (raw.get("title") or "").strip()
        category = raw.get("category") if raw.get("category") in CUE_CATEGORIES else None
        if not body or not title or not category:
            continue
        pano = image_panos.get(Path(images[idx_i]).name) or {}
        lat_i, lng_i = _as_float(pano.get("lat")), _as_float(pano.get("lng"))
        if lat_i is None or lng_i is None:
            continue

        layer = _CUE_LAYER.get(category, "architecture")
        tags = sorted({slug(t) for t in (raw.get("tags") or []) if t} | {category})[:10]
        ents = sorted({slug(e) for e in (raw.get("entities") or []) if e})[:6]
        cell = geohash(lat_i, lng_i, 7)

        aid = atom_id(layer, ents, body)
        existing = (st.load(aid) or _find_near_duplicate_point(st, category, cell, title)
                    or _find_near_duplicate(st, layer, ents, tags, "point", category))
        per_image[idx_i] = per_image.get(idx_i, 0) + 1
        if existing is not None:
            _touch(existing, report_file, tags, [])
            st.save(existing)
            saved.append(existing)
            merged += 1
            continue

        atom = Atom(
            id=aid, layer=layer, scope="point", title=title[:120], body=body,
            category=category, tags=tags, entities=ents,
            lat=lat_i, lng=lng_i, heading=_as_float(pano.get("heading")),
            radius_km=_SCOPE_RADIUS_KM.get("point", 1), cell=cell, lang="en",
            # 캡처 썸네일 경로 — 로컬 전용(배포본엔 captures/ 가 없다). sources 는 원래
            # 출처 URL 을 담는 자리인데, P1 원자의 출처는 정확히 이 캡처 이미지 한 장이다.
            sources=[f"/captures/{Path(images[idx_i]).name}"],
            reports=[report_file] if report_file else [],
        )
        st.save(atom)
        saved.append(atom)
        created += 1

    return {"atoms": saved, "created": created, "merged": merged, "cost_usd": round(cost, 4)}


# ── P2 · 장소 사실 ─────────────────────────────────────────────────
_P2_TOOL = {
    "name": "knowledge_atoms",
    "description": "Break the report's narrowing chain and cultural/economic read into reusable, de-duplicated knowledge atoms.",
    "input_schema": {
        "type": "object",
        "required": ["atoms"],
        "properties": {
            "atoms": {
                "type": "array",
                "description": (
                    "Reusable facts. Prefer GENERALIZABLE facts (a polity, a period, an "
                    "architectural style, a trade network) over one-off trivia — those are what "
                    "future reports elsewhere will be able to reuse. Roughly one per narrowing step."
                ),
                "items": {
                    "type": "object",
                    "required": ["layer", "scope", "title", "body", "tags", "category"],
                    "properties": {
                        "layer": {"type": "string", "enum": list(LAYERS)},
                        "category": {
                            "type": "string", "enum": sorted(ALL_CATEGORIES),
                            "description": (
                                "Finer-grained than layer, from a CLOSED vocabulary. Use 'other' "
                                "only when nothing genuinely fits; never invent a new slug."
                            ),
                        },
                        "scope": {
                            "type": "string", "enum": list(SCOPES),
                            "description": (
                                "How widely this fact applies. 'city'/'region'/'country'=administrative, "
                                "'polity'=a historical state or cultural sphere (e.g. the Carolingian "
                                "Empire), 'period'=time-bound and not geographically constrained."
                            ),
                        },
                        "title": {"type": "string", "description": "Short noun phrase, <=80 chars."},
                        "body": {
                            "type": "string",
                            "description": "2-4 sentences. Dense, factual, self-contained. No hedging.",
                        },
                        "tags": {
                            "type": "array", "items": {"type": "string"},
                            "description": "lowercase-hyphenated hashtags, 3-10 of them.",
                        },
                        "entities": {
                            "type": "array", "items": {"type": "string"},
                            "description": (
                                "Canonical lowercase-hyphenated slugs for the people, polities, "
                                "styles or institutions this is about (e.g. charlemagne, "
                                "carolingian-empire, romanesque-architecture)."
                            ),
                        },
                        "period_start": {"type": ["integer", "null"], "description": "Year; negative = BCE."},
                        "period_end": {"type": ["integer", "null"]},
                        "radius_km": {"type": ["number", "null"], "description": "Applicability radius."},
                        "restates": {
                            "type": ["string", "null"],
                            "description": (
                                "If this merely restates a KNOWN atom listed in the prompt, put its "
                                "atm_ id here and keep body short — it will be linked, not duplicated."
                            ),
                        },
                    },
                },
            },
            "reused_atom_ids": {
                "type": "array", "items": {"type": "string"},
                "description": "atm_ ids from the KNOWN list that this report actually relied on.",
            },
        },
    },
}

_P2_SYSTEM = (
    "You are a knowledge-base curator for a geography research tool. You turn a finished "
    "location report's narrowing chain into small, reusable, non-redundant knowledge atoms. "
    "(Point-scope visual cues and the research profile are handled by separate passes — do not "
    "duplicate raw sign/pole/plate observations here.)\n\n"
    "RULES\n"
    "- Write atoms in English regardless of the report's language — this is the canonical store.\n"
    "- An atom must be reusable by a FUTURE report about a DIFFERENT place. Prefer facts about "
    "polities, periods, styles, trade networks, geology, language families. Skip anything that is "
    "only true of this one street.\n"
    "- If a fact is already in the KNOWN list, do NOT restate it: set `restates` to that id.\n"
    "- Choose `scope` honestly: an atom about the Carolingian Empire is scope=polity, not city. "
    "This is what lets a report in Aachen reuse an atom created in Reims.\n"
    "- Tags and entities are the retrieval keys. Use stable, canonical slugs so that the same "
    "concept always produces the same slug.\n"
    "- Never invent facts to fill the schema. Fewer, solid atoms beat many speculative ones.\n"
    "- The report's `narrowing` chain is your richest source. A step's `discriminator` ('Hokuriku "
    "snow-country roofs carry deeper eaves and steeper pitch than Kanto') is exactly the kind of "
    "transferable, reusable fact this store exists for — turn those into atoms. Do NOT turn "
    "'the signs are in Japanese' into an atom; that is not reusable knowledge."
)


def _ingest_p2(
    settings: Settings, *, a: dict, place_label: str, lat=None, lng=None,
    report_file: str = "", known: list[Atom] | None = None,
) -> dict:
    """P2 — narrowing 체인 + cultural_economic_read → 지금까지의 원자와 같은 것."""
    if not settings.knowledge_enabled:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "disabled"}
    if not a:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "no-content"}
    known = known or []

    payload = {
        "place": place_label or "unknown",
        "coordinates": {"lat": lat, "lng": lng},
        "best_guess": a.get("best_guess"),
        "coarse_filters": a.get("coarse_filters"),
        # 판별 사슬은 원자로 만들기에 가장 좋은 재료다 — "호쿠리쿠 설국 지붕은 처마가 깊다"
        # 같은 판별자는 다른 지역 보고서에서 그대로 재사용된다.
        "narrowing": a.get("narrowing"),
        "cultural_economic_read": a.get("cultural_economic_read"),
    }
    known_block = as_prompt_block(known)
    user = (
        f"REPORT (place: {place_label or 'unknown'})\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)[:20000]}\n\n"
        + (f"KNOWN ATOMS (already in the store — link, do not restate):\n{known_block}\n\n"
           if known_block else "")
        + "Call knowledge_atoms once."
    )

    try:
        resp = llm.call(
            settings, system=_P2_SYSTEM, messages=[{"role": "user", "content": user}],
            tools=[_P2_TOOL], tool_choice={"type": "tool", "name": "knowledge_atoms"},
            max_tokens=settings.knowledge_max_tokens, effort=settings.knowledge_effort,
            deadline_s=settings.knowledge_timeout_s, role="fact",
        )
    except llm.LLMUnavailable as exc:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 적재 실패가 보고서를 막으면 안 된다
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "error": str(exc)}

    cost = llm.spend(resp)
    data = llm.tool_input(resp, "knowledge_atoms") or {}
    st = store_for(settings)
    created = merged = 0
    saved: list[Atom] = []
    known_by_id = {k.id: k for k in known}

    for raw in (data.get("atoms") or [])[: settings.knowledge_max_atoms]:
        if not isinstance(raw, dict):
            continue
        body = (raw.get("body") or "").strip()
        title = (raw.get("title") or "").strip()
        if not body or not title:
            continue
        layer = raw.get("layer") if raw.get("layer") in LAYERS else "culture"
        category = raw.get("category") if raw.get("category") in ALL_CATEGORIES else CATEGORY_OTHER
        scope = raw.get("scope") if raw.get("scope") in SCOPES else "city"
        tags = sorted({slug(t) for t in (raw.get("tags") or []) if t})[:12]
        ents = sorted({slug(e) for e in (raw.get("entities") or []) if e})[:8]

        # 모델이 "기존 원자의 재진술"이라고 표시했으면 새로 만들지 않고 링크만 강화한다.
        restates = (raw.get("restates") or "").strip()
        if restates and (restates in known_by_id or st.load(restates)):
            existing = st.load(restates) or known_by_id[restates]
            _touch(existing, report_file, tags, [])
            st.save(existing)
            saved.append(existing)
            merged += 1
            continue

        aid = atom_id(layer, ents, body)
        existing = st.load(aid) or _find_near_duplicate(st, layer, ents, tags, scope, category)
        if existing is not None:
            _touch(existing, report_file, tags, [])
            if len(body) > len(existing.body) * 1.4:      # 더 풍부한 서술이면 승격
                existing.body = body
            st.save(existing)
            saved.append(existing)
            merged += 1
            continue

        radius = raw.get("radius_km")
        atom = Atom(
            id=aid, layer=layer, scope=scope, title=title[:120], body=body,
            category=category, tags=tags, entities=ents,
            period_start=_as_int(raw.get("period_start")), period_end=_as_int(raw.get("period_end")),
            lat=_as_float(lat), lng=_as_float(lng),
            radius_km=float(radius) if isinstance(radius, (int, float)) else _SCOPE_RADIUS_KM.get(scope, 25),
            cell=geohash(lat, lng, 7) if lat is not None and lng is not None else "",
            lang="en", reports=[report_file] if report_file else [],
        )
        st.save(atom)
        saved.append(atom)
        created += 1

    # 명시적으로 재사용됐다고 보고된 원자는 사용 횟수를 올린다(회상 점수에 반영).
    for rid in (data.get("reused_atom_ids") or []):
        ex = st.load(str(rid))
        if ex:
            _touch(ex, report_file, [], [])
            st.save(ex)

    return {"atoms": saved, "created": created, "merged": merged, "cost_usd": round(cost, 4)}


# ── P3 · 프로파일 사실 ─────────────────────────────────────────────
_P3_TOOL = {
    "name": "profile_atoms",
    "description": "Turn a researched place profile into reusable knowledge atoms, grouped by section.",
    "input_schema": {
        "type": "object",
        "required": ["atoms"],
        "properties": {
            "atoms": {
                "type": "array",
                "description": (
                    "2-4 atoms per section (economy, industry, culture, tourism, notable_people, "
                    "neighbor_comparison, residents). Skip a section with nothing concrete/reusable."
                ),
                "items": {
                    "type": "object",
                    "required": ["section", "layer", "category", "scope", "title", "body", "tags"],
                    "properties": {
                        "section": {"type": "string", "enum": list(_PROFILE_SECTIONS)},
                        "layer": {"type": "string", "enum": list(LAYERS)},
                        "category": {"type": "string", "enum": sorted(ALL_CATEGORIES)},
                        "scope": {"type": "string", "enum": ["city", "region", "country"]},
                        "title": {"type": "string", "description": "Short noun phrase, <=80 chars."},
                        "body": {"type": "string", "description": "2-4 sentences. Dense, factual, self-contained."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "lowercase-hyphenated, 3-10."},
                        "entities": {
                            "type": "array", "items": {"type": "string"},
                            "description": (
                                "REQUIRED (non-empty) for section=notable_people, and whenever the "
                                "atom names a specific company/institution — this is what turns it "
                                "into an atlas actor."
                            ),
                        },
                        "restates": {"type": ["string", "null"]},
                    },
                },
            },
        },
    },
}

_P3_SYSTEM = (
    "You are a knowledge-base curator for a geography research tool. You are given a researched "
    "place PROFILE (already web-searched) split into sections. Turn each section into small, "
    "reusable, non-redundant knowledge atoms.\n\n"
    "RULES\n"
    "- Write in English. Use ONLY what the profile states — this is already-researched material, "
    "not a place for new speculation.\n"
    "- A named person (notable_people) or a named company/institution MUST get an `entities` slug "
    "— that is what lets it become an actor in the wider atlas. A vague sentence with no name in "
    "it should usually be skipped rather than forced into an atom.\n"
    "- If a fact is already in the KNOWN list, do NOT restate it: set `restates` to that id.\n"
    "- scope is city/region/country only here — never point/polity/period.\n"
    "- Never invent facts to fill the schema. Fewer, solid atoms beat many speculative ones."
)


def _ingest_p3(
    settings: Settings, *, research: dict | None, lat=None, lng=None,
    report_file: str = "", known: list[Atom] | None = None,
) -> dict:
    """P3 — 리서치 프로파일 절(경제·산업·문화·관광·인물·인접비교·주민)마다 2~4개."""
    if not settings.knowledge_enabled:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "disabled"}
    profile = (research or {}).get("profile") if isinstance(research, dict) else None
    sections = {k: (profile or {}).get(k) for k in _PROFILE_SECTIONS}
    if not profile or not any(sections.values()):
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "no-profile"}
    known = known or []

    known_block = as_prompt_block(known)
    user = (
        f"PROFILE SECTIONS:\n{json.dumps(sections, ensure_ascii=False, default=str)[:14000]}\n\n"
        + (f"KNOWN ATOMS (already in the store — link, do not restate):\n{known_block}\n\n"
           if known_block else "")
        + "Call profile_atoms once."
    )
    try:
        resp = llm.call(
            settings, system=_P3_SYSTEM, messages=[{"role": "user", "content": user}],
            tools=[_P3_TOOL], tool_choice={"type": "tool", "name": "profile_atoms"},
            max_tokens=6000, effort=settings.knowledge_effort,
            deadline_s=settings.knowledge_timeout_s, role="fact",
        )
    except llm.LLMUnavailable as exc:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "error": str(exc)}

    cost = llm.spend(resp)
    data = llm.tool_input(resp, "profile_atoms") or {}
    sources = [s.get("url") for s in ((research or {}).get("sources") or []) if isinstance(s, dict)]
    sources = [u for u in sources if u][:10]

    st = store_for(settings)
    created = merged = 0
    saved: list[Atom] = []
    known_by_id = {k.id: k for k in known}
    per_section: dict[str, int] = {}

    for raw in data.get("atoms") or []:
        if not isinstance(raw, dict) or len(saved) >= settings.knowledge_p3_max_atoms:
            continue
        section = raw.get("section") if raw.get("section") in _PROFILE_SECTIONS else "other"
        if per_section.get(section, 0) >= settings.knowledge_p3_max_per_section:
            continue
        body = (raw.get("body") or "").strip()
        title = (raw.get("title") or "").strip()
        if not body or not title:
            continue
        layer = raw.get("layer") if raw.get("layer") in LAYERS else "economy"
        category = raw.get("category") if raw.get("category") in ALL_CATEGORIES else CATEGORY_OTHER
        scope = raw.get("scope") if raw.get("scope") in ("city", "region", "country") else "city"
        tags = sorted({slug(t) for t in (raw.get("tags") or []) if t} | {"profile"})[:12]
        ents = sorted({slug(e) for e in (raw.get("entities") or []) if e})[:8]
        per_section[section] = per_section.get(section, 0) + 1

        restates = (raw.get("restates") or "").strip()
        if restates and (restates in known_by_id or st.load(restates)):
            existing = st.load(restates) or known_by_id[restates]
            _touch(existing, report_file, tags, sources)
            st.save(existing)
            saved.append(existing)
            merged += 1
            continue

        aid = atom_id(layer, ents, body)
        existing = st.load(aid) or _find_near_duplicate(st, layer, ents, tags, scope, category)
        if existing is not None:
            _touch(existing, report_file, tags, sources)
            if len(body) > len(existing.body) * 1.4:
                existing.body = body
            st.save(existing)
            saved.append(existing)
            merged += 1
            continue

        atom = Atom(
            id=aid, layer=layer, scope=scope, title=title[:120], body=body,
            category=category, tags=tags, entities=ents,
            lat=_as_float(lat), lng=_as_float(lng), radius_km=_SCOPE_RADIUS_KM.get(scope, 25),
            cell=geohash(lat, lng, 7) if lat is not None and lng is not None else "",
            lang="en", sources=sources, reports=[report_file] if report_file else [],
        )
        st.save(atom)
        saved.append(atom)
        created += 1

    return {"atoms": saved, "created": created, "merged": merged, "cost_usd": round(cost, 4)}


# ── 공개 API ─────────────────────────────────────────────────────
def ingest(
    settings: Settings,
    *,
    analysis: dict | None,
    research: dict | None,
    lat=None,
    lng=None,
    report_file: str = "",
    known: list[Atom] | None = None,
    image_panos: dict | None = None,
    passes: tuple[str, ...] = ("p1", "p2", "p3"),
) -> dict:
    """보고서 결과 → 원자 추출 → 중복제거 → 저장. P1(지점 단서)·P2(장소 사실)·P3(프로파일)
    로 나뉜다 — `passes` 로 고를 수 있다. 재료가 없는 패스는 실패가 아니라 조용히 0개로
    끝난다(skipped 로 표시). 반환 {atoms, created, merged, cost_usd, passes: {p1:{...}, ...}}.
    """
    if not settings.knowledge_enabled:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "disabled"}

    top = analysis if isinstance(analysis, dict) else {}
    a = top.get("analysis") if isinstance(top.get("analysis"), dict) else (top or {})
    g = (a or {}).get("best_guess") or {}
    place_label = ", ".join(x for x in [g.get("city"), g.get("region_or_state"), g.get("country")] if x)
    known = known or []

    pass_results: dict[str, dict] = {}
    all_saved: list[Atom] = []
    total_cost = 0.0
    created = merged = 0

    if "p2" in passes:
        r = _ingest_p2(settings, a=a, place_label=place_label, lat=lat, lng=lng,
                       report_file=report_file, known=known)
        pass_results["p2"] = {k: v for k, v in r.items() if k != "atoms"} | {"n": len(r["atoms"])}
        all_saved += r["atoms"]; total_cost += r["cost_usd"]; created += r["created"]; merged += r["merged"]
    if "p1" in passes:
        r = _ingest_p1(settings, analysis=analysis, image_panos=image_panos, report_file=report_file)
        pass_results["p1"] = {k: v for k, v in r.items() if k != "atoms"} | {"n": len(r["atoms"])}
        all_saved += r["atoms"]; total_cost += r["cost_usd"]; created += r["created"]; merged += r["merged"]
    if "p3" in passes:
        r = _ingest_p3(settings, research=research, lat=lat, lng=lng,
                       report_file=report_file, known=known)
        pass_results["p3"] = {k: v for k, v in r.items() if k != "atoms"} | {"n": len(r["atoms"])}
        all_saved += r["atoms"]; total_cost += r["cost_usd"]; created += r["created"]; merged += r["merged"]

    if all_saved:
        st = store_for(settings)
        _write_place_note(st, lat, lng, place_label, all_saved, report_file)
        for e in {e for a2 in all_saved for e in a2.entities}:
            _write_entity_note(st, e)

    seen: set[str] = set()
    atom_ids = [a2.id for a2 in all_saved if not (a2.id in seen or seen.add(a2.id))]

    return {
        "atoms": atom_ids, "created": created, "merged": merged,
        "cost_usd": round(total_cost, 4), "passes": pass_results,
    }


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 노트 파일 ────────────────────────────────────────────────────
def _write_place_note(st: Store, lat, lng, label: str, atoms: list[Atom], report_file: str) -> None:
    if lat is None or lng is None:
        return
    cell = geohash(lat, lng, 5)
    if not cell:
        return
    st.places_dir.mkdir(parents=True, exist_ok=True)
    path = st.places_dir / f"{cell}.md"
    prev = path.read_text(encoding="utf-8") if path.exists() else ""
    seen = set(re.findall(r"atm_[0-9a-f]{12}", prev))
    new_lines = [f"- [[{a.id}]] **{a.title}** ({a.layer}/{a.scope})" for a in atoms if a.id not in seen]
    if not prev:
        prev = (
            f"# 장소 노트 · `{cell}`\n\n"
            f"**대표 지명**: {label or '미상'}\n"
            f"**좌표**: {round(float(lat), 5)}, {round(float(lng), 5)}\n\n"
            f"## 원자\n"
        )
    body = prev
    if new_lines:
        body = body.rstrip() + "\n" + "\n".join(new_lines) + "\n"
    if report_file and f"`{report_file}`" not in body:
        if "## 보고서" not in body:
            body = body.rstrip() + "\n\n## 보고서\n"
        body = body.rstrip() + f"\n- `{report_file}`\n"
    path.write_text(body, encoding="utf-8")


def _write_entity_note(st: Store, entity: str) -> None:
    idx = st.index()
    ids = idx.get("entities", {}).get(entity, [])
    if not ids:
        return
    st.entities_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    reports: set[str] = set()
    for aid in ids:
        meta = idx["atoms"].get(aid) or {}
        lines.append(f"- [[{aid}]] **{meta.get('title','')}** ({meta.get('layer')}/{meta.get('scope')})")
        reports.update(meta.get("reports") or [])
    body = (
        f"# 엔티티 · {entity}\n\n"
        f"원자 {len(ids)}개 · 이 엔티티를 다룬 보고서 {len(reports)}건\n\n"
        f"## 원자\n" + "\n".join(sorted(lines)) + "\n\n"
        f"## 보고서\n" + "\n".join(f"- `{r}`" for r in sorted(reports)) + "\n"
    )
    (st.entities_dir / f"{entity}.md").write_text(body, encoding="utf-8")


def write_report_note(
    settings: Settings, *, report_file: str, place: str, lat, lng, atoms: list[str],
    langs: list[str], summary: str = "", passes: list[str] | None = None,
) -> None:
    """보고서 1건의 요약 노트 — 원본 HTML 대신 이걸 읽으면 되도록.

    P1·P2·P3 가 서로 다른 시점(P2 는 즉시, P1·P3 는 사후)에 각자 호출할 수 있으므로,
    atoms·passes 는 **누적**한다 — 나중 패스가 앞 패스의 기록을 덮어쓰지 않는다.
    """
    if not settings.knowledge_enabled:
        return
    st = store_for(settings)
    st.reports_dir.mkdir(parents=True, exist_ok=True)
    name = Path(report_file).stem or "report"
    cell = geohash(lat, lng, 5) if lat is not None and lng is not None else ""
    idx = st.index()
    prior = (idx.get("reports") or {}).get(report_file) or {}
    all_atoms = sorted(set(prior.get("atoms") or []) | set(atoms))
    # 패스 구분 이전(260830 이전) 노트는 "passes" 필드가 아예 없다 — 그래도 노트가 있었다는
    # 것 자체가 옛 단일 패스(=지금의 p2)를 이미 마쳤다는 뜻이다. 이 변환 없이 그냥 합치면
    # p1·p3 만 사후 적재했을 때 p2 가 다시 결손으로 보인다.
    prior_passes = prior.get("passes")
    if prior_passes is None and prior:
        prior_passes = ["p2"]
    all_passes = sorted(set(prior_passes or []) | set(passes or []))
    place = place or prior.get("place") or ""
    body = (
        f"---\n"
        + json.dumps(
            {"report": report_file, "place": place, "lat": lat, "lng": lng, "cell": cell,
             "langs": langs, "atoms": all_atoms, "passes": all_passes, "created": time.time()},
            ensure_ascii=False, indent=2,
        )
        + f"\n---\n\n# {place or '미상'}\n\n"
        f"보고서: `{report_file}` · 언어 {', '.join(langs)} · 적재 {'/'.join(all_passes) or '-'}\n"
        + (f"장소 노트: [[{cell}]]\n" if cell else "")
        + (f"\n{summary}\n" if summary else "")
        + "\n## 이 보고서가 쓴 원자\n"
        + "\n".join(f"- [[{a}]]" for a in all_atoms)
        + "\n"
    )
    (st.reports_dir / f"{name}.md").write_text(body, encoding="utf-8")
    idx.setdefault("reports", {})[report_file] = {
        "place": place, "lat": lat, "lng": lng, "cell": cell, "atoms": all_atoms,
        "langs": langs, "passes": all_passes,
    }
    st._save_index()


# ── 종합(synthesize) — A + B' + C' ───────────────────────────────
_SYNTH_TOOL = {
    "name": "synthesis",
    "description": "Write a synthesis across several previously-researched places.",
    "input_schema": {
        "type": "object",
        "required": ["title", "thesis", "sections"],
        "properties": {
            "title": {"type": "string"},
            "thesis": {"type": "string", "description": "2-4 sentences: what ties these places together."},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["heading", "body"],
                    "properties": {
                        "heading": {"type": "string"},
                        "body": {"type": "string", "description": "Prose. Cite atoms inline as [[atm_...]]."},
                        "atom_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "connections": {
                "type": "array",
                "description": "Concrete cross-place links this synthesis establishes.",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "atom_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def synthesize(settings: Settings, *, selector: str, lang: str = "en") -> dict:
    """엔티티 슬러그 또는 태그로 원자를 모아 종합 보고서 재료를 만든다.

    새 웹 검색을 하지 않는다 — 이미 저장된 A(광역 사실)와 각 장소의 B'·C' 만 재조립한다.
    반환 {status, synthesis, atoms, places, cost_usd}.
    """
    st = store_for(settings)
    idx = st.index()
    key = slug(selector)
    seed_ids = list(idx.get("entities", {}).get(key) or idx.get("tags", {}).get(key) or [])
    if not seed_ids:
        return {"status": "NO_MATCH", "message": f"'{selector}' 에 해당하는 원자가 없습니다."}

    seed = [a for a in (st.load(i) for i in seed_ids) if a]
    if not seed:
        return {"status": "NO_MATCH", "message": f"'{selector}' 원자를 읽을 수 없습니다."}

    # A + B' + C' 확장 —
    # 주제 원자(A)만으로는 종합이 안 된다. A 를 참조한 보고서들을 찾고, 그 보고서에 함께
    # 실린 다른 원자(B', C')까지 끌어와야 "같은 강역·같은 시기의 서로 다른 두 지역"이
    # 한 문서에서 엮인다. B'·C' 는 이미 조사돼 있으므로 새 검색은 일어나지 않는다.
    reports: dict[str, dict] = {}
    for a in seed:
        for r in a.reports:
            reports.setdefault(r, idx.get("reports", {}).get(r, {}))
    related_ids = set(seed_ids)
    if reports:
        want = set(reports)
        for aid, meta in idx.get("atoms", {}).items():
            if set(meta.get("reports") or []) & want:
                related_ids.add(aid)

    atoms = [a for a in (st.load(i) for i in sorted(related_ids)) if a]
    if len(atoms) < 2:
        return {"status": "TOO_FEW",
                "message": ("종합하려면 원자가 2개 이상 필요합니다. "
                            "이 주제를 다룬 보고서를 한 건 더 만들면 종합할 수 있습니다."),
                "atoms": [a.id for a in atoms]}
    # 확장 후 보고서 목록 갱신(B'·C' 가 가져온 보고서 포함)
    for a in atoms:
        for r in a.reports:
            reports.setdefault(r, idx.get("reports", {}).get(r, {}))

    seed_set = set(seed_ids)
    block = "\n".join(
        ("[TOPIC] " if a.id in seed_set else "[CONTEXT] ") + line
        for a, line in zip(atoms, as_prompt_block(atoms).split("\n"))
    )
    places = sorted({(v.get("place") or "") for v in reports.values() if v.get("place")})
    user = (
        f"TOPIC: {selector}\n"
        f"PLACES ALREADY RESEARCHED: {', '.join(places) or 'unknown'}\n"
        f"REPORTS: {', '.join(sorted(reports)) or 'none'}\n\n"
        f"KNOWLEDGE ATOMS (this is your ONLY evidence — do not invent beyond it).\n"
        f"[TOPIC] atoms are the subject; [CONTEXT] atoms come from the same reports and are what "
        f"you connect through the topic:\n{block}\n\n"
        "Write a synthesis that connects these places through the topic. Call synthesis once."
    )
    system = (
        "You are a comparative historian and geographer. You are given knowledge atoms that were "
        "extracted from separate location reports. Your job is to find and articulate what "
        "connects them — shared polity, shared period, shared style, shared trade network, shared "
        "geology — and to say plainly where the evidence is thin.\n"
        "- Use ONLY the supplied atoms as evidence. Cite them inline as [[atm_...]].\n"
        "- Do not repeat an atom's text verbatim; synthesize across them.\n"
        "- If the atoms do not actually support a connection, say so instead of manufacturing one."
    )

    try:
        resp = llm.call(
            settings,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[_SYNTH_TOOL],
            tool_choice={"type": "tool", "name": "synthesis"},
            max_tokens=settings.synthesis_max_tokens,
            effort=settings.knowledge_effort,
            deadline_s=settings.knowledge_timeout_s,
            role="reason",          # 무관해 보이는 것들의 연관 — 가장 어려운 일
        )
    except llm.LLMUnavailable as exc:
        return {"status": "NO_KEY", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "API_ERROR", "message": str(exc)}

    data = llm.tool_input(resp, "synthesis")
    if not data:
        return {"status": "NO_TOOL_USE", "message": "종합 결과 구조화 실패."}
    return {
        "status": "OK",
        "synthesis": data,
        "atoms": [a.id for a in atoms],
        "seedAtoms": sorted(seed_set),
        "selector": selector,
        "places": places,
        "reports": sorted(reports),
        "cost_usd": round(llm.spend(resp), 4),
        "lang": lang,
    }
