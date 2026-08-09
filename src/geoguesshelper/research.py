"""식별된 장소를 웹 검색으로 심층 조사 → 도시 프로파일(경제·문화·관광·인물·산업·인접도시 비교·장단점·주민).

분석(analyze.py)이 이미지만 보고 '어디인지'를 맞춘다면, 여기서는 그 장소를 Claude 웹 검색
(server tool `web_search`)으로 실제 조사해 리포트에 넣을 배경지식을 만든다. 출력 언어는 lang.

구현: tools=[web_search, location_profile] 단일 호출로 검색 → 구조화 도구 호출. 모델이 도구를
안 부르면(드묾) 검색 텍스트를 넘겨 tool_choice 강제로 한 번 더 구조화한다.
"""
from __future__ import annotations

import re

from .config import Settings

# Claude 인용 마크업(<cite index="1-2">...</cite>) 제거용 — 출처는 별도 목록으로 제공.
_CITE_RE = re.compile(r"</?cite\b[^>]*>", re.I)


def _strip_cites(v):
    if isinstance(v, str):
        return _CITE_RE.sub("", v).strip()
    if isinstance(v, list):
        return [_strip_cites(x) for x in v]
    if isinstance(v, dict):
        return {k: _strip_cites(x) for k, x in v.items()}
    return v


_PROFILE_TOOL = {
    "name": "location_profile",
    "description": "Return a researched profile of the identified place.",
    "input_schema": {
        "type": "object",
        "required": ["summary", "economy", "industry", "culture", "tourism",
                     "notable_people", "neighbor_comparison", "pros", "cons", "residents"],
        "properties": {
            "summary": {"type": "string", "description": "2-4 sentence overview of the place."},
            "economy": {"type": "string", "description": "Economy: main sectors, income level, employers."},
            "industry": {"type": "string", "description": "Key industries / what the place produces or is known for."},
            "culture": {"type": "string", "description": "Culture, history, identity, festivals, food."},
            "tourism": {"type": "string", "description": "Tourism: what visitors come for, main attractions."},
            "notable_people": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "note": {"type": "string", "description": "who they are / why notable"},
                    },
                },
            },
            "neighbor_comparison": {
                "type": "string",
                "description": "How it compares to / competes with neighboring towns/cities, and why.",
            },
            "pros": {"type": "array", "items": {"type": "string"}, "description": "Advantages / strengths of living/being there."},
            "cons": {"type": "array", "items": {"type": "string"}, "description": "Drawbacks / weaknesses."},
            "residents": {"type": "string", "description": "What the people/residents are like (demographics, character)."},
            "reused_atom_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "atm_ ids from the KNOWN FACTS block that you actually relied on instead of "
                    "searching. Leave empty if none were supplied or none applied."
                ),
            },
            "new_findings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short notes on what you had to search for because it was NOT already known.",
            },
        },
    },
}


# ── 병렬 샤드 ────────────────────────────────────────────────────
# 단일 리서치 호출이 실측 419초(검색 3회)로 전체 보고서의 병목이었다. 프로파일 필드들은
# 서로 독립적인 조사이므로 주제별로 쪼개 **동시에** 던지고 합친다. 임계경로가 '가장 느린
# 샤드 1개'로 줄어든다. 각 샤드는 좁은 질문이라 검색도 적게 쓴다.
_SHARDS = [
    {
        "key": "econ",
        "fields": ["economy", "industry"],
        "ask": "the local economy (main sectors, income level, major employers) and the key industries "
               "— what the place produces or is known for",
    },
    {
        "key": "culture",
        "fields": ["culture", "tourism"],
        "ask": "culture, history, identity, festivals and food; and tourism — what visitors come for "
               "and the main attractions",
    },
    {
        "key": "people",
        "fields": ["notable_people", "neighbor_comparison"],
        "ask": "notable people born in or associated with the place; and how it compares to or competes "
               "with neighbouring towns/cities and why",
    },
    {
        "key": "demo",
        "fields": ["residents"],
        "ask": "demographics and daily life — population size, age/decline trend, character and lifestyle "
               "of the residents",
    },
]

_MERGE_TOOL = {
    "name": "profile_merge",
    "description": "Derive the overview and the pros/cons from already-gathered facts.",
    "input_schema": {
        "type": "object",
        "required": ["summary", "pros", "cons"],
        "properties": {
            "summary": {"type": "string", "description": "2-4 sentence overview of the place."},
            "pros": {"type": "array", "items": {"type": "string"},
                     "description": "2-4 advantages, REASONED from the supplied facts."},
            "cons": {"type": "array", "items": {"type": "string"},
                     "description": "2-4 drawbacks, REASONED from the supplied facts."},
        },
    },
}


