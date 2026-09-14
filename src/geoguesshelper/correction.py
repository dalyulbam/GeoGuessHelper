"""자동 정정 루프 — 좌표를 가리고 판단(X) → 실측으로 정정(X2) → 판별자 원자 → 다음 판단을 유도.

기획(docs/plan/impl-spec_260907.md §0·§3, 사용자 결정 260907):

    실측 파노 좌표를 가리고 → "이 이미지는 X 위치이다"라고 판단하고 → 실측 파노를 보고
    "X 는 사실 X2 였다"의 수정 작업을 거친다. 특징 추출 → 우리가 축적한 atom 참조 →
    다음번에 X 가 아닌 X2 가 추측되도록 유도한다. **승인은 사람이 할 필요가 없다.**

잡 1건의 흐름
  1-1 blind 1패스  analyze_captures(로드뷰 패널 크롭, mode="blind")             — 회상 없음 = 순수 판단 X
  1-2 회상 + 2패스  recall(X 의 좌표·장소·후보 ISO) → 있으면 같은 이미지로 재판단  — 판별자가 X 를 X2 로 밀어내는 자리
  2   실측 공개     verdict = compare(final.best_guess, truth)                   — truth = pano 평균 좌표 + 파일명 ISO + aided 분석의 국가/지역/도시
  3   정정 호출     LLM(vision) 에 blind 이미지 + blind 사슬 + truth + aided 사슬의 **결론만** → 도구 `correction`
  4   적재·채점     knowledge.ingest_corrections(판별자) · knowledge.record_evidence(hits/misses)
  5   기록          docs/knowledge/corrections/corr_<job>.json · corrections.jsonl(시계열) · README.md(재생성)

원칙
  · 정답은 좌표가 안다 — 이 모듈은 나라를 다시 추측하지 않는다. 모델에게는 "왜 X 가 아니고 X2 인가"만 묻는다.
  · aided(지도 포함) 사슬의 **관찰**은 넘기지 않는다. 지도 라벨을 "보였다"고 옮겨 쓰는 것이 기준선 실험이
    잡아낸 오염(FINDINGS_260907 §3)이라, 정답 참고로는 level→conclusion 목록만 준다.
  · 사람 승인 관문 없음. 기계 산출은 kind=discriminator·origin=correction 표식을 달고 곧바로 원자가 되며,
    회상돼 쓰인 뒤 정답/오답에 기여했는지가 hits/misses 로 채점된다(오답만 뒷받침하면 retracted).
  · 이미지 블록은 세 호출(1패스·2패스·정정)에 **같은 순서·같은 base64·마지막에 cache_control** 로 넣는다.
    1패스→2패스는 tools/system 이 같아 캐시가 맞는다. 정정 호출은 도구가 다르므로(캐시는 tools→system→messages
    접두 일치) 1패스 캐시를 이어받지 못하지만, 깨진 도구 입력으로 재시도할 때는 같은 접두라 캐시가 먹는다.

실행
    uv run geoguesshelper correct run --dry-run        # 대상 잡·비용 추정만
    uv run geoguesshelper correct run --limit 1
    uv run geoguesshelper correct run --redo --job <id> # 유도 확인(직전에 만든 판별자가 2패스에 들어오는가)
    uv run geoguesshelper correct run                   # image_panos 있는 잡 전부(이미 OK 인 것은 건너뜀)
    uv run geoguesshelper correct report                # README 재생성(LLM 호출 없음)
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import baseline, knowledge
from .config import Settings, load_settings

_LEDGER_DIR = "corrections"
_JSONL = "corrections.jsonl"
_STATUS_OK = "OK"
_STATUS_PARTIAL = "PARTIAL"   # 판단(blind/aided/정정 호출)은 끝났지만 적재나 채점이 실패했다 — run() 이 재시도 대상으로 삼는다(260908)
_STATUS_API_ERROR = "API_ERROR"
# 연결 오류 뒤 물러섰다 다시 거는 간격(초). 지속적인 장애면 세 번 만에 포기하고 넘어간다 —
# 한 잡 때문에 배치 전체를 붙잡지 않는다.
_API_ERROR_BACKOFF_S = (5.0, 20.0, 60.0)
# 잡 1건 비용 추정(dry-run 용). 실측이 쌓이면 원장 평균으로 대체한다.
_EST_JOB_USD = 0.8


def _dir(settings: Settings) -> Path:
    return settings.knowledge_dir / _LEDGER_DIR


def _corr_path(settings: Settings, job_id: str) -> Path:
    return _dir(settings) / f"corr_{job_id}.json"


# ── 정답(truth) ───────────────────────────────────────────────────
def _truth_of(job: dict) -> dict:
    """baseline._truth(pano 평균 좌표·파일명 ISO) + aided 분석의 국가/지역/도시.

    국가/지역/도시 문자열은 aided_prior(원 보고서의 지도 포함 분석)에서 온다 — 지도를 본 분석이라
    장소 이름은 믿을 만하지만 **관찰**은 여기서 쓰지 않는다(source 를 명시해 뒤에서 구분한다).
    """
    t = baseline._truth(job)
    g = (job.get("aided_prior") or {}).get("best_guess") or {}
    iso = t.get("iso") or (str(g.get("country_iso") or "").upper()[:2] or None)
    return {
        "lat": t.get("lat"), "lng": t.get("lng"), "iso": iso,
        "country": g.get("country"), "region": g.get("region_or_state"), "city": g.get("city"),
        "source": "aided_prior", "file_lat": t.get("file_lat"), "file_lng": t.get("file_lng"),
        "n_panos": t.get("n_panos"),
    }


# ── 판정(verdict) ─────────────────────────────────────────────────
_GENERIC = {"region", "province", "prefecture", "district", "state", "municipality", "governorate",
            "department", "county", "city", "area", "metro", "north", "south", "east", "west",
            "central", "northern", "southern", "eastern", "western", "island", "islands"}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", " ", str(s or "").lower()).strip()


def _match_text(guess, truth) -> str:
    """지역/도시 비교 — 정답 쪽이 비면 unknown. 포함 관계 · difflib ≥ 0.6 · 고유 토큰(≥5자, 일반어 제외) 공유면 hit.

    정답 문자열은 'Northwestern Region (Norðurland vestra), Húnaþing vestra municipality' 처럼 길고 괄호가
    많다 — 추측이 그 일부('Norðurland vestra')만 말해도 맞은 것으로 본다.
    """
    if not truth:
        return "unknown"
    if not guess:
        return "miss"
    g, t = _norm(guess), _norm(truth)
    if not g or not t:
        return "miss" if g or t else "unknown"
    if g in t or t in g:
        return "hit"
    if difflib.SequenceMatcher(None, g, t).ratio() >= 0.6:
        return "hit"
    gt = {w for w in g.split() if len(w) >= 5 and w not in _GENERIC}
    tt = {w for w in t.split() if len(w) >= 5 and w not in _GENERIC}
    return "hit" if gt & tt else "miss"


def _bucket(err) -> str:
    if err is None:
        return "na"
    if err < 1:
        return "<1"
    if err < 10:
        return "<10"
    if err < 100:
        return "<100"
    return ">=100"


def compare(analysis: dict | None, truth: dict) -> dict:
    """blind 최종 판단 vs 실측. 국가는 ISO, 지역/도시는 문자열 유사도, 좌표는 haversine."""
    from .knowledge import haversine_km

    a = analysis or {}
    g = a.get("best_guess") or {}
    g_iso = str(g.get("country_iso") or "").upper()[:2]
    t_iso = str(truth.get("iso") or "").upper()[:2]
    if t_iso and g_iso:
        country = "hit" if g_iso == t_iso else "miss"
    elif truth.get("country"):
        country = _match_text(g.get("country"), truth.get("country"))
    else:
        country = "unknown"
    est = g.get("coordinate_estimate") or {}
    err = None
    if truth.get("lat") is not None and isinstance(est, dict) and est.get("lat") is not None:
        try:
            err = round(haversine_km(truth["lat"], truth["lng"], float(est["lat"]), float(est["lng"])), 2)
        except (TypeError, ValueError):
            err = None
    return {
        "country": country,
        "region": _match_text(g.get("region_or_state"), truth.get("region")),
        "city": _match_text(g.get("city"), truth.get("city")),
        "error_km": err, "bucket": _bucket(err),
    }


def _final_of(analysis: dict | None, truth: dict, source: str) -> dict:
    a = analysis or {}
    g = a.get("best_guess") or {}
    est = g.get("coordinate_estimate") if isinstance(g.get("coordinate_estimate"), dict) else {}
    m = baseline.metrics(a, truth)
    return {
        "country_iso": g.get("country_iso"), "country": g.get("country"),
        "region": g.get("region_or_state"), "city": g.get("city"),
        "lat": est.get("lat"), "lng": est.get("lng"),
        "error_km": m.get("error_km"), "reached_level": m.get("reached_level"),
        "confidence": g.get("confidence"), "from": source,
    }


# ── 회상(2패스 재료) ──────────────────────────────────────────────
def _isos_of(analysis: dict) -> list[str]:
    g = analysis.get("best_guess") or {}
    out = []
    for x in [g] + [x for x in (analysis.get("alternatives") or []) if isinstance(x, dict)]:
        iso = str(x.get("country_iso") or "").upper()[:2]
        if len(iso) == 2 and iso not in out:
            out.append(iso)
    return out


def _recall_for(settings: Settings, analysis: dict, job_id: str) -> list:
    """blind 1패스의 판단 X 를 열쇠로 회상한다.

    좌표는 X 의 coordinate_estimate(정답이 아니다 — 정답 좌표로 회상하면 정답을 흘리는 것이다).
    confusion_isos 에 X 와 대안들의 ISO 를 넣어 "X 처럼 보이지만 X2" 판별자가 지리 접점 없이도
    들어오게 한다(knowledge.recall 의 conf 경로). aided 계층 원자는 장면 분석에 싣지 않는다.
    """

    g = analysis.get("best_guess") or {}
    est = g.get("coordinate_estimate") if isinstance(g.get("coordinate_estimate"), dict) else {}
    lat, lng = est.get("lat"), est.get("lng")
    try:
        lat, lng = (float(lat), float(lng)) if lat is not None and lng is not None else (None, None)
    except (TypeError, ValueError):
        lat, lng = None, None
    tags = [c.get("category") for c in (analysis.get("cues") or []) if isinstance(c, dict) and c.get("category")]
    ents = [g.get("country"), g.get("region_or_state")]
    ents += [x.get("country") for x in (analysis.get("alternatives") or []) if isinstance(x, dict)]
    return knowledge.recall(
        settings, lat=lat, lng=lng,
        place={"country": g.get("country"), "region_or_state": g.get("region_or_state"), "city": g.get("city")},
        tags=[t for t in tags if t], entities=[e for e in ents if e],
        confusion_isos=_isos_of(analysis), exclude_tiers=("aided",),
        limit=settings.correction_recall_limit, char_budget=settings.knowledge_recall_chars,
        ctx={"job": job_id, "mode": "blind-p2"},
    )


def _recall_paths(atom, isos: set[str], ents: set[str], cell: str) -> list[str]:
    """이 원자가 어느 경로(conf/ent/geo)로 들어왔는지 — README 의 '유도 확인' 이 근거를 적기 위해."""
    from .knowledge import _SCOPE_PRECISION, slug

    via = []
    if any(str(c).split(">")[0].upper() in isos for c in (getattr(atom, "confusions", None) or [])):
        via.append("conf")
    if {slug(e) for e in atom.entities} & ents:
        via.append("ent")
    prec = _SCOPE_PRECISION.get(atom.scope, 5)
    if prec and cell and atom.cell and cell[:prec] == str(atom.cell)[:prec]:
        via.append("geo")
    return via


# ── 정정 호출 ─────────────────────────────────────────────────────
def _tool_schema() -> dict:
    from .knowledge import ALL_CATEGORIES, LAYERS

    cats = sorted(ALL_CATEGORIES)
    place = {
        "type": "object", "required": ["country_iso", "country"],
        "properties": {
            "country_iso": {"type": "string", "description": "ISO 3166-1 alpha-2, e.g. NO."},
            "country": {"type": "string"},
            "region": {"type": ["string", "null"]},
        },
    }
    return {
        "name": "correction",
        "description": ("Record the correction of one blind GeoGuessr judgement against ground truth: what misled, "
                        "what was decisive, and transferable 'looks like X but is actually Y' discriminators."),
        "input_schema": {
            "type": "object",
            "required": ["summary", "misleading_cues", "decisive_cues", "discriminators",
                         "misled_atoms", "confirming_atoms", "retract_candidates"],
            "properties": {
                "summary": {"type": "string", "description": (
                    "2-4 English sentences. Miss: 'X was actually X2 because …'. "
                    "Hit: 'X confirmed; the alternatives A, B were correctly excluded because …'.")},
                "misleading_cues": {"type": "array", "items": {
                    "type": "object", "required": ["observation", "why_misleading"],
                    "properties": {"observation": {"type": "string"}, "why_misleading": {"type": "string"},
                                   "image_index": {"type": ["integer", "null"]}}}},
                "decisive_cues": {"type": "array", "description": (
                    "Cues that point to the TRUTH and are actually visible in the blind images."), "items": {
                    "type": "object", "required": ["observation", "why_decisive", "category"],
                    "properties": {"observation": {"type": "string"}, "why_decisive": {"type": "string"},
                                   "category": {"type": "string", "enum": cats},
                                   "image_index": {"type": ["integer", "null"]}}}},
                "discriminators": {"type": "array", "description": (
                    "Counterfactual, transferable patterns only: 'in <actually> you see A; in <looks_like> you would "
                    "see B instead'. Exclude anything true only of this one street."), "items": {
                    "type": "object",
                    "required": ["title", "body", "layer", "category", "scope", "tags", "entities",
                                 "looks_like", "actually", "confidence"],
                    "properties": {
                        "title": {"type": "string", "description": "<= 100 chars, English."},
                        "body": {"type": "string", "description": "2-4 English sentences with the 'instead' clause."},
                        "layer": {"type": "string", "enum": list(LAYERS)},
                        "category": {"type": "string", "enum": cats},
                        "scope": {"type": "string", "enum": ["country", "region", "city", "point"]},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "entities": {"type": "array", "items": {"type": "string"},
                                     "description": "Both candidates (countries/regions) as names."},
                        "looks_like": place, "actually": place,
                        "image_index": {"type": ["integer", "null"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    }}},
                "misled_atoms": {"type": "array", "items": {"type": "string"},
                                 "description": "KNOWN ATOMS ids that pushed the analyst toward a wrong conclusion."},
                "confirming_atoms": {"type": "array", "items": {"type": "string"},
                                     "description": "KNOWN ATOMS ids that correctly supported the truth."},
                "retract_candidates": {"type": "array", "items": {
                    "type": "object", "required": ["atom_id", "reason"],
                    "properties": {"atom_id": {"type": "string"}, "reason": {"type": "string"}}},
                    "description": "KNOWN ATOMS that are wrong as stated (not merely inapplicable)."},
            },
        },
    }


_CORR_SYSTEM = """You audit one BLIND GeoGuessr judgement against ground truth and turn the gap into transferable
knowledge. You see the same street-view images the analyst saw (NO map panel). You are given the analyst's
chain and best guess, the GROUND TRUTH (pano coordinates and the country/region/city from a map-aided
analysis) with the verdict, and the KNOWN ATOMS that were offered to the analyst (and which it relied on).

