"""범주(category) 재분류 — 보고서 유래 원자에 닫힌 어휘 category 를 채운다(기획 v3 1단계).

문제: Atom.layer 는 7개뿐이라 "경제" 하나에 조선소·양조장·리튬 광산·크루즈 관광이 다
들어간다. 렌즈(주식·부동산·인재 …)가 그 안에서 갈라 쓸 손잡이가 없다.

여기서 하는 것: 이미 저장된 원자 중 **보고서에서 태어난 것**(a.reports 가 있는 것 —
확장·위키 원자는 대상 아님)만 골라, knowledge.ALL_CATEGORIES 닫힌 어휘로 한 번 분류한다.
본문·태그·좌표는 건드리지 않는다 — category 한 칸만 채운다(마이그레이션 아님).

원칙:
  · 새 사실을 만들지 않는다 — 이미 있는 원자의 라벨만 정한다.
  · 어휘 밖으로 나가면 "other" 로 강등한다(폐기하지 않음, 재시도하지 않음).
  · 체크포인트(categorize.json)에 처리한 원자 id 를 기록한다 — 중단해도 이어서 돈다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import llm
from .config import Settings
from .knowledge import ALL_CATEGORIES, CATEGORIES_BY_LAYER, CATEGORY_OTHER, CUE_CATEGORIES, Atom, store_for

_CATEGORIZE_TOOL = {
    "name": "atom_categories",
    "description": "Assign each given atom exactly one category from the closed vocabulary.",
    "input_schema": {
        "type": "object",
        "required": ["assignments"],
        "properties": {
            "assignments": {
                "type": "array",
                "description": "One entry per ATOM below, in any order.",
                "items": {
                    "type": "object",
                    "required": ["id", "category"],
                    "properties": {
                        "id": {"type": "string", "description": "atm_ id copied from the ATOMS list."},
                        "category": {"type": "string", "enum": sorted(ALL_CATEGORIES)},
                    },
                },
            },
        },
    },
}


def _vocab_block() -> str:
    lines = [f"{layer}: {', '.join(cats)}" for layer, cats in CATEGORIES_BY_LAYER.items()]
    lines.append(f"cue (any layer — point-scope visual/physical observation): {', '.join(CUE_CATEGORIES)}")
    return "\n".join(lines)


_CATEGORIZE_SYSTEM = (
    "You are classifying existing knowledge atoms (from a geography research tool) into a CLOSED "
    "category vocabulary. You are not writing new facts — only assigning a label.\n\n"
    f"VOCABULARY (grouped by the atom's own layer; 'cue' slugs may apply under any layer):\n"
    f"{_vocab_block()}\n\ncategory {CATEGORY_OTHER}: use only when nothing above genuinely fits.\n\n"
    "RULES\n"
    "- Pick exactly ONE slug per atom, copied verbatim from the vocabulary above.\n"
    "- The slug does not have to belong to the atom's own layer group — 'cue' slugs describe a "
    "point-scope visual/physical detail (a road-sign style, a utility-pole type, a license-plate "
    "format) and can apply to an atom tagged with any layer.\n"
    f"- Use '{CATEGORY_OTHER}' only when the atom genuinely fits none of the listed slugs. Do not "
    "stretch a category to fit, and never invent a new slug.\n"
    "- Judge from title/body/layer/scope; tags are a tie-breaker only.\n"
    "- Return exactly one assignment per atom id given, no more, no fewer."
)


def _atom_block(atoms: list[Atom]) -> str:
    lines = []
    for a in atoms:
        tags = " ".join(f"#{t}" for t in a.tags[:6])
        lines.append(f"[[{a.id}]] ({a.layer}/{a.scope}) **{a.title}** — {a.body} {tags}")
    return "\n".join(lines)


def _state_path(settings: Settings) -> Path:
    return Path(settings.knowledge_dir) / "categorize.json"


def _load_state(settings: Settings) -> dict:
    p = _state_path(settings)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "done" in d:
                return d
        except Exception:  # noqa: BLE001
            pass
    return {
        "meta": {"pass": "categorize", "note": "보고서 유래 원자 → 닫힌 범주 어휘 재분류"},
        "done": [], "categorized": 0, "other": 0, "skipped": 0, "cost_usd": 0.0,
    }


def _save_state(settings: Settings, state: dict) -> None:
    state["meta"]["updated"] = time.time()
    p = _state_path(settings)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def run(settings: Settings, *, limit: int = 0, batch: int = 20, parallel: int = 3,
        dry_run: bool = False, log=print) -> dict:
    """재분류 실행. limit=0 이면 남은 대상 전부. 반환은 상태 dict."""
    st = store_for(settings)
    # 대상 = 보고서에서 태어난 원자(reports 비어있지 않음)이면서 아직 category 가 없는 것.
    # 확장(expand)·위키(wiki) 원자는 reports=[] 로 만들어지므로 자연히 제외된다.
    targets_all = [a for a in st.all_atoms() if a.reports]
    state = _load_state(settings)
    done = set(state["done"])
    todo = [a for a in targets_all if a.id not in done]
    if limit:
        todo = todo[:limit]
    log(f"[categorize] 보고서 유래 원자 {len(targets_all)}개 중 남은 것 {len(todo)}개"
        f" (완료 {len(done)}) · 배치 {batch}")
    if dry_run or not todo:
        return state

    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]

    def _one(chunk: list[Atom]):
        user = (
            f"ATOMS ({len(chunk)}):\n{_atom_block(chunk)}\n\n"
            "Call atom_categories once with one assignment per atom id above."
        )
        return llm.call(
            settings,
            system=_CATEGORIZE_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=[_CATEGORIZE_TOOL],
            tool_choice={"type": "tool", "name": "atom_categories"},
            max_tokens=4000,
            effort=settings.knowledge_effort,
            deadline_s=settings.knowledge_timeout_s,
            role="fact",
        )

    for gi in range(0, len(batches), parallel):
        group = batches[gi:gi + parallel]
        results = llm.gather([lambda c=c: _one(c) for c in group], settings, limit=parallel)
        for chunk, resp in zip(group, results):
            by_id = {a.id: a for a in chunk}
            if isinstance(resp, Exception):
                log(f"[categorize] 배치 실패({chunk[0].id}…): {resp}")
                continue
            state["cost_usd"] = round(state["cost_usd"] + llm.spend(resp), 4)
            data = llm.tool_input(resp, "atom_categories") or {}
            n_cat = n_other = 0
            for raw in data.get("assignments") or []:
                if not isinstance(raw, dict):
                    continue
                aid = str(raw.get("id") or "")
                a = by_id.pop(aid, None)
                if a is None:
                    continue
                cat = raw.get("category") if raw.get("category") in ALL_CATEGORIES else CATEGORY_OTHER
                a.category = cat
                st.save(a)
                state["done"].append(a.id)
                if cat == CATEGORY_OTHER:
                    n_other += 1
                else:
                    n_cat += 1
            state["categorized"] += n_cat
            state["other"] += n_other
            state["skipped"] += len(by_id)  # 모델이 응답에서 빠뜨린 원자 — 다음 실행에서 재시도
            state["done"] = sorted(set(state["done"]))
            _save_state(settings, state)
            log(f"[categorize] {gi + group.index(chunk) + 1}/{len(batches)} 배치 → "
                f"범주 {n_cat} · other {n_other} · 누락 {len(by_id)} "
                f"(누적 {state['categorized']} · other {state['other']} · ${state['cost_usd']})")
    return state