def _shard_tool(shard: dict) -> dict:
    """이 샤드가 담당하는 필드만 담은 도구 스키마."""
    props = {k: v for k, v in _PROFILE_TOOL["input_schema"]["properties"].items()
             if k in shard["fields"]}
    return {
        "name": "partial_profile",
        "description": f"Return only the researched fields: {', '.join(shard['fields'])}.",
        "input_schema": {"type": "object", "required": list(shard["fields"]), "properties": props},
    }


def _place_str(place: dict) -> str:
    parts = [place.get("city"), place.get("region_or_state"), place.get("country")]
    label = ", ".join([p for p in parts if p])
    ce = place.get("coordinate_estimate") or {}
    if ce.get("lat") is not None and ce.get("lng") is not None:
        label += f" (approx {ce.get('lat')}, {ce.get('lng')})"
    return label or "the identified location"


def _sources_from(resp) -> list[dict]:
    """응답의 web_search_tool_result 블록에서 실제 검색 결과 URL/제목을 추출(중복 제거)."""
    out: list[dict] = []
    seen: set[str] = set()
    for b in resp.content:
        if getattr(b, "type", None) != "web_search_tool_result":
            continue
        content = getattr(b, "content", None)
        if not isinstance(content, list):
            continue
        for item in content:
            url = getattr(item, "url", None)
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"title": getattr(item, "title", "") or url, "url": url})
    return out


