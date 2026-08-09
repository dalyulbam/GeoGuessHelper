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
    tags: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    period_start: int | None = None
    period_end: int | None = None
    lat: float | None = None
    lng: float | None = None
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
            _, fm, _ = text.split("---", 2)
            data = json.loads(fm)
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
                    return self._index
            except Exception:  # noqa: BLE001
                pass
        self._index = self.rebuild()
        return self._index

    def rebuild(self) -> dict:
        idx: dict[str, Any] = {"atoms": {}, "cells": {}, "entities": {}, "tags": {}, "reports": {}}
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
        geo = 0.0
        if prec == 0:
            geo = 0.6                                   # 시기/전역 원자는 지리 조건 없음
        elif q_cell and meta.get("cell"):
            if q_cell[:prec] == str(meta["cell"])[:prec]:
                geo = 1.0
            elif meta.get("lat") is not None and meta.get("radius_km"):
                d = haversine_km(lat, lng, meta.get("lat"), meta.get("lng"))
                if d <= float(meta.get("radius_km") or 0):
                    geo = 0.8
        tag_s = _jaccard(q_tags, {slug(t) for t in (meta.get("tags") or [])})
        ent_s = _jaccard(q_ents, {slug(e) for e in (meta.get("entities") or [])})
        if geo <= 0 and tag_s <= 0 and ent_s <= 0:
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


