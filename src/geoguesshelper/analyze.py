"""Claude 비전 분석 — 캡처 PNG → 지오게서 메타 기반 국가/지역·문화·경제 구조화 리포트.

Claude 에는 네이티브 JSON 모드가 없으므로 tool_use 의 input_schema 로 구조화 출력을 강제한다.
얼굴·번호판은 Google 이 블러 처리 → 개인 식별 금지(프롬프트 + 하드 가드).
"""
from __future__ import annotations

import base64
from pathlib import Path

from .config import Settings

_SYSTEM = """You are a GeoGuessr scene analyst. You receive Google Street View screenshots
(top = 로드뷰, bottom = 지도). Infer the geographic / cultural / economic context.

HARD RULES
- Analyze scene, geography, infrastructure, vegetation, signage, and culture ONLY.
- Faces and license-plate characters are intentionally blurred. NEVER identify a specific
  person and never guess plate characters. You may use plate SHAPE and COLOR as a country cue.
- If evidence is weak, lower confidence rather than inventing detail.

METHOD (reason, then call the tool)
1. Coarse filters: driving side, dominant script/language, hemisphere from sun/shadow, climate/biome.
2. Fine metas: road-line colors, bollards, utility poles, license-plate format, the Google car
   (snorkel/antenna/camera generation), sign fonts & backs, chevrons, guardrails.
3. Language: list scripts/languages you can read, with the evidence text.
4. Landmarks / route markers.
5. Cultural/economic read: development level & culture from vehicles, road quality, upkeep, ads.
6. best_guess country + region with calibrated confidence; 1-3 alternatives summing (with best) to <= 1.0.
Every confidence must be justified by at least one item in "cues"."""

_TOOL = {
    "name": "report_location",
    "description": "Return the structured GeoGuessr scene analysis.",
    "input_schema": {
        "type": "object",
        "required": ["best_guess", "coarse_filters", "cues", "cultural_economic_read"],
        "properties": {
            "best_guess": {
                "type": "object",
                "required": ["country", "confidence"],
                "properties": {
                    "country": {"type": "string"},
                    "country_iso": {"type": "string"},
                    "confidence": {"type": "number"},
                    "region_or_state": {"type": ["string", "null"]},
                    "city": {"type": ["string", "null"]},
                    "coordinate_estimate": {
                        "type": ["object", "null"],
                        "properties": {
                            "lat": {"type": "number"},
                            "lng": {"type": "number"},
                            "radius_km": {"type": "number"},
                        },
                    },
                },
            },
            "alternatives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "country": {"type": "string"},
                        "country_iso": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            },
            "coarse_filters": {
                "type": "object",
                "properties": {
                    "driving_side": {"type": "string"},
                    "hemisphere": {"type": "string"},
                    "approx_climate_biome": {"type": "string"},
                },
            },
            "cues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["category", "observation"],
                    "properties": {
                        "category": {"type": "string"},
                        "observation": {"type": "string"},
                        "weight": {"type": "string"},
                        "supports": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "languages_detected": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string"},
                        "script": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            },
            "landmarks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": ["string", "null"]},
                        "type": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            },
            "cultural_economic_read": {"type": "string"},
            "overall_confidence": {"type": "number"},
            "place_slug": {
                "type": ["string", "null"],
                "description": (
                    "Short ASCII, lowercase, hyphen-separated filename token for THIS location by its "
                    "most recognizable name — normally the romanized city (e.g. 'paris', 'new-york'); "
                    "but if a landmark/monument here is more internationally famous than the city, use "
                    "that instead (e.g. 'eiffel-tower', 'taj-mahal'). a-z 0-9 hyphens only; no spaces."
                ),
            },
        },
    },
}

# 대략적 비용 추정 (claude-opus-4-8: $5/MTok in, $25/MTok out)
_IN_PER_MTOK = 5.0
_OUT_PER_MTOK = 25.0


def _b64(path: Path) -> tuple[str, str]:
    data = base64.standard_b64encode(path.read_bytes()).decode()
    media = "image/png"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        media = "image/jpeg"
    return data, media


def analyze_captures(image_paths: list[Path], settings: Settings, lang: str = "ko") -> dict:
    """캡처 이미지들을 Claude 비전으로 분석 → 구조화 dict. 예산 캡 적용.

    lang: 자연어 필드(관찰/문화경제 서술 등)를 쓸 출력 언어 코드(ko/en/ja/...).
    """
    from . import i18n

    lang = i18n.normalize(lang)
    if not settings.has_anthropic:
        return {"status": "NO_KEY", "message": "ANTHROPIC_API_KEY 가 필요합니다. .env 를 설정하세요."}
    try:
        import anthropic  # type: ignore
    except ModuleNotFoundError:
        return {
            "status": "NO_DEP",
            "message": "anthropic 미설치 — `uv sync --extra analyze` 후 다시 시도하세요.",
        }

    paths = [Path(p) for p in image_paths][: settings.max_analyze_images]
    paths = [p for p in paths if p.exists()]
    if not paths:
        return {"status": "NO_IMAGES", "message": "분석할 캡처 이미지가 없습니다."}

    content: list[dict] = []
    for p in paths:
        data, media = _b64(p)
        content.append(
            {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
        )
    content.append(
        {
            "type": "text",
            "text": (
                f"위 {len(paths)}장의 스트리트뷰 캡처를 분석해 report_location 도구를 호출하세요. "
                "단서(cues)를 먼저 근거로 나열하고, 모든 confidence 를 cue 로 정당화하세요."
            ),
        }
    )

    import httpx

    from .tls import httpx_verify

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        http_client=httpx.Client(verify=httpx_verify(), timeout=120.0),
    )
    try:
        resp = client.messages.create(
            model=settings.model,
            max_tokens=4096,
            system=_SYSTEM + i18n.claude_language_directive(lang),
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "report_location"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "API_ERROR", "message": f"Claude 호출 실패: {exc}"}

    result = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            result = block.input
            break

    usage = getattr(resp, "usage", None)
    cost = 0.0
    if usage is not None:
        cost = (
            usage.input_tokens / 1_000_000 * _IN_PER_MTOK
            + usage.output_tokens / 1_000_000 * _OUT_PER_MTOK
        )

    return {
        "status": "OK" if result else "NO_TOOL_USE",
        "analysis": result,
        "lang": lang,
        "images": [p.name for p in paths],
        "cost_usd": round(cost, 4),
        "model": settings.model,
        "privacy": {
            "individuals_identified": False,
            "note": "얼굴·번호판은 블러 처리됨 — 개인 식별 안 함.",
        },
    }