def research_parallel(
    place: dict,
    settings: Settings,
    lang: str = "ko",
    *,
    known: list | None = None,
    should_stop=None,
    on_progress=None,
) -> dict:
    """주제별 샤드를 **동시에** 조사하고 합친다. 반환 형태는 research_location 과 동일.

    임계경로 = 가장 느린 샤드 1개 + 짧은 병합 호출. 단일 호출(실측 419초) 대비 크게 줄어든다.
    샤드 하나가 실패해도 나머지 필드로 프로파일을 만든다(부분 성공).
    """
    from . import i18n, knowledge, llm

    lang = i18n.normalize(lang)
    if not (place.get("country") or place.get("city")):
        return {"status": "NO_PLACE", "message": "식별된 장소가 없어 리서치를 건너뜁니다."}

    loc = _place_str(place)
    langname = i18n._CLAUDE.get(lang, "English")
    known_block = knowledge.as_prompt_block(known or [])
    per_shard_searches = max(1, min(3, settings.web_search_max_uses))

    known_rule = (
        "\n\nKNOWN FACTS — the knowledge base already contains the atoms listed below. Do NOT spend a "
        "web_search re-establishing anything they already state; reference them as [[atm_id]] and "
        "search only for what is missing.\n" + known_block
        if known_block else ""
    )

    def make_shard(sh: dict):
        def run():
            system = (
                "You are a geography researcher. Use web_search to gather ACCURATE, up-to-date facts "
                "about ONE narrow aspect of a place, then call partial_profile. Prefer authoritative "
                "sources. Do not invent facts. Be concise and dense — no hedging."
                + known_rule
                + f"\n\nOUTPUT LANGUAGE: write every string value in {langname}. Proper nouns may stay "
                  "in their original script."
            )
            user = (
                f"Place: {loc}\n\nResearch ONLY this aspect: {sh['ask']}.\n"
                f"Use at most {per_shard_searches} focused web_search queries, then call "
                f"partial_profile with the fields {', '.join(sh['fields'])}. Fill every field."
            )
            return llm.call(
                settings,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[
                    {"type": "web_search_20260209", "name": "web_search", "max_uses": per_shard_searches},
                    _shard_tool(sh),
                ],
                max_tokens=4000,
                effort=settings.research_effort,
                deadline_s=settings.research_timeout_s,
                should_stop=should_stop,
                role="fact",
            )
        return run

    if on_progress:
        on_progress(f"웹 리서치 {len(_SHARDS)}갈래 동시 조사…")
    results = llm.gather([make_shard(sh) for sh in _SHARDS], settings)

    profile: dict = {}
    sources: list[dict] = []
    seen_src: set[str] = set()
    cost = 0.0
    searches = 0
    failed: list[str] = []
    for sh, r in zip(_SHARDS, results):
        if isinstance(r, llm.LLMCanceled):
            raise r
        if isinstance(r, Exception):
            failed.append(sh["key"])
            continue
        cost += llm.spend(r)
        u = getattr(r, "usage", None)
        searches += getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
        part = llm.tool_input(r, "partial_profile") or {}
        for k in sh["fields"]:
            if part.get(k):
                profile[k] = part[k]
        for s in _sources_from(r):
            if s["url"] not in seen_src:
                seen_src.add(s["url"])
                sources.append(s)

    if not profile:
        return {"status": "NO_PROFILE",
                "message": f"리서치 샤드가 모두 실패했습니다({', '.join(failed)}).",
                "sources": sources, "cost_usd": round(cost, 4)}

    # 병합 — 검색 없이 이미 모은 사실만으로 개요와 장단점을 도출한다(추론 단계).
    if should_stop and should_stop():
        raise llm.LLMCanceled("호출이 취소되었습니다.")
    if on_progress:
        on_progress("조사 결과 병합 중…")
    try:
        m = llm.call(
            settings,
            system=(
                "You synthesize a place profile from facts that were already gathered. Do NOT invent "
                "new facts — reason only from what you are given. Derive pros and cons as informed "
                "judgment from those facts (strong industry/heritage/nature → pros; remoteness, harsh "
                "climate, shrinking or ageing population, few jobs → cons)."
                + f"\n\nOUTPUT LANGUAGE: write every string value in {langname}."
            ),
            messages=[{"role": "user", "content":
                       f"Place: {loc}\n\nGathered facts:\n{_json_compact(profile)}\n\n"
                       "Call profile_merge once."}],
            tools=[_MERGE_TOOL],
            tool_choice={"type": "tool", "name": "profile_merge"},
            max_tokens=3000,
            effort=settings.research_effort,
            deadline_s=settings.research_merge_timeout_s,
            should_stop=should_stop,
            role="fact",
        )
        cost += llm.spend(m)
        merged = llm.tool_input(m, "profile_merge") or {}
        for k in ("summary", "pros", "cons"):
            if merged.get(k):
                profile[k] = merged[k]
    except llm.LLMCanceled:
        raise
    except Exception:  # noqa: BLE001 — 병합 실패해도 샤드 결과로 보고서는 나간다
        pass

    profile = _strip_cites(profile)
    profile.setdefault("summary", "")
    profile.setdefault("pros", [])
    profile.setdefault("cons", [])
    return {
        "status": "OK",
        "profile": profile,
        "sources": sources,
        "cost_usd": round(cost, 4),
        "model": settings.model_fact,
        "lang": lang,
        "place": loc,
        "reused_atom_ids": [],
        "searches": searches,
        "known_offered": len(known or []),
        "shards": {"ok": len(_SHARDS) - len(failed), "failed": failed},
    }


def _json_compact(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)[:12000]


