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
import time as _time
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
    # 보고서 스크립트(사람이 읽는 서술) — 보고서에서 가장 긴 산문이다.
    "headline", "lede", "confidence_note",
}
# 문자열 리스트를 통째로 번역할 키
_TEXT_LIST_KEYS = {"pros", "cons", "open_questions", "candidates", "ruled_out"}

# 절대 번역하지 않는 키(방어용 — 위 화이트리스트와 겹치지 않지만 명시해 둔다)
_NEVER = {
    "country_iso", "place_slug", "driving_side", "hemisphere", "weight",
    "supports", "confidence", "overall_confidence", "coordinate_estimate",
    "level", "confidence_after",
    "lat", "lng", "radius_km", "url", "sources", "id", "layer", "scope", "tags",
    "entities", "period", "refs", "cells", "key", "_meta",
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
# 한 번의 호출에 넣을 상한. 실측으로 정했다 — 보고서 하나가 만들어 내는 번역 대상은
# 150개 안팎 · 2만 자를 넘고, 그걸 통째로 한 호출에 넣으면 출력 토큰과 벽시계 마감
# 어느 쪽이든 걸린다. 걸리면 **조용히 원문(영어)이 그대로 남는다**.
# 나눠서 동시에 돌리면 호출당 지연이 짧아지고, 한 덩이가 실패해도 나머지는 살아남는다.
_CHUNK_ITEMS = 30
_CHUNK_CHARS = 6000


def _chunk(texts: dict[str, str]) -> list[dict[str, str]]:
    """개수와 글자 수 **둘 다** 보고 자른다. 긴 문장 몇 개로도 한도를 넘길 수 있다."""
    out: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    n = 0
    for k, v in texts.items():
        if cur and (len(cur) >= _CHUNK_ITEMS or n + len(v) > _CHUNK_CHARS):
            out.append(cur)
            cur, n = {}, 0
        cur[k] = v
        n += len(v)
    if cur:
        out.append(cur)
    return out


def translate_texts(
    texts: dict[str, str], target_lang: str, settings: Settings, *, base_lang: str = "en",
    stats: dict | None = None,
) -> tuple[dict[str, str], float]:
    """{id: 원문} → ({id: 번역}, 비용).

    실패해도 원문을 돌려주어 보고서는 계속 만들지만, **그 사실을 stats 로 알린다**.
    예전에는 조용히 원문을 돌려줘서 한국어 탭이 통째로 영어인 보고서가 나갔는데
    작업 로그에는 errors:[] 로 찍혔다. 실패는 반드시 밖으로 나가야 한다.
    """
    if not texts:
        return {}, 0.0
    if stats is not None:
        stats.setdefault("requested", 0)
        stats.setdefault("translated", 0)
        stats.setdefault("failed_chunks", 0)
        stats.setdefault("chunks", 0)
        stats.setdefault("errors", [])
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
    chunks = _chunk(texts)

    def run_one(part: dict[str, str]):
        payload = [{"id": k, "text": v} for k, v in part.items()]
        user = (
            "Translate every `text` below. Call translated_strings exactly once with one entry per "
            "input id.\n\n" + _json_compact(payload)
        )
        return llm.call(
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

    out = dict(texts)  # 기본값 = 원문(모델이 빠뜨린 항목 보호)
    cost = 0.0

    def _absorb(parts: list[dict], results: list) -> list[tuple[dict, str]]:
        """응답을 out 에 반영하고, 실패한 (청크, 사유) 목록을 돌려준다."""
        nonlocal cost
        bad: list[tuple[dict, str]] = []
        for part, res in zip(parts, results):
            if isinstance(res, Exception):
                bad.append((part, f"{type(res).__name__}: {str(res)[:80]}"))
                continue
            cost += llm.spend(res)
            data = llm.tool_input(res, "translated_strings") or {}
            got = 0
            for item in data.get("items") or []:
                if not isinstance(item, dict):
                    continue
                i, t = item.get("id"), item.get("text")
                if isinstance(i, str) and i in part and isinstance(t, str) and t.strip():
                    out[i] = t
                    got += 1
            if got == 0:
                bad.append((part, "빈 응답(도구 호출 없음 또는 항목 0개)"))
        return bad

    failed_pairs = _absorb(chunks, llm.gather([(lambda c=c: run_one(c)) for c in chunks], settings))
    if failed_pairs:
        # 실패의 대부분은 일시 오류(레이트리밋·타임아웃)다 — 특히 밤에 보고서를 연달아
        # 뽑을 때 그렇다(실측: 260813 밤 배치 9건에서 한국어 청크들이 죽어 영어로 남았다).
        # 실패한 청크만 잠시 뒤 **순차로** 한 번 더 보낸다. 동시성을 줄여야 같은 제한에
        # 다시 걸리지 않는다.
        _time.sleep(3.0)
        retry_parts = [p for p, _ in failed_pairs]
        failed_pairs = _absorb(
            retry_parts,
            llm.gather([(lambda c=c: run_one(c)) for c in retry_parts], settings, limit=1),
        )
        if stats is not None:
            stats["retried"] = len(retry_parts)
    failed = len(failed_pairs)
    errs = [msg for _, msg in failed_pairs]

    if stats is not None:
        stats["requested"] += len(texts)
        stats["translated"] += sum(1 for k, v in texts.items() if out.get(k) != v)
        stats["chunks"] += len(chunks)
        stats["failed_chunks"] += failed
        if errs:
            stats["errors"].extend(errs[:4])
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

    st: dict = {}
    done, cost = translate_texts(texts, target, settings, base_lang=base_lang, stats=st)

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
        # 실패를 밖으로 내보낸다. 호출부는 이 값을 보고 사용자에게 알려야 한다 —
        # 조용히 영어 보고서를 내보내는 것이 이 시스템의 가장 오래된 실수였다.
        "failed_chunks": st.get("failed_chunks", 0),
        "chunks": st.get("chunks", 0),
        "errors": st.get("errors", []),
    }


def translate_script(script: dict | None, target_lang: str, settings: Settings,
                     *, base_lang: str = "en") -> tuple[dict | None, float, dict]:
    """보고서 스크립트(headline/lede/sections/confidence_note)를 통째로 번역한다.

    예전에는 스크립트를 기준 언어로만 만들고 **그 언어 섹션에만** 실었다.
    오역을 피하려던 것인데, 결과적으로 한국어 독자는 보고서에서 가장 긴 산문
    (9천~1만 4천 자)을 아예 보지 못했다. 번역이 제대로 도는 지금은 옮기는 편이 낫다.
    `key` 와 `_meta` 는 로직 값이라 _NEVER 에 있어 전송되지 않는다.
    """
    if not isinstance(script, dict) or not script.get("sections"):
        return script, 0.0, {}
    st: dict = {}
    a = copy.deepcopy(script)
    slots: list[tuple[tuple, str]] = []
    _harvest(a, slots)
    if not slots:
        return script, 0.0, {}
    texts = {f"s{i}": v for i, (_, v) in enumerate(slots)}
    done, cost = translate_texts(texts, target_lang, settings, base_lang=base_lang, stats=st)
    for i, (path, val) in enumerate(slots):
        _splice(a, path, done.get(f"s{i}", val))
    meta = dict(a.get("_meta") or {})
    meta["lang"] = i18n.normalize(target_lang)
    meta["translatedFrom"] = i18n.normalize(base_lang)
    a["_meta"] = meta
    return a, cost, st


def _json_compact(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
