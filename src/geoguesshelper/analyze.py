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

METHOD — progressive discrimination, not assertion

The valuable part of your work is NARROWING, not naming. Identifying the country from a script
any beginner recognises is the trivial step; the analysis earns its keep in what comes after it.

Build `narrowing` as an ordered chain, coarse to fine. Each step takes the candidates still alive,
asks ONE question that splits them, and kills at least one with a specific observation.

  continent/hemisphere → country → macro-region → cultural/ethnolinguistic sphere
    → admin region (province/prefecture/state) → metro area → district

HARD RULES FOR THE CHAIN
- ONE step may settle the country. After that, every further step MUST discriminate BELOW the
  country. Re-stating "the signs are Japanese, therefore Japan" three times is worthless.
- Every step needs a real `ruled_out`. If nothing is eliminated, the step does not belong.
- `discriminator` must be counterfactual: say what you would have seen INSTEAD in the rejected
  candidate. "Kansai would use X-shaped poles and different guardrail profiles" — that is a
  discriminator. "This looks like Hokuriku" is not.
- Prefer cuts that a non-expert could not make: regional utility-pole hardware, guardrail and
  bollard profiles, snow-country roof pitch and eave depth, regional rail operators, prefectural
  route-marker shapes, dialect on shop signage, agricultural pattern, soil and rock colour.
- Use cultural/ethnolinguistic spheres where they cut DIFFERENTLY from administrative borders
  (Yamato vs Ainu/Ryukyuan, Han vs Uyghur/Tibetan, Castilian vs Catalan vs Basque, Flemish vs
  Walloon, Bantu vs Khoisan, criollo vs indigenous highland). Say which sphere and why.
- If a level genuinely cannot be split from the image, emit the step with an empty `ruled_out`,
  say plainly what evidence is missing, and lower `confidence_after`. Do not invent a cut.