def research_location(
    place: dict,
    settings: Settings,
    lang: str = "ko",
    *,
    max_searches: int | None = None,
    known: list | None = None,
    should_stop=None,
) -> dict:
    """place(best_guess)를 웹 검색으로 조사.

    known = knowledge.recall() 이 돌려준 원자 목록. 이미 아는 사실은 다시 검색하지 말고
    [[atm_id]] 로 참조하라고 지시한다 → 검색 횟수와 출력 토큰이 줄어든다(A' 절약).

    반환 {status, profile, sources, cost_usd, model, lang, reused_atom_ids, searches}.
    """
    from . import i18n, knowledge, llm

    lang = i18n.normalize(lang)
    if not (place.get("country") or place.get("city")):
        return {"status": "NO_PLACE", "message": "식별된 장소가 없어 리서치를 건너뜁니다."}

    loc = _place_str(place)
    langname = i18n._CLAUDE.get(lang, "English")
    max_searches = settings.web_search_max_uses if max_searches is None else max_searches

    known_block = knowledge.as_prompt_block(known or [])
    known_rule = (
        "\n\nKNOWN FACTS\n"
        "- The user's knowledge base already contains the atoms listed in the prompt.\n"
        "- Do NOT spend a web_search re-establishing anything an atom already states. Treat those "
        "facts as given and reference them inline as [[atm_id]].\n"
        "- Spend your searches ONLY on what is missing: this specific settlement's current data, and "
        "anything the atoms do not cover.\n"
        "- List the atom ids you actually relied on in `reused_atom_ids`."
        if known_block else ""
    )

    system = (
        "You are a geography/travel researcher. Given a place, use web_search to gather ACCURATE, "
        "up-to-date facts, then call location_profile. Prefer authoritative sources (encyclopedias, "
        "official statistics, tourism boards, reputable news). If a small settlement lacks data, "
        "widen to the municipality/region and say so. Do not invent facts."
        + known_rule
        + f"\n\nOUTPUT LANGUAGE: write EVERY string value in the location_profile call in {langname}. "
        "Proper nouns (place/person names) may stay in their original script."
    )
    user = (
        f"Research this place and build a rich profile: {loc}.\n"
        + (f"\nKNOWN ATOMS (already researched — reference, do not re-search):\n{known_block}\n\n"
           if known_block else "")
        + "Use several focused web_search queries — including specific ones for: (a) notable people born in "
        "or associated with the place, (b) neighboring towns/cities and how this place compares to or "
        "competes with them and why, (c) demographics/population trend and daily life there.\n"
        "Then you MUST call location_profile and FILL EVERY field.\n"
        "- economy, industry, culture, tourism, neighbor_comparison: from the facts you found.\n"
        "- notable_people: 2+ if any real people are associated with the place; empty only if truly none.\n"
        "- residents: describe the people from demographic + cultural facts (population size, age/decline "
        "trend, character, lifestyle) — always fill this, never leave empty.\n"
        "- pros and cons: SYNTHESIZE 2-4 each by REASONING from the facts you gathered (e.g. strong "
        "industry/nature/heritage → pros; remoteness, harsh climate, shrinking/aging population, few jobs "
        "→ cons). These are your informed judgment from the research, so do not leave them empty.\n"
        "Base factual claims on the search results and do not fabricate specifics, but you MAY reason to "
        "derive pros/cons/residents from those facts."
    )
    # web_search_20260209 = 동적 필터링. 검색 결과를 컨텍스트에 넣기 전에 코드로 걸러
    # 후속 턴의 입력 토큰이 줄어든다(Opus 4.8 지원). code_execution 은 따로 선언하지 않는다.
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
        _PROFILE_TOOL,
    ]

    try:
        resp = llm.call(
            settings,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=tools,
            max_tokens=10000,
            effort=settings.research_effort,
            deadline_s=settings.research_timeout_s,
            should_stop=should_stop,
            role="fact",            # 사실/현상 검색
        )
    except llm.LLMTimeout as exc:
        return {"status": "TIMEOUT", "message": str(exc)}
    except llm.LLMCanceled:
        raise
    except llm.LLMUnavailable as exc:
        return {"status": "NO_KEY", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "API_ERROR", "message": f"리서치 호출 실패: {exc}"}

    profile = llm.tool_input(resp, "location_profile")
    sources = _sources_from(resp)
    usage = getattr(resp, "usage", None)
    cost = llm.spend(resp)
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0

    # 폴백: 모델이 검색만 하고 도구를 안 불렀으면, 검색 텍스트를 넘겨 강제 구조화.
    if profile is None:
        notes = llm.text_of(resp)
        if notes:
            try:
                resp2 = llm.call(
                    settings,
                    system=system,
                    messages=[{"role": "user", "content":
                        f"Research notes about {loc}:\n\n{notes}\n\nProduce the location_profile now."}],
                    tools=[_PROFILE_TOOL],
                    tool_choice={"type": "tool", "name": "location_profile"},
                    max_tokens=10000,
                    effort=settings.research_effort,
                    deadline_s=max(60.0, settings.research_timeout_s / 2),
                    role="fact",
                )
                profile = llm.tool_input(resp2, "location_profile")
                cost += llm.spend(resp2)
            except Exception:  # noqa: BLE001
                pass

    if profile is None:
        return {"status": "NO_PROFILE", "message": "리서치 결과 구조화 실패.", "sources": sources,
                "cost_usd": round(cost, 4)}

    profile = _strip_cites(profile)
    reused = [str(x) for x in (profile.pop("reused_atom_ids", None) or []) if x]
    profile.pop("new_findings", None)

    return {
        "status": "OK",
        "profile": profile,
        "sources": sources,
        "cost_usd": round(cost, 4),
        "model": settings.model,
        "lang": lang,
        "place": loc,
        "reused_atom_ids": reused,
        "searches": searches,
        "known_offered": len(known or []),
    }