Call the `correction` tool exactly once.

RULES
- English only.
- Evidence is what is ACTUALLY VISIBLE in these images. Never write that something "is visible" or "was
  seen" if you only know it from the ground-truth coordinates, the place name, or map knowledge. If the
  truth cannot be seen in the images, say so in `summary` rather than inventing a cue.
- Faces and licence-plate characters are blurred: never identify a person, never guess plate text.
  Plate shape and colour are allowed as cues.
- Discriminators are COUNTERFACTUAL and TRANSFERABLE: "in <actually> you see A; in <looks_like> you would
  see B instead". Exclude anything true only of this one street (a shop name, a house number, one sign's
  wording). Prefer utility-pole hardware, bollards/guardrails, road markings, sign shapes and colours,
  plate shape, roof/facade typology, vegetation, soil, script/toponymy patterns.
  · Verdict MISS: looks_like = the wrong best guess, actually = the truth.
  · Verdict HIT: looks_like = an alternative the analyst could not fully exclude, actually = the truth;
    the body states why the alternative was correctly excluded.
- `misled_atoms`, `confirming_atoms`, `retract_candidates.atom_id` must be ids from the KNOWN ATOMS list.
  An atom that simply did not apply is neither misled nor confirming — leave it out.
- Vocabulary is closed: layer, category and scope only from the enums. scope=point only for a cue tied to
  one image at one place."""


def _chain_text(a: dict) -> str:
    g = a.get("best_guess") or {}
    est = g.get("coordinate_estimate") if isinstance(g.get("coordinate_estimate"), dict) else {}
    lines = [f"best_guess: {g.get('country')} ({g.get('country_iso')}) / {g.get('region_or_state')} / "
             f"{g.get('city')} · confidence {g.get('confidence')} · coordinate_estimate "
             f"{est.get('lat')}, {est.get('lng')} (radius {est.get('radius_km')} km)"]
    alts = [x for x in (a.get("alternatives") or []) if isinstance(x, dict)]
    if alts:
        lines.append("alternatives: " + "; ".join(
            f"{x.get('country')} ({x.get('country_iso')}) {x.get('confidence')}" for x in alts))
    lines.append("narrowing:")
    for s in (a.get("narrowing") or []):
        if isinstance(s, dict):
            lines.append(f"- [{s.get('level')}] Q: {s.get('question')} | obs: {s.get('observation')} | "
                         f"disc: {s.get('discriminator')} | ruled_out: {', '.join(map(str, s.get('ruled_out') or []))} "
                         f"| => {s.get('conclusion')} (conf {s.get('confidence_after')})")
    lines.append("cues:")
    for c in (a.get("cues") or []):
        if isinstance(c, dict):
            lines.append(f"- (img {c.get('image_index')}) [{c.get('category')}/{c.get('level')}] "
                         f"{c.get('observation')} (w {c.get('weight')})")
    if a.get("relied_on_atoms"):
        lines.append(f"relied_on_atoms: {', '.join(map(str, a['relied_on_atoms']))}")
    if a.get("revised_from"):
        lines.append(f"revised_from: {a['revised_from']}")
    return "\n".join(lines)


def _aided_conclusions(aided: dict | None) -> list[str]:
    """aided 사슬에서 level→conclusion 만. 관찰·판별자는 지도 라벨을 인용했을 수 있어 넘기지 않는다."""
    out = []
    for s in ((aided or {}).get("narrowing") or []):
        if isinstance(s, dict) and s.get("conclusion"):
            out.append(f"- {s.get('level')}: {s.get('conclusion')}")
    return out


def _known_text(known: list, relied: list[str]) -> str:
    if not known:
        return "KNOWN ATOMS offered to the analyst: (none — the 2nd pass was skipped)"
    rel = set(relied or [])
    rows = ["KNOWN ATOMS offered to the analyst in the 2nd pass ([RELIED] = the analyst listed it in relied_on_atoms):"]
    for a in known:
        conf = ",".join(a.confusions[:3]) if getattr(a, "confusions", None) else ""
        rows.append(f"- [[{a.id}]]{' [RELIED]' if a.id in rel else ''} ({a.layer}/{a.scope}"
                    f"{'/' + conf if conf else ''}) {a.title} — {a.body[:400]}")
    return "\n".join(rows)


def _user_text(*, final: dict, phase1: dict, phase2: dict | None, truth: dict, verdict: dict,
               aided: dict | None, known: list, relied: list[str], max_atoms: int) -> str:
    parts = []
    if phase2 is not None:
        parts.append("BLIND JUDGEMENT — FINAL (2nd pass, street-view panel only, KNOWN ATOMS offered):\n"
                     + _chain_text(final))
        g1 = (phase1.get("analysis") or {}).get("best_guess") or {}
        gf = final.get("best_guess") or {}
        if (g1.get("country_iso"), g1.get("region_or_state"), g1.get("city")) != \
                (gf.get("country_iso"), gf.get("region_or_state"), gf.get("city")):
            parts.append("BLIND 1st PASS (before KNOWN ATOMS) differed — best_guess: "
                         f"{g1.get('country')} ({g1.get('country_iso')}) / {g1.get('region_or_state')} / {g1.get('city')}")
    else:
        parts.append("BLIND JUDGEMENT (street-view panel only, no KNOWN ATOMS):\n" + _chain_text(final))
    parts.append(_known_text(known, relied))
    parts.append("GROUND TRUTH (never shown to the analyst): "
                 f"iso {truth.get('iso')} · country {truth.get('country')} · region {truth.get('region')} · "
                 f"city {truth.get('city')} · lat {truth.get('lat')} · lng {truth.get('lng')}")
    concl = _aided_conclusions(aided)
    if concl:
        parts.append("TRUTH-SIDE CHAIN — conclusions only (from a map-aided analysis; its observations are withheld "
                     "because they may quote map labels — do NOT treat these as things visible in the images):\n"
                     + "\n".join(concl))
    parts.append(f"VERDICT: country {verdict.get('country')} · region {verdict.get('region')} · city {verdict.get('city')} "
                 f"· error {verdict.get('error_km')} km (bucket {verdict.get('bucket')})")
    parts.append(f"Call `correction` once. At most {max_atoms} discriminators; fewer, sharper ones are better.")
    return "\n\n".join(parts)


_ARRAY_FIELDS = ("misleading_cues", "decisive_cues", "discriminators", "misled_atoms", "confirming_atoms",
                 "retract_candidates")
# sonnet 류의 XML 누출(molecule.py 실측 2026-09-07): 배열이 문자열로 오거나 '<parameter name=…>' 꼬리가 딸려 온다.
_CORRUPT_RE = re.compile(
    r"<(?:value|item|!\[CDATA\[)|</?parameter\b|</?(?:summary|misleading_cues|decisive_cues|discriminators|"
    r"misled_atoms|confirming_atoms|retract_candidates)\b"
)


def _strings(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _strings(x)]
    if isinstance(v, (list, tuple)):
        return [s for x in v for s in _strings(x)]
    return []


def is_corrupt(data: dict | None) -> bool:
    """도구 입력이 XML 누출 형태로 깨졌는가 — 배열 필드가 문자열, 또는 어디든 <parameter>/<value> 마커."""
    if not data or not isinstance(data, dict):
        return True
    for k in _ARRAY_FIELDS:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return True
    return any(_CORRUPT_RE.search(s) for s in _strings(data))


def _rescue(data: dict) -> dict:
    """재시도 뒤에도 깨져 있으면 살릴 수 있는 만큼만 살린다(parse_rescued=True 표식).

    문자열은 '<parameter' 앞까지만 남기고 닫는 태그를 지운다. 배열이어야 할 문자열은 JSON 으로 읽어 보고,
    안 되면 빈 배열 — 억지로 쪼개 만든 판별자는 원자로 굳으면 안 되기 때문이다.
    """
    def clean_str(s: str) -> str:
        s = re.split(r"<parameter\s+name=", s, maxsplit=1)[0]
        # 필드 이름 태그('</summary>' 등)도 함께 걷어낸다 — 누출은 다음 필드의 여는 태그뿐 아니라 자기 필드의 닫는 태그로도 온다.
        return re.sub(r"</?(?:parameter|value|item|summary|misleading_cues|decisive_cues|discriminators|misled_atoms|"
                      r"confirming_atoms|retract_candidates)\b[^>]*>|<!\[CDATA\[|\]\]>", "", s).strip()

    def walk(v):
        if isinstance(v, str):
            return clean_str(v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    out = dict(data)
    for k in _ARRAY_FIELDS:
        v = out.get(k)
        if isinstance(v, str):
            try:
                parsed = json.loads(clean_str(v))
                out[k] = parsed if isinstance(parsed, list) else []
            except ValueError:
                out[k] = []
    out = walk(out)
    out["parse_rescued"] = True
    return out


def _sanitize(corr: dict, offered: set[str], max_atoms: int) -> dict:
    """도구 출력 정리 — 원자 id 는 offered 안의 것만, 배열은 dict 항목만, 판별자는 상한까지."""
    c = dict(corr)
    for k in ("misled_atoms", "confirming_atoms"):
        c[k] = [x for x in dict.fromkeys(c.get(k) or []) if isinstance(x, str) and x in offered]
    c["retract_candidates"] = [x for x in (c.get("retract_candidates") or [])
                               if isinstance(x, dict) and x.get("atom_id") in offered]
    for k in ("misleading_cues", "decisive_cues", "discriminators"):
        c[k] = [x for x in (c.get(k) or []) if isinstance(x, dict)]
    c["discriminators"] = c["discriminators"][:max_atoms]
    c["summary"] = str(c.get("summary") or "").strip()
    return c


def _correction_call(settings: Settings, image_blocks: list[dict], text: str, *, log=print) -> dict:
    """정정 호출 1회(+깨졌을 때 1회 재시도). 반환 {…도구 출력, cost_usd, model, retries, error?}."""
    from . import llm

    tool = _tool_schema()
    content = list(image_blocks) + [{"type": "text", "text": text}]
    out: dict[str, Any] = {"cost_usd": 0.0, "model": None, "retries": 0}
    last_err = None
    data = None
    for attempt in range(2):
        try:
            resp = llm.call(
                settings, system=_CORR_SYSTEM,
                messages=[{"role": "user", "content": content}],
                tools=[tool], tool_choice={"type": "tool", "name": "correction"},
                max_tokens=8000, effort=settings.correction_effort,
                deadline_s=settings.correction_timeout_s, role="vision",
            )
        except llm.LLMUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            out["retries"] = attempt
            log(f"    정정 호출 실패({attempt + 1}/2): {last_err}")
            continue
        out["cost_usd"] += llm.spend(resp)
        out["model"] = llm.used_model(resp)
        out["cache"] = llm.cache_stats(getattr(resp, "usage", None))
        data = llm.tool_input(resp, "correction")
        if data and not is_corrupt(data):
            out["retries"] = attempt
            break
        last_err = "corrupt tool input (XML leakage / array as string)" if data else "no correction tool_use"
        out["retries"] = attempt + 1
        log(f"    정정 도구 입력 이상({attempt + 1}/2): {last_err} — 재시도")
    if data is None:
        out["error"] = last_err or "no response"
        return out
    if is_corrupt(data):
        data = _rescue(data)
        out["error"] = last_err
    out.update({k: v for k, v in data.items()})
    return out


# ── 잡 1건 ────────────────────────────────────────────────────────
def _status_of(res: dict) -> str:
    st = res.get("status") or "API_ERROR"
    return st if st in ("OK", "NO_KEY", "API_ERROR", "NO_IMAGES") else "API_ERROR"


def _phase_record(res: dict, truth: dict) -> dict:
    a = res.get("analysis") or {}
    return {"status": res.get("status"), "analysis": a, "cost_usd": res.get("cost_usd") or 0.0,
            "model": res.get("used_model"), "cache": res.get("cache"), "metrics": baseline.metrics(a, truth)}


def run_job(job: dict, settings: Settings, *, lang: str = "en", phase2: bool = True, log=print) -> dict:
    """잡 1건: blind 판단 → 실측 대조 → 정정 호출 → 판별자 적재·채점 → 기록. 항상 corr_<job>.json 을 쓴다."""
    from .analyze import _b64, analyze_captures

    job_id = job["job_id"]
    truth = _truth_of(job)
    out_dir = _dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    prev = None
    if _corr_path(settings, job_id).exists():
        try:
            prev = json.loads(_corr_path(settings, job_id).read_text(encoding="utf-8"))
        except ValueError:
            prev = None

    rec: dict[str, Any] = {
        "job_id": job_id, "report_file": job.get("report_file"), "label": job.get("label"),
        "created": time.time(), "lang": lang, "images": [], "image_panos": {},
        "truth": truth, "blind": {"phase1": None, "phase2": None, "final": None},
        "verdict": None, "correction": None, "atoms": {"created": [], "merged": []},
        "evidence": {"hits": [], "misses": [], "retracted": [], "restored": []},
        "cost_usd": 0.0, "status": _STATUS_OK,
    }

    def finish(status: str, message: str | None = None) -> dict:
        rec["status"] = status
        if message:
            rec["message"] = message
        rec["cost_usd"] = round(float(rec["cost_usd"]), 4)
        _write_record(settings, rec)
        _append_ledger(settings, rec)
        return rec

    originals = [settings.captures_dir / Path(n).name for n in job.get("images") or []]
    originals = [p for p in originals if p.exists()][: settings.max_analyze_images]
    if not originals:
        log(f"  [{job_id}] 캡처 없음")
        return finish("NO_IMAGES", "캡처 이미지가 없습니다.")

    # PARTIAL 재개 — 판단(blind 1·2패스, 판정, 정정 호출)은 이전 기록을 그대로 쓰고 실패한 4단계(적재·채점)만
    # 다시 한다. 전체를 다시 돌리면 LLM 비용을 또 쓰고, 이미 병합된 원자의 hits/uses 가 한 번 더 오른다(codex 2차 260908).
    if _resumable(prev):
        for k in ("images", "image_panos", "blind", "verdict", "correction", "induction"):
            if k in prev:
                rec[k] = prev[k]
        rec["resumed_from"] = prev.get("created")
        rec["cost_usd"] = 0.0          # 이번 시도의 비용 — 판단 비용은 이전 기록의 원장 행에 이미 있다
        log(f"  [{job_id}] PARTIAL 재개 — 판단은 이전 결과 재사용, 적재·채점만 다시")
        return _ingest_and_score(settings, job, job_id, rec, truth, prev, finish, log)
    blinds = [baseline.blind_crop(p, settings.captures_dir / "blind", settings)[0] for p in originals]
    rec["images"] = [p.name for p in originals]
    rec["image_panos"] = {Path(k).name: v for k, v in (job.get("image_panos") or {}).items()
                          if Path(k).name in set(rec["images"])}

    # ── 1-1 blind 1패스(회상 없음) ────────────────────────────────
    log(f"  [{job_id}] blind 1패스 {len(blinds)}장 (정답 {truth.get('iso')} {truth.get('city') or truth.get('region') or ''})")
    r1 = analyze_captures(blinds, settings, lang, mode="blind")
    rec["blind"]["phase1"] = _phase_record(r1, truth)
    rec["cost_usd"] += r1.get("cost_usd") or 0.0
    if r1.get("status") != "OK" or not r1.get("analysis"):
        log(f"    1패스 실패: {r1.get('status')} {r1.get('message') or ''}")
        return finish(_status_of(r1), r1.get("message") or r1.get("status"))
    a1 = r1["analysis"]
    g1 = a1.get("best_guess") or {}
    log(f"    X = {g1.get('country')} ({g1.get('country_iso')}) / {g1.get('region_or_state')} / {g1.get('city')}")

    # ── 1-2 회상 → 2패스 ─────────────────────────────────────────
    known: list = []
    relied: list[str] = []
    final_a, final_from = a1, "phase1"
    if phase2:
        try:
            known = _recall_for(settings, a1, job_id)
        except Exception as exc:  # noqa: BLE001 — 회상 실패가 정정을 막으면 안 된다
            log(f"    회상 실패(2패스 생략): {type(exc).__name__}: {exc}")
            known = []
        if known:
            log(f"    회상 {len(known)}건 → 2패스")
            r2 = analyze_captures(blinds, settings, lang, mode="blind", known=known)
            rec["cost_usd"] += r2.get("cost_usd") or 0.0
            offered = list(r2.get("known_offered") or [a.id for a in known])
            a2 = r2.get("analysis") or {}
            relied = [i for i in (a2.get("relied_on_atoms") or []) if isinstance(i, str) and i in set(offered)]
            p2 = _phase_record(r2, truth)
            p2.update({"known_offered": offered, "relied_on": relied, "revised_from": a2.get("revised_from") or None})
            rec["blind"]["phase2"] = p2
            if r2.get("status") == "OK" and a2:
                final_a, final_from = a2, "phase2"
                g2 = a2.get("best_guess") or {}
                log(f"    X' = {g2.get('country')} ({g2.get('country_iso')}) / {g2.get('region_or_state')} / "
                    f"{g2.get('city')} · relied {len(relied)}" + (f" · revised_from {a2.get('revised_from')}" if a2.get("revised_from") else ""))
            else:
                log(f"    2패스 실패({r2.get('status')}) — 1패스 결과를 최종으로")
        else:
            log("    회상 0건 — 2패스 생략")
    rec["blind"]["final"] = _final_of(final_a, truth, final_from)

    # 유도 확인 — 직전 실행이 만든 판별자가 이번 2패스에 실제로 들어왔는가(redo 의 검증 근거).
    if prev and isinstance(prev.get("atoms"), dict):
        prior = [i for i in (prev["atoms"].get("created") or []) + (prev["atoms"].get("merged") or []) if isinstance(i, str)]
        offered_set = set((rec["blind"]["phase2"] or {}).get("known_offered") or [])
        isos = set(_isos_of(a1))
        ents = {knowledge.slug(e) for e in [g1.get("country"), g1.get("region_or_state"), g1.get("city")]
                + [x.get("country") for x in (a1.get("alternatives") or []) if isinstance(x, dict)] if e}
        est = g1.get("coordinate_estimate") if isinstance(g1.get("coordinate_estimate"), dict) else {}
        cell = knowledge.geohash(est.get("lat"), est.get("lng"), 7) if est.get("lat") is not None else ""
        by_id = {a.id: a for a in known}
        rec["induction"] = {
            "prior_run": prev.get("created"), "prior_atoms": prior,
            "offered": [i for i in prior if i in offered_set],
            "relied": [i for i in prior if i in set(relied)],
            "via": {i: _recall_paths(by_id[i], isos, ents, cell) for i in prior if i in by_id},
        }
        log(f"    유도: 직전 판별자 {len(prior)} → offered {len(rec['induction']['offered'])} · relied {len(rec['induction']['relied'])}")

    # ── 2 실측 공개 ───────────────────────────────────────────────
    verdict = compare(final_a, truth)
    rec["verdict"] = verdict
    log(f"    판정: 국가 {verdict['country']} · 지역 {verdict['region']} · 도시 {verdict['city']} · 오차 {verdict['error_km']} km")

    # ── 3 정정 호출 — 같은 이미지 블록(같은 순서·마지막에 cache_control) ─────────
    blocks: list[dict] = []
    for i, p in enumerate(blinds):
        data, media = _b64(p)
        b: dict = {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
        if i == len(blinds) - 1:
            b["cache_control"] = {"type": "ephemeral"}
        blocks.append(b)
    text = _user_text(final=final_a, phase1=rec["blind"]["phase1"], phase2=rec["blind"]["phase2"], truth=truth,
                      verdict=verdict, aided=job.get("aided_prior"), known=known, relied=relied,
                      max_atoms=settings.correction_max_atoms)
    try:
        corr = _correction_call(settings, blocks, text, log=log)
    except Exception as exc:  # noqa: BLE001 — LLMUnavailable 포함
        from . import llm as _llm

        st = "NO_KEY" if isinstance(exc, _llm.LLMUnavailable) else "API_ERROR"
        rec["correction"] = {"error": f"{type(exc).__name__}: {exc}", "cost_usd": 0.0, "model": None, "retries": 0}
        return finish(st, str(exc))
    rec["cost_usd"] += corr.get("cost_usd") or 0.0
    if "summary" not in corr:
        rec["correction"] = corr
        return finish("API_ERROR", corr.get("error") or "정정 호출이 도구 출력을 내지 않았습니다.")
    offered_set = set((rec["blind"]["phase2"] or {}).get("known_offered") or [])
    corr = _sanitize(corr, offered_set, settings.correction_max_atoms)
    rec["correction"] = corr
    log(f"    정정: 판별자 {len(corr['discriminators'])} · misled {len(corr['misled_atoms'])} · "
        f"confirming {len(corr['confirming_atoms'])} · retract {len(corr['retract_candidates'])} · ${corr.get('cost_usd', 0):.3f}"
        + (" · rescued" if corr.get("parse_rescued") else ""))

    # ── 4 적재 · 채점 ─────────────────────────────────────────────
    return _ingest_and_score(settings, job, job_id, rec, truth, prev, finish, log)


def _resumable(prev: dict | None) -> bool:
    """이전 기록이 PARTIAL 이고 판단(blind final·verdict·정정 호출)이 온전하면 4단계만 재개할 수 있다."""
    if not isinstance(prev, dict) or prev.get("status") != _STATUS_PARTIAL:
        return False
    corr = prev.get("correction")
    blind = prev.get("blind") if isinstance(prev.get("blind"), dict) else {}
    return isinstance(corr, dict) and "summary" in corr and bool(prev.get("verdict")) and bool(blind.get("final"))


def _ingest_and_score(settings: Settings, job: dict, job_id: str, rec: dict, truth: dict, prev: dict | None,
                      finish, log) -> dict:
    """4단계 — 판별자 적재 + 증거 채점. 재개(rec['resumed_from'])면 이전에 적재가 성공했을 때 그 결과를 재사용해
    hits/uses 중복 가산을 막고, 채점은 knowledge.record_evidence 의 잡 단위 멱등성(evidence_log)에 기댄다."""
    corr = rec["correction"]
    offered_set = set(((rec.get("blind") or {}).get("phase2") or {}).get("known_offered") or [])
    prev_atoms = prev.get("atoms") if isinstance(prev, dict) and isinstance(prev.get("atoms"), dict) else None
    reuse = bool(rec.get("resumed_from")) and prev_atoms is not None and not prev_atoms.get("error") \
        and bool(prev_atoms.get("ordered") or prev_atoms.get("created") or prev_atoms.get("merged"))
    if reuse:
        rec["atoms"] = {k: list(prev_atoms.get(k) or []) for k in ("created", "merged", "ordered")}
        rec["atoms"]["reused"] = True
        log("    원자: 이전 적재 결과 재사용(재적재 없음 — hits/uses 중복 가산 방지)")
    else:
        try:
            before = set((knowledge.store_for(settings).index().get("atoms") or {}).keys())
            ing = knowledge.ingest_corrections(
                settings, discriminators=corr["discriminators"],
                truth={**truth, "country": truth.get("country"), "region": truth.get("region"), "city": truth.get("city")},
                images=rec["images"], image_panos=rec["image_panos"], report_file=job.get("report_file") or "",
                ctx={"job": job_id},
            )
            atoms = ing.get("atoms") or []
            # ordered = discriminators[] 와 같은 순서의 원자 id(README 가 판별자 ↔ 원자를 짝지을 때 쓴다).
            rec["atoms"] = {"created": [a.id for a in atoms if a.id not in before],
                            "merged": [a.id for a in atoms if a.id in before],
                            "ordered": [a.id for a in atoms]}
            log(f"    원자: {ing.get('created', 0)} 신규 · {ing.get('merged', 0)} 병합")
        except Exception as exc:  # noqa: BLE001
            rec["atoms"] = {"created": [], "merged": [], "error": f"{type(exc).__name__}: {exc}"}
            log(f"    적재 실패: {exc}")
    try:
        hits = [i for i in corr["confirming_atoms"] if i in offered_set]
        misses = [i for i in dict.fromkeys(corr["misled_atoms"] + [x["atom_id"] for x in corr["retract_candidates"]])
                  if i in offered_set]
        ev = knowledge.record_evidence(settings, hits=hits, misses=misses, ctx={"job": job_id},
                                       note=(corr.get("summary") or "")[:200]) if (hits or misses) else {}
        rec["evidence"] = {"hits": hits, "misses": misses,
                           "retracted": ev.get("retracted") or [], "restored": ev.get("restored") or [],
                           "skipped_idempotent": [s["atom"] for s in (ev.get("skipped") or [])]}
        if hits or misses:
            log(f"    채점: hits {len(hits)} · misses {len(misses)} · retracted {len(rec['evidence']['retracted'])}"
                + (f" · 멱등 건너뜀 {len(rec['evidence']['skipped_idempotent'])}" if rec["evidence"]["skipped_idempotent"] else ""))
    except Exception as exc:  # noqa: BLE001
        rec["evidence"]["error"] = f"{type(exc).__name__}: {exc}"
    # 판단(1~3단계)이 끝나도 적재·채점(4단계)이 실패하면 OK 로 고정하지 않는다 — OK 는 run() 의 재시도 제외
    # 조건이라, 실패가 OK 로 저장되면 그 잡은 두 번 다시 시도되지 않는다(codex 감사 260908). 재시도는 위 재개 경로.
    if rec["atoms"].get("error") or rec["evidence"].get("error"):
        return finish(_STATUS_PARTIAL, "판단은 끝났으나 원자 적재 또는 증거 채점이 실패했다 — 재시도 대상(재개 경로).")
    return finish(_STATUS_OK)


# ── 기록 ─────────────────────────────────────────────────────────
def _write_record(settings: Settings, rec: dict) -> Path:
    p = _corr_path(settings, rec["job_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)
    return p


def _ledger_row(rec: dict) -> dict:
    fin = (rec.get("blind") or {}).get("final") or {}
    p2 = (rec.get("blind") or {}).get("phase2")
    v = rec.get("verdict") or {}
    corr = rec.get("correction") or {}
    ev = rec.get("evidence") or {}
    atoms = rec.get("atoms") or {}
    return {
        "at": rec.get("created"), "job_id": rec.get("job_id"), "report_file": rec.get("report_file"),
        "truth_iso": (rec.get("truth") or {}).get("iso"), "blind_iso": fin.get("country_iso"),
        "blind_country": fin.get("country"), "truth_country": (rec.get("truth") or {}).get("country"),
        "country_hit": v.get("country"), "region_hit": v.get("region"), "city_hit": v.get("city"),
        "error_km": v.get("error_km"), "bucket": v.get("bucket"),
        "phase2_used": bool(p2), "n_offered": len((p2 or {}).get("known_offered") or []),
        "n_relied": len((p2 or {}).get("relied_on") or []),
        "revised": bool((p2 or {}).get("revised_from")),
        "n_discriminators": len(corr.get("discriminators") or []) if isinstance(corr, dict) else 0,
        "atoms_created": len(atoms.get("created") or []), "atoms_merged": len(atoms.get("merged") or []),
        "hits": len(ev.get("hits") or []), "misses": len(ev.get("misses") or []),
        "retracted": len(ev.get("retracted") or []),
        "cost_usd": rec.get("cost_usd"), "status": rec.get("status"),
    }


def _append_ledger(settings: Settings, rec: dict) -> None:
    """corrections.jsonl 은 시계열이다 — 같은 잡을 다시 돌려도 줄이 추가된다(학습 곡선의 재료)."""
    p = _dir(settings) / _JSONL
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_ledger_row(rec), ensure_ascii=False, default=str) + "\n")


def _load_records(settings: Settings) -> list[dict]:
    out = []
    d = _dir(settings)
    for p in sorted(d.glob("corr_*.json")) if d.exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    out.sort(key=lambda r: float(r.get("created") or 0))
    return out


def _load_ledger(settings: Settings) -> list[dict]:
    p = _dir(settings) / _JSONL
    rows = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _rate(rows: list[dict], key: str) -> str:
    hit = sum(1 for r in rows if r.get(key) == "hit")
    miss = sum(1 for r in rows if r.get(key) == "miss")
    return f"{hit}/{hit + miss}" + (f" ({hit / (hit + miss):.0%})" if hit + miss else "")


def _readme(settings: Settings, recs: list[dict], ledger: list[dict]) -> str:
    ok = [r for r in recs if r.get("status") == _STATUS_OK]
    verdicts = [r.get("verdict") or {} for r in ok]
    errs = sorted(v["error_km"] for v in verdicts if isinstance(v.get("error_km"), (int, float)))
    med = errs[len(errs) // 2] if errs else None
    p2_used = sum(1 for r in ok if (r.get("blind") or {}).get("phase2"))
    revised = sum(1 for r in ok if ((r.get("blind") or {}).get("phase2") or {}).get("revised_from"))
    created = sum(len((r.get("atoms") or {}).get("created") or []) for r in ok)
    merged = sum(len((r.get("atoms") or {}).get("merged") or []) for r in ok)
    retracted = sum(len((r.get("evidence") or {}).get("retracted") or []) for r in ok)
    total = sum(float(r.get("cost_usd") or 0) for r in recs)
    total_ledger = sum(float(r.get("cost_usd") or 0) for r in ledger)
    idx = knowledge.store_for(settings).index()
    n_disc = sum(1 for m in (idx.get("atoms") or {}).values() if m.get("kind") == "discriminator")
    n_retracted_now = sum(1 for m in (idx.get("atoms") or {}).values() if m.get("status") == "retracted")

    ts = lambda t: time.strftime("%m-%d %H:%M", time.localtime(float(t))) if t else "—"  # noqa: E731
    lines = [
        "# 자동 정정 루프 — 정정 원장",
        "",
        f"생성 {time.strftime('%Y-%m-%d %H:%M')} · 잡 {len(recs)}건(최신 기록, 그중 OK {len(ok)}) · 실행 {len(ledger)}회(corrections.jsonl) · "
        f"이번 기록 비용 ${total:.3f} · 누적 실행 비용 ${total_ledger:.3f} · 기획: docs/plan/impl-spec_260907.md §3",
        "",
        "같은 캡처를 지도 없이(blind) 다시 판단하고(회상 원자가 있으면 2패스), 실측 pano 좌표·aided 분석과 대조해 "
        "\"X 는 사실 X2 였다\"는 정정을 만들어 kind=discriminator 원자로 적재한다. 사람 승인은 없다 — 회상돼 쓰인 원자는 "
        "confirming/misled 로 채점되어 hits/misses 가 오르내리고, 오답만 뒷받침한 원자는 retracted(회상 제외)된다.",
        "",
        "## 집계",
        "",
        "| 지표 | 값 |", "|---|---|",
        f"| 국가 적중률(blind 최종) | {_rate(verdicts, 'country')} |",
        f"| 지역 적중률 | {_rate(verdicts, 'region')} |",
        f"| 도시 적중률 | {_rate(verdicts, 'city')} |",
        f"| 좌표 오차 km 중앙값 | {_fmt(med)} (n={len(errs)}) |",
        f"| 오차 분포 | " + " · ".join(f"{b} {sum(1 for v in verdicts if v.get('bucket') == b)}" for b in ('<1', '<10', '<100', '>=100', 'na')) + " |",
        f"| 2패스 사용(회상 원자 있음) | {p2_used}/{len(ok)} |",
        f"| 2패스로 판단이 바뀐 건수(revised) | {revised} |",
        f"| 판별자 원자 — 이번 기록에서 신규 / 병합 | {created} / {merged} |",
        f"| 저장소의 kind=discriminator 원자(누적) | {n_disc} |",
        f"| 철회된 원자 — 이번 기록 / 저장소 현재 status=retracted | {retracted} / {n_retracted_now} |",
        f"| 총비용(최신 기록 합) | ${total:.3f} |",
        "",
        "## 잡별",
        "",
        "| 날짜 | 보고서 | 정답 | blind 판단 | 국가 | 지역 | 도시 | 오차 km | 2패스 offered/relied | 판별자(신규+병합) | 비용 | 상태 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in recs:
        t = r.get("truth") or {}
        fin = (r.get("blind") or {}).get("final") or {}
        p2 = (r.get("blind") or {}).get("phase2")
        v = r.get("verdict") or {}
        corr = r.get("correction") or {}
        at = r.get("atoms") or {}
        rep = (r.get("report_file") or r.get("job_id") or "")
        truth_s = f"{t.get('iso')} · {t.get('city') or t.get('region') or t.get('country') or ''}"
        blind_s = f"{fin.get('country_iso') or '—'} · {fin.get('city') or fin.get('region') or fin.get('country') or '—'}"
        p2_s = f"{len(p2.get('known_offered') or [])}/{len(p2.get('relied_on') or [])}" + (" ↻" if p2.get("revised_from") else "") if p2 else "—"
        lines.append(f"| {ts(r.get('created'))} | `{rep}` | {truth_s} | {blind_s} | {v.get('country', '—')} | {v.get('region', '—')} | "
                     f"{v.get('city', '—')} | {_fmt(v.get('error_km'))} | {p2_s} | "
                     f"{len(corr.get('discriminators') or []) if isinstance(corr, dict) else 0} "
                     f"({len(at.get('created') or [])}+{len(at.get('merged') or [])}) | ${float(r.get('cost_usd') or 0):.3f} | {r.get('status')} |")

    # 판별자 목록 — 무엇이 학습됐는지 사람이 한눈에 보게.
    lines += ["", "## 이번 기록의 판별자 원자", ""]
    any_disc = False
    for r in recs:
        corr = r.get("correction") or {}
        at = r.get("atoms") or {}
        ids = at.get("ordered") or []          # 순서 정보가 없는 옛 기록은 id 를 붙이지 않는다(틀린 짝보다 없는 게 낫다)
        merged_ids = set(at.get("merged") or [])
        discs = corr.get("discriminators") or [] if isinstance(corr, dict) else []
        if not discs:
            continue
        any_disc = True
        lines.append(f"- **{r.get('job_id')}** ({(r.get('truth') or {}).get('iso')}) — {str(corr.get('summary') or '')[:300]}")
        for i, d in enumerate(discs):
            lk, ac = d.get("looks_like") or {}, d.get("actually") or {}
            aid = ids[i] if i < len(ids) else None
            lines.append(f"  - {'`' + aid + '`' + (' (병합)' if aid in merged_ids else '') + ' ' if aid else ''}"
                         f"[{d.get('layer')}/{d.get('category')}/{d.get('scope')}] "
                         f"{lk.get('country_iso')}>{ac.get('country_iso')} **{d.get('title')}**")
    if not any_disc:
        lines.append("- (없음)")

    # 유도 확인 — redo 가 남긴 induction 필드로만 말한다(데이터 없는 주장은 쓰지 않는다).
    lines += ["", "## 유도 확인 (redo)", "",
              "같은 잡을 `--redo` 로 다시 돌렸을 때 **직전 실행이 만든 판별자**가 2패스 `known_offered` 에 들어왔는가, "
              "그리고 모델이 `relied_on_atoms` 에 적었는가. 경로: conf = 혼동 ISO(\"X>X2\" 의 X 가 blind 추측/대안에 있음) · "
              "ent = 엔티티 접점 · geo = 스코프 지오해시 접점.", ""]
    ind_rows = [r for r in recs if isinstance(r.get("induction"), dict)]
    if not ind_rows:
        lines.append("- 아직 redo 기록이 없다 — `uv run geoguesshelper correct run --redo --job <id>` 로 확인.")
    for r in ind_rows:
        ind = r["induction"]
        prior, off, rel = ind.get("prior_atoms") or [], ind.get("offered") or [], ind.get("relied") or []
        fin = (r.get("blind") or {}).get("final") or {}
        lines.append(f"- **{r.get('job_id')}** ({(r.get('truth') or {}).get('iso')}) · 직전 실행 {ts(ind.get('prior_run'))} 판별자 {len(prior)}개 → "
                     f"이번 2패스 offered **{len(off)}** · relied **{len(rel)}** · 이번 blind 판단 {fin.get('country_iso')} "
                     f"(국가 {(r.get('verdict') or {}).get('country')})" + (" · 2패스 없음(회상 0건)" if not (r.get("blind") or {}).get("phase2") else ""))
        for aid in prior:
            via = ",".join((ind.get("via") or {}).get(aid) or []) or ("—" if aid not in off else "?")
            mark = "relied" if aid in rel else ("offered" if aid in off else "not offered")
            lines.append(f"  - `{aid}` {mark} via {via}")

    lines += ["", "## 학습 곡선 (시간순, corrections.jsonl)", "",
              "| 시각 | 잡 | 정답 | blind | 국가 | 오차 km | 2패스 | revised | 판별자 | hits/misses | 비용 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in ledger:
        lines.append(f"| {ts(row.get('at'))} | {row.get('job_id')} | {row.get('truth_iso')} | {row.get('blind_iso')} | "
                     f"{row.get('country_hit')} | {_fmt(row.get('error_km'))} | {row.get('n_offered')}/{row.get('n_relied')} | "
                     f"{'✓' if row.get('revised') else ''} | {row.get('n_discriminators')} | {row.get('hits')}/{row.get('misses')} | "
                     f"${float(row.get('cost_usd') or 0):.3f} |")
    lines += ["", "## 파일", "",
              "- `corr_<job_id>.json` — 잡 1건의 전체 기록(blind 두 패스·verdict·정정 도구 출력·적재·채점). redo 는 덮어쓴다.",
              "- `corrections.jsonl` — 실행 1회 = 1줄(시계열). redo 도 줄이 추가된다.",
              "- 원자 자체는 `docs/knowledge/atoms/` 에 kind=discriminator · origin=correction · tier=unaided 로, 회상 로그는 "
              "`docs/knowledge/recall_log.jsonl`(mode=blind-p2), 채점은 `docs/knowledge/evidence_log.jsonl`.",
              "", "## 재실행", "", "```",
              "uv run geoguesshelper correct run --dry-run          # 대상·비용 추정만",
              "uv run geoguesshelper correct run --limit 1",
              "uv run geoguesshelper correct run --redo --job <id>  # 유도 확인",
              "uv run geoguesshelper correct run                    # 전부(OK 인 잡은 건너뜀)",
              "uv run geoguesshelper correct report                 # 이 문서 재생성(LLM 호출 없음)",
              "```", ""]
    return "\n".join(lines)


def report(settings: Settings, log=print) -> dict:
    recs = _load_records(settings)
    ledger = _load_ledger(settings)
    d = _dir(settings)
    if not recs and not ledger:
        log("[correct] 기록이 없습니다 — 먼저 `correct run`")
        return {"jobs": 0, "runs": 0}
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text(_readme(settings, recs, ledger), encoding="utf-8")
    ok = [r for r in recs if r.get("status") == _STATUS_OK]
    log(f"[correct] README.md 재생성 — 잡 {len(recs)}건(OK {len(ok)}) · 실행 {len(ledger)}회 → {d / 'README.md'}")
    return {"jobs": len(recs), "ok": len(ok), "runs": len(ledger), "readme": str(d / "README.md")}


# ── 여러 잡 ───────────────────────────────────────────────────────
def run(settings: Settings, *, limit: int = 0, only: list[str] | None = None, redo: bool = False,
        lang: str = "en", phase2: bool = True, dry_run: bool = False, max_job_usd: float | None = None,
        log=print) -> dict:
    """image_panos 있는 잡을 대상으로 run_job. redo 가 아니면 status=OK 기록이 있는 잡은 건너뛴다."""
    jobs = baseline.load_jobs(settings)
    if only:
        jobs = [j for j in jobs if j["job_id"] in set(only)]
    existing: dict[str, dict] = {}
    for j in jobs:
        p = _corr_path(settings, j["job_id"])
        if p.exists():
            try:
                existing[j["job_id"]] = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                pass
    if not redo:
        jobs = [j for j in jobs if (existing.get(j["job_id"]) or {}).get("status") != _STATUS_OK]
    if limit:
        jobs = jobs[:limit]
    prior_costs = [float(r.get("cost_usd") or 0) for r in existing.values() if r.get("status") == _STATUS_OK and r.get("cost_usd")]
    est = (sum(prior_costs) / len(prior_costs)) if prior_costs else _EST_JOB_USD
    log(f"[correct] 대상 잡 {len(jobs)}건 (lang={lang}, phase2={phase2}, redo={redo}) · 예상 비용 ≈ ${est * len(jobs):.2f} "
        f"(${est:.2f}/잡{' — 기존 기록 평균' if prior_costs else ' — 추정'})")
    if dry_run:
        for j in jobs:
            t = _truth_of(j)
            ex = existing.get(j["job_id"])
            log(f"  - {j['job_id']} {t.get('iso')} · {len(j['images'])}장 · {t.get('city') or t.get('region') or ''}"
                + (f" · 기존 기록 {ex.get('status')}" if ex else ""))
        return {"targets": [j["job_id"] for j in jobs], "estimated_usd": round(est * len(jobs), 3), "dry_run": True}

    done: list[dict] = []
    total = 0.0
    aborted = None
    for j in jobs:
        rec, c, tries = _run_with_backoff(
            j, settings, lang=lang, phase2=phase2, log=log,
            budget_left=(None if max_job_usd is None else max_job_usd),
        )
        total += c
        item = {k: rec.get(k) for k in ("job_id", "status", "message")}
        # 비용은 **모든 시도의 합**이다. 마지막 시도 값만 쓰면 전체 합계와 어긋난다.
        item["cost_usd"] = round(c, 4)
        item["last_attempt_usd"] = rec.get("cost_usd")
        item["attempts"] = tries
        done.append(item)
        log(f"    → {rec.get('status')} · ${c:.3f} (누적 ${total:.3f}"
            + (f", 시도 {tries}회" if tries > 1 else "") + ")")
        if max_job_usd is not None and c > max_job_usd:
            aborted = f"잡 {j['job_id']} 비용 ${c:.3f} > 상한 ${max_job_usd:.2f}"
            log(f"[correct] 중단: {aborted}")
            break
    report(settings, log=log)
    log(f"[correct] 완료 {sum(1 for d in done if d['status'] == _STATUS_OK)}/{len(done)} · ${total:.3f}")
    return {"done": done, "cost_usd": round(total, 4), "aborted": aborted}


def job_from_result(source_job_id: str, result: dict, label: str = "") -> dict | None:
    """보고서 잡의 반환 dict → run_job 이 먹는 잡 dict(baseline.load_jobs 와 같은 모양). 재료가 없으면 None."""
    res = result or {}
    pa = res.get("primaryAnalysis") or {}
    if not (isinstance(pa, dict) and pa.get("image_panos") and pa.get("images")):
        return None
    rep = next((r for r in (res.get("reports") or []) if isinstance(r, dict) and r.get("file")), {})
    return {
        "job_id": source_job_id, "label": label, "report_file": rep.get("file"),
        "images": list(pa.get("images") or []), "image_panos": dict(pa.get("image_panos") or {}),
        "aided_prior": pa.get("analysis") or {}, "prior_lang": pa.get("lang"),
    }


def _run_with_backoff(job: dict, settings: Settings, *, lang: str, phase2: bool, log,
                      budget_left: float | None = None) -> tuple[dict, float, int]:
    """run_job + 연결 오류 백오프. 반환 (마지막 기록, 시도 비용 합, 시도 횟수).

    연결 오류는 이 잡의 결함이 아니라 그 순간의 네트워크다. SDK 재시도(max_retries=2)와
    _correction_call 의 1회 재시도를 **다 쓰고도** 실패한 상태이므로, 여기서는 시간을 두고
    다시 건다. (260910 실측: corr_job_737cae606637 이 "Connection error" 로 $0 에 끝났다.)

    비용 상한은 **매 시도 직후** 본다 — 나중에 한 번만 보면 이미 넘긴 뒤에도 유료 실행을
    세 번 더 하게 된다(260912 Codex 검토).
    """
    rec = run_job(job, settings, lang=lang, phase2=phase2, log=log)
    spent = float(rec.get("cost_usd") or 0.0)
    tries = 1
    for attempt, delay in enumerate(_API_ERROR_BACKOFF_S, start=1):
        if rec.get("status") != _STATUS_API_ERROR:
            break
        if budget_left is not None and spent > budget_left:
            log(f"    ⏹ 비용 ${spent:.3f} > 상한 ${budget_left:.2f} — 재시도 중단")
            break
        log(f"    ⏳ API 오류 — {delay:.0f}초 뒤 재시도 {attempt}/{len(_API_ERROR_BACKOFF_S)}")
        time.sleep(delay)
        rec = run_job(job, settings, lang=lang, phase2=phase2, log=log)
        spent += float(rec.get("cost_usd") or 0.0)
        tries += 1
    return rec, spent, tries


def run_for_result(settings: Settings, *, source_job_id: str, result: dict, label: str = "", log=print) -> dict:
    """서버 훅용 — 보고서 잡의 반환 dict 에서 잡 재료를 꺼내 run_job. jobs.jsonl 을 다시 읽지 않는다."""
    job = job_from_result(source_job_id, result, label)
    if job is None:
        return {"job_id": source_job_id, "status": "NO_IMAGES", "message": "primaryAnalysis.image_panos/images 가 없습니다.",
                "cost_usd": 0.0}
    # 백오프는 여기에도 있어야 한다 — 260910 에 실제로 죽은 것이 **이 경로**(서버가 보고서
    # 뒤에 자동 등록하는 정정 잡)였다. CLI 배치에만 넣으면 그 사례는 그대로 재발한다.
    rec, spent, tries = _run_with_backoff(job, settings, lang="en", phase2=True, log=log)
    if tries > 1:
        rec = dict(rec)
        rec["cost_usd"] = round(spent, 4)
        rec["attempts"] = tries
    try:
        report(settings, log=log)
    except Exception as exc:  # noqa: BLE001 — README 는 부가물이다
        log(f"[correct] README 재생성 실패: {exc}")
    return rec


# ── CLI ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="geoguesshelper correct",
                                 description="자동 정정 루프 — blind 판단 → 실측 정정 → 판별자 원자 (docs/knowledge/corrections/)")
    sub = ap.add_subparsers(dest="cmd")

    def add_run_args(p):
        p.add_argument("--limit", type=int, default=0, help="처리할 잡 수 (0=전부)")
        p.add_argument("--job", action="append", default=[], help="특정 잡 id 만(반복 가능)")
        p.add_argument("--redo", action="store_true", help="이미 OK 기록이 있는 잡도 다시(유도 확인)")
        p.add_argument("--lang", default="en", help="blind 분석 출력 언어 (기본 en)")
        p.add_argument("--no-phase2", action="store_true", help="회상 2패스 생략")
        p.add_argument("--dry-run", action="store_true", help="대상 잡·비용 추정만")
        p.add_argument("--max-job-usd", type=float, default=None, help="잡 1건 비용이 이 값을 넘으면 중단")

    add_run_args(sub.add_parser("run", help="정정 루프 실행(LLM 비용)"))
    p_one = sub.add_parser("one", help="잡 1건 = run --job ID")
    add_run_args(p_one)
    sub.add_parser("report", help="README.md 재생성(LLM 호출 없음)")
    sub.add_parser("help", help="도움말")

    a = ap.parse_args(argv)
    if a.cmd in (None, "help"):
        ap.print_help()
        return
    settings = load_settings()
    if a.cmd == "report":
        res = report(settings)
    else:
        if a.cmd == "one" and not a.job:
            ap.error("one 에는 --job ID 가 필요합니다.")
        res = run(settings, limit=a.limit, only=a.job or None, redo=a.redo, lang=a.lang,
                  phase2=not a.no_phase2, dry_run=a.dry_run, max_job_usd=a.max_job_usd)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
