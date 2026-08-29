"""원자 전방위 확장(패스 D) — 저장된 원자를 씨앗으로 축을 바꿔 새 원자를 만든다.

문제: 저장소의 원자는 방문한 장소에서만 자란다. "체코슬로바키아 파넬라크 단지" 원자는
있지만, 그것이 속한 **전세계 패턴**(흐루쇼프카·플라텐바우·단지單地), 그 판을 움직인
**같은 업의 사람들**(건축가·기업가·정책가), 그리고 그 흐름의 **반대급부**(반작용·
대항 사례·치른 대가)는 없다. 방문 없이는 영영 안 생긴다.

여기서 하는 것: 씨앗 원자마다 세 축으로 확장 원자를 뽑는다.

    worldwide    같은 패턴의 전세계 병렬 사례 — 씨앗을 일반화하는 세계 원자
    people       같은 업종·분야의 실존 인물들 — 그 판을 만든 사람
    counterpart  반대급부 — 반작용·대항 사례·그 패턴이 치른 대가

원칙:
  · 재조사 없음 — 웹 검색을 하지 않는다. 모델이 이미 아는, 널리 문서화된 사실만.
    확신이 없는 축은 **비운다**(적은 확실한 원자 > 많은 추측 원자 — 적재 규칙과 동일).
  · 확장 원자는 refs=[씨앗 id] 로 계보를 남기고, 태그에 `expansion` + 축 태그가 붙는다.
    sources 는 비워 둔다 — 웹 출처가 아니라 모델 지식이라는 표시다.
  · 저장은 기존 Store 를 그대로 쓴다 — 내용 해시 주소·근접 중복 병합·인덱스가 동일하게
    작동하므로, 서로 다른 씨앗이 같은 세계 패턴을 제안하면 원자 하나에 refs 만 쌓인다.
  · 체크포인트(expansion.json)에 처리한 씨앗을 기록한다 — 중단해도 이어서 돈다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import llm
from .config import Settings
from .knowledge import (
    LAYERS,
    SCOPES,
    _SCOPE_RADIUS_KM,
    Atom,
    _find_near_duplicate,
    _write_entity_note,
    atom_id,
    geohash,
    slug,
    store_for,
)

AXES = ("worldwide", "people", "counterpart")
_AXIS_TAG = {"worldwide": "x-worldwide", "people": "x-people", "counterpart": "x-counterpart"}

_EXPAND_TOOL = {
    "name": "expansion_atoms",
    "description": "Expand seed knowledge atoms along fixed axes into new reusable atoms.",
    "input_schema": {
        "type": "object",
        "required": ["atoms"],
        "properties": {
            "atoms": {
                "type": "array",
                "description": (
                    "New atoms derived from the seeds. 0-3 per seed per axis. Skip an axis "
                    "entirely when you know nothing solid — fewer certain atoms beat many "
                    "speculative ones."
                ),
                "items": {
                    "type": "object",
                    "required": ["seed_id", "axis", "layer", "scope", "title", "body", "tags"],
                    "properties": {
                        "seed_id": {"type": "string", "description": "atm_ id of the seed this expands."},
                        "axis": {"type": "string", "enum": list(AXES)},
                        "layer": {"type": "string", "enum": list(LAYERS)},
                        "scope": {
                            "type": "string", "enum": list(SCOPES),
                            "description": (
                                "'global' for worldwide patterns; 'country'/'region' when the "
                                "new atom anchors somewhere specific; 'polity' for historical "
                                "states/spheres; 'period' for time-bound, geography-free facts."
                            ),
                        },
                        "title": {"type": "string", "description": "Short noun phrase, <=80 chars."},
                        "body": {
                            "type": "string",
                            "description": (
                                "2-4 sentences. Dense, factual, self-contained, verifiable "
                                "common knowledge. Name concrete instances/people/places. "
                                "No hedging, no speculation."
                            ),
                        },
                        "tags": {"type": "array", "items": {"type": "string"},
                                 "description": "lowercase-hyphenated, 3-10."},
                        "entities": {"type": "array", "items": {"type": "string"},
                                     "description": "Canonical lowercase-hyphenated slugs."},
                        "period_start": {"type": ["integer", "null"], "description": "Year; negative = BCE."},
                        "period_end": {"type": ["integer", "null"]},
                        "lat": {"type": ["number", "null"],
                                "description": "Anchor if the atom is about a specific place; null for global."},
                        "lng": {"type": ["number", "null"]},
                        "radius_km": {"type": ["number", "null"]},
                    },
                },
            },
        },
    },
}

_EXPAND_SYSTEM = (
    "You are a knowledge-base curator for a geography research tool. You are given SEED atoms — "
    "small factual notes tied to places already visited. Your job is to expand each seed along "
    "three axes into NEW reusable atoms:\n\n"
    "1. worldwide — the seed's pattern generalized: name the parallel instances of the same "
    "phenomenon across the world (e.g. a seed about Czechoslovak panelák estates expands to the "
    "global socialist prefab-housing family: Khrushchyovka, Plattenbau, Yugoslav blokovi, "
    "Japanese danchi, Korean apateu). One atom for the global family, and optionally 1-2 atoms "
    "for the strongest named instances elsewhere (with their own anchor coordinates).\n"
    "2. people — the real, notable people OF THAT SAME FIELD who shaped it: architects, "
    "industrialists, engineers, dynasts, policy-makers, artists. Only people whose role is "
    "widely documented. Anchor each person to their main locus of activity if clear.\n"
    "3. counterpart — 반대급부: the counter-force, reaction, cost, or opposing case of the "
    "seed's pattern. What pushed back, what it displaced, what it cost, or the pattern that "
    "developed in the opposite direction (e.g. mass tourism ↔ anti-tourism regulation; "
    "depopulation ↔ boomtown magnets; prefab uniformity ↔ heritage-preservation movements).\n\n"
    "RULES\n"
    "- Write in English — this is the canonical store.\n"
    "- ONLY well-documented, verifiable common knowledge. Never invent facts, people, dates or "
    "places. If unsure of a name or year, omit it rather than guess. Skipping an axis is fine.\n"
    "- Each atom must be reusable by a FUTURE report about a DIFFERENT place — prefer named "
    "patterns, styles, networks, movements, dynasties over one-off trivia.\n"
    "- Do NOT restate the seed itself; the expansion must add places, people or forces the seed "
    "does not contain.\n"
    "- Tags and entities are retrieval keys — stable canonical slugs.\n"
    "- scope honestly: a worldwide family is scope=global (lat/lng null); a named instance "
    "elsewhere gets its own country/region scope and approximate coordinates."
)


def _seed_block(atoms: list[Atom]) -> str:
    lines = []
    for a in atoms:
        period = ""
        if a.period_start is not None or a.period_end is not None:
            period = f" [{a.period_start or '?'}–{a.period_end or '?'}]"
        tags = " ".join(f"#{t}" for t in a.tags[:8])
        lines.append(
            f"SEED [[{a.id}]] ({a.layer}/{a.scope}){period} **{a.title}**\n"
            f"  {a.body}\n  {tags} · entities: {', '.join(a.entities) or '-'}"
        )
    return "\n\n".join(lines)


def _state_path(settings: Settings) -> Path:
    return Path(settings.knowledge_dir) / "expansion.json"


def _load_state(settings: Settings) -> dict:
    p = _state_path(settings)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "seeds_done" in d:
                return d
        except Exception:  # noqa: BLE001
            pass
    return {
        "meta": {"pass": "D", "note": "원자 전방위 확장 — worldwide/people/counterpart"},
        "seeds_done": [], "created": 0, "merged": 0, "skipped": 0, "cost_usd": 0.0,
    }


def _save_state(settings: Settings, state: dict) -> None:
    state["meta"]["updated"] = time.time()
    p = _state_path(settings)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _absorb(settings: Settings, batch_seeds: list[Atom], raw_atoms: list, state: dict) -> list[str]:
    """모델 산출 → 검증 → 중복 병합 또는 신규 저장. 반환은 저장/병합된 id 목록."""
    st = store_for(settings)
    seed_ids = {a.id for a in batch_seeds}
    touched_entities: set[str] = set()
    out: list[str] = []

    for raw in raw_atoms or []:
        if not isinstance(raw, dict):
            continue
        seed_id = (raw.get("seed_id") or "").strip()
        axis = raw.get("axis") if raw.get("axis") in AXES else None
        body = (raw.get("body") or "").strip()
        title = (raw.get("title") or "").strip()
        if not body or not title or not axis or seed_id not in seed_ids:
            state["skipped"] += 1
            continue
        layer = raw.get("layer") if raw.get("layer") in LAYERS else "culture"
        scope = raw.get("scope") if raw.get("scope") in SCOPES else "global"
        tags = sorted({slug(t) for t in (raw.get("tags") or []) if t} | {"expansion", _AXIS_TAG[axis]})[:14]
        ents = sorted({slug(e) for e in (raw.get("entities") or []) if e})[:8]

        aid = atom_id(layer, ents, body)
        existing = st.load(aid) or _find_near_duplicate(st, layer, ents, tags, scope)
        if existing is not None:
            # 이미 있는 사실 — 새로 쓰지 않고 계보(refs)와 태그만 합친다. uses 는 회상/보고서
            # 재사용의 지표이므로 여기서 올리지 않는다.
            if seed_id not in existing.refs:
                existing.refs = (existing.refs + [seed_id])[-16:]
            existing.tags = sorted(set(existing.tags) | set(tags))[:16]
            st.save(existing)
            touched_entities.update(existing.entities)
            state["merged"] += 1
            out.append(existing.id)
            continue

        lat, lng = raw.get("lat"), raw.get("lng")
        if scope in ("global", "period"):
            lat = lng = None
        try:
            lat = float(lat) if lat is not None else None
            lng = float(lng) if lng is not None else None
        except (TypeError, ValueError):
            lat = lng = None
        radius = raw.get("radius_km")
        atom = Atom(
            id=aid, layer=layer, scope=scope, title=title[:120], body=body,
            tags=tags, entities=ents,
            period_start=raw.get("period_start") if isinstance(raw.get("period_start"), int) else None,
            period_end=raw.get("period_end") if isinstance(raw.get("period_end"), int) else None,
            lat=lat, lng=lng,
            radius_km=float(radius) if isinstance(radius, (int, float)) else _SCOPE_RADIUS_KM.get(scope, 0),
            cell=geohash(lat, lng, 7) if lat is not None and lng is not None else "",
            lang="en", sources=[], refs=[seed_id], reports=[],
        )
        st.save(atom)
        touched_entities.update(ents)
        state["created"] += 1
        out.append(atom.id)

    for e in touched_entities:
        _write_entity_note(st, e)
    return out


def run(settings: Settings, *, limit: int = 0, batch: int = 6, dry_run: bool = False,
        parallel: int = 3, log=print) -> dict:
    """확장 패스 실행. limit=0 이면 남은 씨앗 전부. 반환은 상태 dict."""
    st = store_for(settings)
    # 씨앗은 보고서(현장 조사)에서 태어난 원자만 — 확장 원자는 재확장하지 않고(자기 증식),
    # 위키 인제스트 원자도 제외한다(이미 일반화된 리스트 요약이라 worldwide/people/counterpart
    # 축으로 다시 일반화할 것이 없다. eb4d8e1 의 전제도 "다음 보고서가 만든 원자만"이었다).
    seeds = [a for a in st.all_atoms()
             if "expansion" not in a.tags and "wiki" not in a.tags]
    state = _load_state(settings)
    done = set(state["seeds_done"])
    todo = [a for a in seeds if a.id not in done]
    if limit:
        todo = todo[:limit]
    log(f"[expand] 씨앗 {len(seeds)}개 중 남은 것 {len(todo)}개 (완료 {len(done)}) · 배치 {batch}")
    if dry_run or not todo:
        return state

    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]

    def _one(chunk: list[Atom]):
        user = (
            f"{_seed_block(chunk)}\n\n"
            "Expand each seed along the three axes (worldwide / people / counterpart). "
            "Call expansion_atoms once."
        )
        return llm.call(
            settings,
            system=_EXPAND_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=[_EXPAND_TOOL],
            tool_choice={"type": "tool", "name": "expansion_atoms"},
            max_tokens=8000,
            effort=settings.knowledge_effort,
            deadline_s=max(120.0, settings.knowledge_timeout_s),
            role="fact",
        )

    # 배치들을 소그룹으로 묶어 동시 실행 — 실패한 배치는 다음 실행에서 재시도된다.
    for gi in range(0, len(batches), parallel):
        group = batches[gi:gi + parallel]
        results = llm.gather([lambda c=c: _one(c) for c in group], settings, limit=parallel)
        for chunk, resp in zip(group, results):
            if isinstance(resp, Exception):
                log(f"[expand] 배치 실패({chunk[0].id}…): {resp}")
                continue
            state["cost_usd"] = round(state["cost_usd"] + llm.spend(resp), 4)
            data = llm.tool_input(resp, "expansion_atoms") or {}
            ids = _absorb(settings, chunk, data.get("atoms"), state)
            state["seeds_done"] = sorted(set(state["seeds_done"]) | {a.id for a in chunk})
            _save_state(settings, state)
            log(f"[expand] {gi + group.index(chunk) + 1}/{len(batches)} 배치 → 원자 {len(ids)}개 "
                f"(누적 신규 {state['created']} · 병합 {state['merged']} · ${state['cost_usd']})")
    return state