THEN
- `cues`: the raw observations that feed the chain. Tag each with the narrowing level it serves.
- `languages_detected`, `landmarks`, `coarse_filters`, `cultural_economic_read` as specified.
- `best_guess`: the finest level the chain actually reached — not the country if you got further.
  1-3 alternatives summing (with best) to <= 1.0. Alternatives should be the candidates your LAST
  step could not fully eliminate, not other countries you already ruled out."""

_TOOL = {
    "name": "report_location",
    "description": "Return the structured GeoGuessr scene analysis.",
    "input_schema": {
        "type": "object",
        "required": ["best_guess", "coarse_filters", "narrowing", "cues", "cultural_economic_read"],
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
            "narrowing": {
                "type": "array",
                "description": (
                    "The discrimination chain, coarsest first. Each entry must eliminate at least "
                    "one live candidate. At most ONE entry may settle the country; the rest must "
                    "cut below it."
                ),
                "items": {
                    "type": "object",
                    "required": ["level", "question", "candidates", "observation",
                                 "discriminator", "ruled_out", "conclusion"],
                    "properties": {
                        "level": {
                            "type": "string",
                            "enum": ["continent", "country", "macro_region", "cultural_sphere",
                                     "admin_region", "metro_area", "district"],
                            "description": "ASCII enum — do NOT translate.",
                        },
                        "question": {
                            "type": "string",
                            "description": "The single question this step splits, e.g. 'Eastern or western Honshu?'",
                        },
                        "candidates": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Candidates still alive when this step begins.",
                        },
                        "observation": {
                            "type": "string",
                            "description": "What you actually see in the image that bears on the question.",
                        },
                        "discriminator": {
                            "type": "string",
                            "description": (
                                "Counterfactual reasoning: what would have appeared INSTEAD in the "
                                "candidates you are rejecting. This is the part that must be sharp."
                            ),
                        },
                        "ruled_out": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Candidates eliminated here, each with its one-line reason. Empty only if the level truly cannot be split.",
                        },
                        "conclusion": {"type": "string", "description": "What survives this step."},
                        "weight": {"type": "string", "description": "high | medium | low (ASCII enum)"},
                        "confidence_after": {
                            "type": "number",
                            "description": "0-1 confidence in `conclusion` after this step.",
                        },
                    },
                },
            },
            "cues": {
                "type": "array",
                "description": "Raw observations feeding the chain. Tag each with the level it serves.",
                "items": {
                    "type": "object",
                    "required": ["category", "observation"],
                    "properties": {
                        "category": {"type": "string"},
                        "observation": {"type": "string"},
                        "weight": {"type": "string"},
                        "level": {
                            "type": "string",
                            "description": (
                                "Which narrowing level this cue serves (same enum as narrowing.level). "
                                "ASCII — do NOT translate."
                            ),
                        },
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

def _b64(path: Path) -> tuple[str, str]:
    data = base64.standard_b64encode(path.read_bytes()).decode()
    media = "image/png"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        media = "image/jpeg"
    elif path.suffix.lower() == ".webp":
        media = "image/webp"
    return data, media


def analyze_captures(image_paths: list[Path], settings: Settings, lang: str = "ko") -> dict:
    """캡처 이미지들을 Claude 비전으로 분석 → 구조화 dict. 예산 캡 적용.

    lang: 자연어 필드(관찰/문화경제 서술 등)를 쓸 출력 언어 코드(ko/en/ja/...).
    """
    from . import i18n

    from . import llm

    lang = i18n.normalize(lang)

    requested = [Path(p) for p in image_paths]
    paths = [p for p in requested[: settings.max_analyze_images] if p.exists()]
    skipped = [p.name for p in requested[settings.max_analyze_images:]]
    if not paths:
        return {"status": "NO_IMAGES", "message": "분석할 캡처 이미지가 없습니다."}

    # ── 프롬프트 캐싱 ────────────────────────────────────────────
    # 캐시는 접두사 일치이고 렌더 순서는 tools → system → messages 다.
    # 예전에는 언어 지시문을 `system` 에 붙여서, 언어가 바뀔 때마다 이미지보다 **앞** 부분이
    # 달라졌다 → 이미지가 한 번도 캐시되지 않았다(무음 무효화).
    # 이제 system 은 언어와 무관하게 고정하고, 언어 지시문은 이미지 **뒤** 텍스트 블록으로
    # 옮긴 다음 마지막 이미지에 캐시 브레이크포인트를 얹는다.
    #   tools → system(고정) → [이미지들 …cache_control] → 언어별 텍스트
    # 이미지 1장이 수천 토큰이라 1024 토큰 최소치를 넉넉히 넘겨 실제로 캐시된다.
    content: list[dict] = []
    for i, p in enumerate(paths):
        data, media = _b64(p)
        block: dict = {
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": data},
        }
        if i == len(paths) - 1:
            block["cache_control"] = {"type": "ephemeral"}
        content.append(block)
    content.append(
        {
            "type": "text",
            "text": (
                f"위 {len(paths)}장의 스트리트뷰 캡처를 분석해 report_location 도구를 호출하세요. "
                "단서(cues)를 먼저 근거로 나열하고, 모든 confidence 를 cue 로 정당화하세요."
                + i18n.claude_language_directive(lang)
            ),
        }
    )

    try:
        resp = llm.call(
            settings,
            system=_SYSTEM,                     # ← 언어 무관 고정(캐시 접두사)
            messages=[{"role": "user", "content": content}],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "report_location"},
            max_tokens=8000,     # thinking + 도구 입력을 함께 덮는 상한
            effort=settings.analyze_effort,
            deadline_s=settings.analyze_timeout_s,
            role="vision",          # 이미지 해석·추론
        )
    except llm.LLMUnavailable as exc:
        return {"status": "NO_KEY", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "API_ERROR", "message": f"Claude 호출 실패: {exc}"}

    result = llm.tool_input(resp, "report_location")
    usage = getattr(resp, "usage", None)

    return {
        "status": "OK" if result else "NO_TOOL_USE",
        "analysis": result,
        "lang": lang,
        "images": [p.name for p in paths],
        # 분석에 실제로 들어간 장면만 보고서 갤러리에 싣기 위해 상한 초과분을 알려준다.
        "skipped_images": skipped,
        "cost_usd": round(llm.spend(resp), 4),
        "used_model": llm.used_model(resp),
        "cache": llm.cache_stats(usage),
        "model": settings.model,
        "privacy": {
            "individuals_identified": False,
            "note": "얼굴·번호판은 블러 처리됨 — 개인 식별 안 함.",
        },
    }
