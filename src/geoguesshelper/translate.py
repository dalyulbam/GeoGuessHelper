"""기준 언어 결과 → 나머지 언어로 번역.

분석(비전)과 리서치(웹 검색)는 **언어와 무관한 사실**이다. 예전에는 언어마다 전부 다시
돌렸다 — 같은 사진을 N번 업로드해 N번 분석하고, 같은 도시를 N×5회 검색했다. 비용·지연이
N배였을 뿐 아니라, 언어별 결과가 서로 다른 도시를 주장해 한 파일 안에서 대상이 바뀌는
정합성 문제까지 있었다.

여기서는 기준 언어로 한 번만 조사하고, 나머지 언어는 **문자열만** 번역한다.

번역 안전장치 — 구조를 통째로 모델에 맡기지 않는다:
  1. 번역 대상 문자열만 뽑아낸다(harvest). 경로(path)를 키로 기억.
  2. 평평한 {id: text} 만 모델에 보낸다.
  3. 돌아온 것을 경로에 다시 끼워 넣는다(splice). 구조는 원본 그대로.

따라서 country_iso / driving_side / hemisphere / weight / place_slug / confidence /
coordinate_estimate 같은 **로직 값은 애초에 전송되지 않아** 번역이 망가뜨릴 수 없다.
"""
from __future__ import annotations

import copy
from typing import Any

from . import i18n, llm
from .config import Settings

# 번역할 키(그 값이 문자열일 때만). 나머지 키는 원본 유지.
_TEXT_KEYS = {
    # 분석
    "country", "region_or_state", "city", "approx_climate_biome",
    "category", "observation", "language", "script", "evidence",
    "name", "type", "cultural_economic_read",
    # 판별 사슬 (level/weight/confidence_after 는 로직 값이라 제외)
    "question", "discriminator", "conclusion",
    # 리서치 프로파일
    "summary", "economy", "industry", "culture", "tourism",
    "neighbor_comparison", "residents", "note", "title",
    # 종합 보고서
    "thesis", "heading", "body", "claim",
}
# 문자열 리스트를 통째로 번역할 키
_TEXT_LIST_KEYS = {"pros", "cons", "open_questions", "candidates", "ruled_out"}

# 절대 번역하지 않는 키(방어용 — 위 화이트리스트와 겹치지 않지만 명시해 둔다)
_NEVER = {
    "country_iso", "place_slug", "driving_side", "hemisphere", "weight",
    "supports", "confidence", "overall_confidence", "coordinate_estimate",
    "level", "confidence_after",
    "lat", "lng", "radius_km", "url", "sources", "id", "layer", "scope", "tags",
    "entities", "period", "refs", "cells",
}

_TOOL = {
    "name": "translated_strings",
    "description": "Return the translated version of every supplied string.",
    "input_schema": {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "description": "One entry per input string, same id, translated text.",
                "items": {
                    "type": "object",
                    "required": ["id", "text"],
                    "properties": {
                        "id": {"type": "string", "description": "Echo the input id EXACTLY."},
                        "text": {"type": "string", "description": "The translated text."},
                    },
                },
            }
        },
    },
}


