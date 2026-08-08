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
        },
    },
}

_IN_PER_MTOK = 5.0
_OUT_PER_MTOK = 25.0
_WEB_SEARCH_USD = 0.01  # $10 / 1000 requests


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


def _cost(usage) -> float:
    if usage is None:
        return 0.0
    c = (usage.input_tokens / 1_000_000 * _IN_PER_MTOK
         + usage.output_tokens / 1_000_000 * _OUT_PER_MTOK)
    st = getattr(usage, "server_tool_use", None)
    if st is not None:
        c += (getattr(st, "web_search_requests", 0) or 0) * _WEB_SEARCH_USD
    return c


def research_location(place: dict, settings: Settings, lang: str = "ko", *, max_searches: int = 5) -> dict:
    """place(best_guess)를 웹 검색으로 조사. 반환 {status, profile, sources, cost_usd, model, lang}."""
    from . import i18n

    lang = i18n.normalize(lang)
    if not settings.has_anthropic:
        return {"status": "NO_KEY", "message": "ANTHROPIC_API_KEY 가 필요합니다(.env)."}
    if not (place.get("country") or place.get("city")):
        return {"status": "NO_PLACE", "message": "식별된 장소가 없어 리서치를 건너뜁니다."}

    try:
        import anthropic  # type: ignore
    except ModuleNotFoundError:
        return {"status": "NO_DEP", "message": "anthropic 미설치."}

    import httpx

    from .tls import httpx_verify

    loc = _place_str(place)
    langname = i18n._CLAUDE.get(lang, "English")
    system = (
        "You are a geography/travel researcher. Given a place, use web_search to gather ACCURATE, "
        "up-to-date facts, then call location_profile. Prefer authoritative sources (encyclopedias, "
        "official statistics, tourism boards, reputable news). If a small settlement lacks data, "
        "widen to the municipality/region and say so. Do not invent facts.\n\n"
        f"OUTPUT LANGUAGE: write EVERY string value in the location_profile call in {langname}. "
        "Proper nouns (place/person names) may stay in their original script."
    )
    user = (
        f"Research this place and build a rich profile: {loc}.\n"
        "Use several focused web_search queries — including specific ones for: (a) notable people born in "
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
    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches},
        _PROFILE_TOOL,
    ]

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        http_client=httpx.Client(verify=httpx_verify(), timeout=180.0),
    )

    def _extract_profile(resp):
        for b in resp.content:
            if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == "location_profile":
                return b.input
        return None

    try:
        resp = client.messages.create(
            model=settings.model, max_tokens=6000, system=system, tools=tools,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "API_ERROR", "message": f"리서치 호출 실패: {exc}"}

    profile = _extract_profile(resp)
    sources = _sources_from(resp)
    cost = _cost(getattr(resp, "usage", None))

    # 폴백: 모델이 검색만 하고 도구를 안 불렀으면, 검색 텍스트를 넘겨 강제 구조화.
    if profile is None:
        notes = " ".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if notes:
            try:
                resp2 = client.messages.create(
                    model=settings.model, max_tokens=6000, system=system,
                    tools=[_PROFILE_TOOL],
                    tool_choice={"type": "tool", "name": "location_profile"},
                    messages=[{"role": "user", "content":
                        f"Research notes about {loc}:\n\n{notes}\n\nProduce the location_profile now."}],
                )
                profile = _extract_profile(resp2)
                cost += _cost(getattr(resp2, "usage", None))
            except Exception:  # noqa: BLE001
                pass

    if profile is None:
        return {"status": "NO_PROFILE", "message": "리서치 결과 구조화 실패.", "sources": sources,
                "cost_usd": round(cost, 4)}

    return {
        "status": "OK",
        "profile": _strip_cites(profile),
        "sources": sources,
        "cost_usd": round(cost, 4),
        "model": settings.model,
        "lang": lang,
        "place": loc,
    }
