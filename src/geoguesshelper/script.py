"""보고서 **스크립트** — HTML 을 만들기 전에 먼저 만드는 중간 산출물.

왜 필요한가
-----------
예전에는 비싼 모델 하나가 분석·서술·구성을 한꺼번에 했다. 느리고, 실패하면 처음부터 다시였다.

여기서는 두 단계로 나눈다:

  1. **초안(draft)** — haiku/sonnet 이 빠르게 뼈대를 만든다. 이미 확정된 사실(분석 결과,
     리서치 프로파일, 회상된 지식 원자)을 **재료로만** 쓰고 새 사실을 만들지 않는다.
     하는 일은 '무엇을 어떤 순서로, 어떤 제목으로 말할지' 정하는 편집 작업이다.

  2. **개선/검증(refine)** — opus 가 초안을 **좁게** 검사한다. 전체를 다시 쓰지 않는다.
     확인 대상은 '재료에 없는 주장이 섞였는가'와 '핵심 판별 논리가 살아있는가' 두 가지뿐이다.
     검증량을 최소로 유지하는 것이 목적이므로, 통과하면 초안을 그대로 쓴다.

스크립트는 디스크에 저장된다(docs/report/*.script.json). 덕분에:
  · HTML 렌더가 실패해도 **모델을 다시 부르지 않고** 다시 만들 수 있다.
  · 나중에 다른 언어·다른 레이아웃으로 재조립할 수 있다.
  · 무엇을 근거로 그 문장이 나왔는지 추적할 수 있다.

중요: 스크립트는 사실의 **원천이 아니다**. 수치·좌표·enum 은 전부 분석/리서치 결과에서
그대로 온다. 스크립트가 정하는 것은 서술과 구성뿐이다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import llm
from .config import Settings

SCHEMA_VERSION = 1

_DRAFT_TOOL = {
    "name": "report_script",
    "description": "Plan the report: section order, headings, and the prose for each section.",
    "input_schema": {
        "type": "object",
        "required": ["headline", "lede", "sections"],
        "properties": {
            "headline": {
                "type": "string",
                "description": "One line naming the finest place level actually established. Not a sentence.",
            },
            "lede": {
                "type": "string",
                "description": (
                    "2-3 sentences: where this is and what makes it identifiable. Lead with the "
                    "conclusion, not with 'this report will…'."
                ),
            },
            "sections": {
                "type": "array",
                "description": (
                    "Ordered sections. Use ONLY the supplied material; do not introduce facts. "
                    "4-7 sections is typical."
                ),
                "items": {
                    "type": "object",
                    "required": ["key", "heading", "body"],
                    "properties": {
                        "key": {
                            "type": "string",
                            "enum": ["narrowing", "cues", "language", "landmarks", "culture",
                                     "economy", "tourism", "people", "neighbors", "residents",
                                     "knowledge", "caveats"],
                            "description": "ASCII enum — do NOT translate.",
                        },
                        "heading": {"type": "string"},
                        "body": {
                            "type": "string",
                            "description": "Prose. Reference knowledge atoms inline as [[atm_...]] when used.",
                        },
                        "atom_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "confidence_note": {
                "type": "string",
                "description": "One sentence on what is uncertain and why. Be plain about weak evidence.",
            },
        },
    },
}

_VERIFY_TOOL = {
    "name": "script_check",
    "description": "Narrowly check a drafted script against the material it was built from.",
    "input_schema": {
        "type": "object",
        "required": ["ok", "unsupported", "verdict"],
        "properties": {
            "ok": {"type": "boolean", "description": "True if the script may be used as-is."},
            "unsupported": {
                "type": "array",
                "description": "Claims present in the script but NOT in the material. Empty if none.",
                "items": {
                    "type": "object",
                    "required": ["section_key", "claim", "fix"],
                    "properties": {
                        "section_key": {"type": "string"},
                        "claim": {"type": "string"},
                        "fix": {"type": "string", "description": "Rewritten sentence using only the material, or '' to delete."},
                    },
                },
            },
            "verdict": {"type": "string", "description": "One sentence."},
        },
    },
}

_DRAFT_SYSTEM = (
    "You are an editor assembling a location report from material that has ALREADY been researched. "
    "You are not a researcher — you introduce no facts of your own.\n\n"
    "RULES\n"
    "- Every sentence must trace to the supplied material. If the material does not say it, do not write it.\n"
    "- Numbers, coordinates, ISO codes, confidence values and enum tokens are quoted verbatim from the "
    "material or omitted. Never round, restate or 'improve' them.\n"
    "- Lead with the conclusion. The first thing a reader wants is what place this is and how sharply "
    "it was pinned down.\n"
    "- The `narrowing` chain is the most valuable material — the discrimination from country down to "
    "district. Give it a section and preserve what each step ruled out.\n"
    "- Where a knowledge atom was reused, cite it inline as [[atm_id]] rather than restating its text.\n"
    "- Say plainly where the evidence is thin. A hedge that is honest beats a confident sentence that "
    "the material does not support.\n"
    "- Write for a reader who was not watching the analysis. Do not reference 'the cues table' or "
    "'step 3'; state the substance."
)

_VERIFY_SYSTEM = (
    "You are a fact-adherence checker. You are given source MATERIAL and a drafted SCRIPT written from it.\n\n"
    "Check exactly two things, nothing else:\n"
    "  1. Does the script assert anything the material does not support? (invented places, dates, "
    "numbers, causal claims, named people)\n"
    "  2. Did it preserve the discrimination logic — what was ruled out and why?\n\n"
    "Do NOT critique style, length, ordering or word choice. Do NOT rewrite anything that is supported. "
    "If the script is faithful, set ok=true with an empty list and stop — that is the expected outcome "
    "and costs nothing. Only list a claim when you can point to its absence in the material."
)


def _material(analysis: dict | None, profile: dict | None, atoms: list | None,
              place: str, lat, lng) -> dict:
    """모델에 넘길 재료 — 스크립트가 참조할 수 있는 것의 전부."""
    a = analysis or {}
    return {
        "place": place,
        "coordinates": {"lat": lat, "lng": lng},
        "best_guess": a.get("best_guess"),
        "coarse_filters": a.get("coarse_filters"),
        "narrowing": a.get("narrowing"),
        "cues": a.get("cues"),
        "languages_detected": a.get("languages_detected"),
        "landmarks": a.get("landmarks"),
        "cultural_economic_read": a.get("cultural_economic_read"),
        "research_profile": profile,
        "knowledge_atoms": [
            {"id": getattr(x, "id", None) or (x.get("id") if isinstance(x, dict) else None),
             "title": getattr(x, "title", None) or (x.get("title") if isinstance(x, dict) else None),
             "body": getattr(x, "body", None) or (x.get("body") if isinstance(x, dict) else None)}
            for x in (atoms or [])
        ],
    }


def _compact(obj, limit: int = 18000) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)[:limit]


def build_script(
    settings: Settings,
    *,
    analysis: dict | None,
    profile: dict | None,
    atoms: list | None,
    place: str,
    lat=None,
    lng=None,
    lang: str = "en",
    should_stop=None,
    on_progress=None,
) -> dict:
    """초안(빠른 모델) → 좁은 검증(opus) → 스크립트.

    반환 {status, script, cost_usd, checked, unsupported, drafted_by, verified_by}.
    어느 단계가 실패해도 status=SKIP 으로 돌려주고, 호출부는 스크립트 없이 기존 경로로 렌더한다.
    """
    from . import i18n

    if not settings.script_first:
        return {"status": "SKIP", "reason": "disabled"}
    mat = _material(analysis, profile, atoms, place, lat, lng)
    if not mat.get("best_guess"):
        return {"status": "SKIP", "reason": "no-analysis"}

    langname = i18n._CLAUDE.get(i18n.normalize(lang), "English")
    cost = 0.0

    # ── 1) 초안 — 빠른 모델 ──────────────────────────────────────
    if on_progress:
        on_progress("보고서 스크립트 초안 작성 중…")
    try:
        d = llm.call(
            settings,
            system=_DRAFT_SYSTEM + f"\n\nOUTPUT LANGUAGE: write every string value in {langname}.",
            messages=[{"role": "user", "content":
                       f"MATERIAL:\n{_compact(mat)}\n\nCall report_script once."}],
            tools=[_DRAFT_TOOL],
            tool_choice={"type": "tool", "name": "report_script"},
            max_tokens=settings.script_max_tokens,
            effort=settings.script_draft_effort,
            deadline_s=settings.draft_timeout_s,
            should_stop=should_stop,
            role="draft",
        )
    except llm.LLMCanceled:
        raise
    except Exception as exc:  # noqa: BLE001 — 스크립트는 부가 산출물이다
        return {"status": "SKIP", "reason": f"draft-failed: {exc}"}

    cost += llm.spend(d)
    script = llm.tool_input(d, "report_script")
    drafted_by = llm.used_model(d)
    if not script or not script.get("sections"):
        return {"status": "SKIP", "reason": "empty-draft", "cost_usd": round(cost, 4)}

    # ── 2) 검증 — opus, 좁게 ────────────────────────────────────
    checked = False
    unsupported: list = []
    verified_by = None
    if settings.script_verify:
        if should_stop and should_stop():
            raise llm.LLMCanceled("호출이 취소되었습니다.")
        if on_progress:
            on_progress("스크립트 사실 정합성 검증 중…")
        try:
            v = llm.call(
                settings,
                system=_VERIFY_SYSTEM,
                messages=[{"role": "user", "content":
                           f"MATERIAL:\n{_compact(mat)}\n\nSCRIPT:\n{_compact(script, 12000)}\n\n"
                           "Call script_check once."}],
                tools=[_VERIFY_TOOL],
                tool_choice={"type": "tool", "name": "script_check"},
                max_tokens=settings.script_verify_max_tokens,
                effort=settings.script_verify_effort,
                deadline_s=settings.verify_timeout_s,
                should_stop=should_stop,
                role="verify",
            )
            cost += llm.spend(v)
            verified_by = llm.used_model(v)
            res = llm.tool_input(v, "script_check") or {}
            checked = True
            unsupported = [u for u in (res.get("unsupported") or []) if isinstance(u, dict)]
            # 검증이 지적한 문장만 **국소적으로** 교체한다. 재작성 호출은 하지 않는다.
            if unsupported:
                by_key: dict[str, list] = {}
                for u in unsupported:
                    by_key.setdefault(str(u.get("section_key") or ""), []).append(u)
                for sec in script.get("sections") or []:
                    for u in by_key.get(str(sec.get("key") or ""), []):
                        claim, fix = str(u.get("claim") or ""), str(u.get("fix") or "")
                        if claim and claim in (sec.get("body") or ""):
                            sec["body"] = sec["body"].replace(claim, fix).strip()
        except llm.LLMCanceled:
            raise
        except Exception:  # noqa: BLE001 — 검증 실패는 치명적이지 않다(초안을 그대로 쓴다)
            pass

    script["_meta"] = {
        "schema": SCHEMA_VERSION,
        "lang": lang,
        "place": place,
        "draftedBy": drafted_by,
        "verifiedBy": verified_by,
        "checked": checked,
        "unsupportedFixed": len(unsupported),
        "created": time.time(),
    }
    return {
        "status": "OK",
        "script": script,
        "cost_usd": round(cost, 4),
        "checked": checked,
        "unsupported": unsupported,
        "drafted_by": drafted_by,
        "verified_by": verified_by,
    }


def save(settings: Settings, report_file: str, script: dict) -> str | None:
    """스크립트를 보고서 옆에 남긴다 — HTML 을 모델 없이 다시 만들 수 있게."""
    try:
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        out = settings.reports_dir / (Path(report_file).stem + ".script.json")
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(script, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(out)
        return out.name
    except Exception:  # noqa: BLE001
        return None


def load(settings: Settings, report_file: str) -> dict | None:
    p = settings.reports_dir / (Path(report_file).stem + ".script.json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