# ── harvest / splice ─────────────────────────────────────────────
def _harvest(node: Any, slots: list[tuple[tuple, str]], path: tuple = ()) -> None:
    """번역 대상 문자열을 (경로, 값) 으로 수집. 컨테이너는 항상 재귀한다."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = path + (k,)
            if k in _NEVER:
                continue
            if k in _TEXT_KEYS and isinstance(v, str) and v.strip():
                slots.append((p, v))
            elif k in _TEXT_LIST_KEYS and isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, str) and item.strip():
                        slots.append((p + (i,), item))
            elif isinstance(v, (dict, list)):
                _harvest(v, slots, p)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, (dict, list)):
                _harvest(item, slots, path + (i,))


def _splice(root: Any, path: tuple, value: str) -> None:
    node = root
    for key in path[:-1]:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            return
    last = path[-1]
    try:
        node[last] = value
    except (KeyError, IndexError, TypeError):
        pass


# ── 공개 API ─────────────────────────────────────────────────────
def translate_texts(
    texts: dict[str, str], target_lang: str, settings: Settings, *, base_lang: str = "en"
) -> tuple[dict[str, str], float]:
    """{id: 원문} → ({id: 번역}, 비용). 실패하면 원문을 그대로 돌려준다(보고서는 계속 생성)."""
    if not texts:
        return {}, 0.0
    target = i18n.normalize(target_lang)
    tgt_name = i18n._CLAUDE.get(target, "English")
    src_name = i18n._CLAUDE.get(i18n.normalize(base_lang), "English")

    system = (
        f"You are a professional translator for a geography/travel research report. The source "
        f"strings are mostly {src_name} but may be mixed — detect per string and translate anyway.\n"
        f"- Translate every supplied string into {tgt_name}. Return ALL of them.\n"
        f"- A string already in {tgt_name} should be returned unchanged.\n"
        f"- Echo each `id` EXACTLY as given. Do not merge, split, drop, or reorder entries.\n"
        f"- Keep proper nouns (place names, person names, brands) in their conventional "
        f"{tgt_name} form; if there is no established form, keep the original script and add "
        f"no explanation.\n"
        f"- Preserve numbers, units, dates and any ISO/ASCII codes verbatim.\n"
        f"- Match the register of the source: these are encyclopedic report fields, not marketing copy.\n"
        f"- Never add commentary, footnotes, or content that was not in the source."
    )
    payload = [{"id": k, "text": v} for k, v in texts.items()]
    user = (
        "Translate every `text` below. Call translated_strings exactly once with one entry per "
        "input id.\n\n" + _json_compact(payload)
    )

    try:
        resp = llm.call(
            settings,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "translated_strings"},
            max_tokens=settings.translate_max_tokens,
            effort=settings.translate_effort,
            deadline_s=settings.translate_timeout_s,
            role="fact",            # 번역은 창작이 아니라 변환
        )
    except llm.LLMUnavailable:
        return dict(texts), 0.0
    except Exception:  # noqa: BLE001 — 번역 실패는 치명적이지 않다
        return dict(texts), 0.0

    cost = llm.spend(resp)
    data = llm.tool_input(resp, "translated_strings") or {}
    out = dict(texts)  # 기본값 = 원문(모델이 빠뜨린 항목 보호)
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        i, t = item.get("id"), item.get("text")
        if isinstance(i, str) and i in out and isinstance(t, str) and t.strip():
            out[i] = t
    return out, cost


def translate_bundle(
    analysis: dict | None,
    profile: dict | None,
    extras: dict[str, str] | None,
    target_lang: str,
    settings: Settings,
    *,
    base_lang: str = "en",
) -> dict:
    """분석 + 리서치 프로파일 + 부가 문자열을 한 번의 호출로 번역.

    반환 {analysis, profile, extras, cost_usd, translated, total}.
    구조는 입력과 동일하며 로직 enum 은 손대지 않는다.
    """
    target = i18n.normalize(target_lang)
    if target == i18n.normalize(base_lang):
        return {
            "analysis": analysis, "profile": profile, "extras": extras or {},
            "cost_usd": 0.0, "translated": 0, "total": 0,
        }

    a = copy.deepcopy(analysis) if isinstance(analysis, dict) else None
    p = copy.deepcopy(profile) if isinstance(profile, dict) else None

    a_slots: list[tuple[tuple, str]] = []
    p_slots: list[tuple[tuple, str]] = []
    if a is not None:
        _harvest(a, a_slots)
    if p is not None:
        _harvest(p, p_slots)

    texts: dict[str, str] = {}
    for i, (path, val) in enumerate(a_slots):
        texts[f"a{i}"] = val
    for i, (path, val) in enumerate(p_slots):
        texts[f"p{i}"] = val
    for k, v in (extras or {}).items():
        if isinstance(v, str) and v.strip():
            texts[f"x:{k}"] = v

    if not texts:
        return {"analysis": a, "profile": p, "extras": extras or {},
                "cost_usd": 0.0, "translated": 0, "total": 0}

    done, cost = translate_texts(texts, target, settings, base_lang=base_lang)

    changed = 0
    for i, (path, val) in enumerate(a_slots):
        new = done.get(f"a{i}", val)
        if new != val:
            changed += 1
        _splice(a, path, new)
    for i, (path, val) in enumerate(p_slots):
        new = done.get(f"p{i}", val)
        if new != val:
            changed += 1
        _splice(p, path, new)
    out_extras = dict(extras or {})
    for k in list(out_extras):
        out_extras[k] = done.get(f"x:{k}", out_extras[k])

    return {
        "analysis": a,
        "profile": p,
        "extras": out_extras,
        "cost_usd": round(cost, 4),
        "translated": changed,
        "total": len(texts),
    }


def _json_compact(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