# ── 적재(ingest) — 조사 후 원자를 추출·중복제거해 저장 ────────────
_EXTRACT_TOOL = {
    "name": "knowledge_atoms",
    "description": "Break the report down into reusable, de-duplicated knowledge atoms.",
    "input_schema": {
        "type": "object",
        "required": ["atoms"],
        "properties": {
            "atoms": {
                "type": "array",
                "description": (
                    "Reusable facts. Prefer GENERALIZABLE facts (a polity, a period, an "
                    "architectural style, a trade network) over one-off trivia — those are what "
                    "future reports elsewhere will be able to reuse. 3-8 atoms is typical."
                ),
                "items": {
                    "type": "object",
                    "required": ["layer", "scope", "title", "body", "tags"],
                    "properties": {
                        "layer": {"type": "string", "enum": list(LAYERS)},
                        "scope": {
                            "type": "string", "enum": list(SCOPES),
                            "description": (
                                "How widely this fact applies. 'point'=this exact spot, "
                                "'city'/'region'/'country'=administrative, 'polity'=a historical "
                                "state or cultural sphere (e.g. the Carolingian Empire), "
                                "'period'=time-bound and not geographically constrained."
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

_EXTRACT_SYSTEM = (
    "You are a knowledge-base curator for a geography research tool. You turn a finished "
    "location report into small, reusable, non-redundant knowledge atoms.\n\n"
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


def ingest(
    settings: Settings,
    *,
    analysis: dict | None,
    research: dict | None,
    lat=None,
    lng=None,
    report_file: str = "",
    known: list[Atom] | None = None,
) -> dict:
    """보고서 결과 → 원자 추출 → 중복제거 → 저장. 반환 {atoms, created, merged, cost_usd}."""
    if not settings.knowledge_enabled:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "disabled"}

    a = (analysis or {}).get("analysis") if isinstance(analysis, dict) else None
    a = a if isinstance(a, dict) else (analysis if isinstance(analysis, dict) else {})
    profile = (research or {}).get("profile") if isinstance(research, dict) else None
    if not a and not profile:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": "no-content"}

    g = (a or {}).get("best_guess") or {}
    place_label = ", ".join(x for x in [g.get("city"), g.get("region_or_state"), g.get("country")] if x)
    known = known or []

    payload = {
        "place": place_label or "unknown",
        "coordinates": {"lat": lat, "lng": lng},
        "analysis": {
            "best_guess": g,
            "coarse_filters": (a or {}).get("coarse_filters"),
            # 판별 사슬은 원자로 만들기에 가장 좋은 재료다 — "호쿠리쿠 설국 지붕은 처마가 깊다"
            # 같은 판별자는 다른 지역 보고서에서 그대로 재사용된다.
            "narrowing": (a or {}).get("narrowing"),
            "cues": (a or {}).get("cues"),
            "languages_detected": (a or {}).get("languages_detected"),
            "landmarks": (a or {}).get("landmarks"),
            "cultural_economic_read": (a or {}).get("cultural_economic_read"),
        },
        "research_profile": profile,
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
            settings,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=[_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "knowledge_atoms"},
            max_tokens=settings.knowledge_max_tokens,
            effort=settings.knowledge_effort,
            deadline_s=settings.knowledge_timeout_s,
            role="fact",            # 이미 확립된 사실의 압축·구조화
        )
    except llm.LLMUnavailable as exc:
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "skipped": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 적재 실패가 보고서를 막으면 안 된다
        return {"atoms": [], "created": 0, "merged": 0, "cost_usd": 0.0, "error": str(exc)}

    cost = llm.spend(resp)
    data = llm.tool_input(resp, "knowledge_atoms") or {}
    sources = [s.get("url") for s in ((research or {}).get("sources") or []) if isinstance(s, dict)]
    sources = [u for u in sources if u][:10]

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
        scope = raw.get("scope") if raw.get("scope") in SCOPES else "city"
        tags = sorted({slug(t) for t in (raw.get("tags") or []) if t})[:12]
        ents = sorted({slug(e) for e in (raw.get("entities") or []) if e})[:8]

        # 모델이 "기존 원자의 재진술"이라고 표시했으면 새로 만들지 않고 링크만 강화한다.
        restates = (raw.get("restates") or "").strip()
        if restates and (restates in known_by_id or st.load(restates)):
            existing = st.load(restates) or known_by_id[restates]
            _touch(existing, report_file, tags, sources)
            st.save(existing)
            saved.append(existing)
            merged += 1
            continue

        aid = atom_id(layer, ents, body)
        existing = st.load(aid)
        if existing is None:
            existing = _find_near_duplicate(st, layer, ents, tags, scope)
        if existing is not None:
            _touch(existing, report_file, tags, sources)
            if len(body) > len(existing.body) * 1.4:      # 더 풍부한 서술이면 승격
                existing.body = body
            st.save(existing)
            saved.append(existing)
            merged += 1
            continue

        radius = raw.get("radius_km")
        atom = Atom(
            id=aid,
            layer=layer,
            scope=scope,
            title=title[:120],
            body=body,
            tags=tags,
            entities=ents,
            period_start=_as_int(raw.get("period_start")),
            period_end=_as_int(raw.get("period_end")),
            lat=_as_float(lat),
            lng=_as_float(lng),
            radius_km=float(radius) if isinstance(radius, (int, float)) else _SCOPE_RADIUS_KM.get(scope, 25),
            cell=geohash(lat, lng, 7) if lat is not None and lng is not None else "",
            lang="en",
            sources=sources,
            reports=[report_file] if report_file else [],
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

    _write_place_note(st, lat, lng, place_label, saved, report_file)
    for e in {e for a2 in saved for e in a2.entities}:
        _write_entity_note(st, e)

    return {
        "atoms": [a2.id for a2 in saved],
        "created": created,
        "merged": merged,
        "cost_usd": round(cost, 4),
    }


def _touch(a: Atom, report_file: str, tags: list[str], sources: list[str]) -> None:
    a.uses += 1
    if report_file and report_file not in a.reports:
        a.reports.append(report_file)
        a.reports = a.reports[-40:]
    if tags:
        a.tags = sorted(set(a.tags) | set(tags))[:16]
    if sources:
        a.sources = sorted(set(a.sources) | set(sources))[:16]


def _find_near_duplicate(st: Store, layer: str, ents: list[str], tags: list[str], scope: str) -> Atom | None:
    """같은 레이어 + 엔티티 겹침 + 태그 자카드 ≥ 0.6 이면 같은 사실로 본다."""
    idx = st.index()
    cand: set[str] = set()
    for e in ents:
        cand.update(idx.get("entities", {}).get(e, []))
    if not cand:
        for t in tags:
            cand.update(idx.get("tags", {}).get(t, []))
    best: tuple[float, str] | None = None
    for aid in cand:
        meta = idx["atoms"].get(aid) or {}
        if meta.get("layer") != layer:
            continue
        j = _jaccard(set(tags), {slug(t) for t in (meta.get("tags") or [])})
        e = _jaccard(set(ents), {slug(x) for x in (meta.get("entities") or [])})
        if j >= 0.6 or (e >= 0.5 and j >= 0.4):
            score = j + e
            if best is None or score > best[0]:
                best = (score, aid)
    return st.load(best[1]) if best else None


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
    langs: list[str], summary: str = "",
) -> None:
    """보고서 1건의 요약 노트 — 원본 HTML 대신 이걸 읽으면 되도록."""
    if not settings.knowledge_enabled:
        return
    st = store_for(settings)
    st.reports_dir.mkdir(parents=True, exist_ok=True)
    name = Path(report_file).stem or "report"
    cell = geohash(lat, lng, 5) if lat is not None and lng is not None else ""
    body = (
        f"---\n"
        + json.dumps(
            {"report": report_file, "place": place, "lat": lat, "lng": lng, "cell": cell,
             "langs": langs, "atoms": atoms, "created": time.time()},
            ensure_ascii=False, indent=2,
        )
        + f"\n---\n\n# {place or '미상'}\n\n"
        f"보고서: `{report_file}` · 언어 {', '.join(langs)}\n"
        + (f"장소 노트: [[{cell}]]\n" if cell else "")
        + (f"\n{summary}\n" if summary else "")
        + "\n## 이 보고서가 쓴 원자\n"
        + "\n".join(f"- [[{a}]]" for a in atoms)
        + "\n"
    )
    (st.reports_dir / f"{name}.md").write_text(body, encoding="utf-8")
    idx = st.index()
    idx.setdefault("reports", {})[report_file] = {
        "place": place, "lat": lat, "lng": lng, "cell": cell, "atoms": atoms, "langs": langs,
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
