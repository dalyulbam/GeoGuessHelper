"""ALTAIYA 추출기 — 패스A(행위자 대장) · 패스B(관계) · 패스C(사료권과 시대 층위).

기획서 `docs/plan/knowledge-atlas_260816.html` 의 2패스를 구현하고, 시대 축을 패스C 로 더한다.
원자는 **읽기만** 한다 — 산출물은 사이드카다.

    altaiya/data/entities.json           패스A · 행위자 대장 (슬러그 → 타입·표기·앵커)
    altaiya/data/relations.json          패스B · 관계 (유형·방향·시기·인용 원자)
    altaiya/data/relations.audit.jsonl   근거 구절 로그 (검수 전용 · 그래프는 쓰지 않는다)
    altaiya/data/periods.json            패스C · 사료권과 그 시대 층위 + 원자별 배정

날조를 막는 세 가지(기획서):
  1. 닫힌 세계 — 그 원자가 언급한 엔티티들 사이의 관계만 받는다. 밖의 것은 폐기.
  2. 문장 근거 — 관계마다 본문 인용 구절을 받아 실재하는지 대조하고, 없으면 폐기.
  3. 병합    — 같은 (src, dst, type) 은 엣지 1개로 합치고 인용 원자를 누적한다(= strength).

증분이다. 대장에 이미 있는 슬러그·이미 처리한 원자는 건너뛴다(`--redo` 로 전체 재추출).

사용:
    uv run --project E:\\GeoguessHelper python altaiya\\altaiya-backend\\extract.py entities
    uv run --project E:\\GeoguessHelper python altaiya\\altaiya-backend\\extract.py relations
    uv run --project E:\\GeoguessHelper python altaiya\\altaiya-backend\\extract.py sample -n 20
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # E:\GeoguessHelper
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))       # 본체 패키지(llm·config) 재사용

import atlas
import rules

from geoguesshelper import llm
from geoguesshelper.config import load_settings

DATA = HERE.parent / "data"
ENTITIES_JSON = DATA / "entities.json"
RELATIONS_JSON = DATA / "relations.json"
AUDIT_JSONL = DATA / "relations.audit.jsonl"
PERIODS_JSON = DATA / "periods.json"

REL_TYPES = ("operates", "supplies", "produces", "connects", "ruled",
             "succeeded", "founded", "opposed", "part-of", "related")
SYMMETRIC = {"connects", "opposed"}

# 유형 → 주 테마 (기획서 "관계 유형 9종" 표). part-of 는 지도 계층이라 전 테마.
REL_THEMES: dict[str, tuple[str, ...]] = {
    "operates":  ("stock", "corporate"),
    "supplies":  ("trade", "stock"),
    "produces":  ("trade", "travel"),
    "connects":  ("trade", "travel", "realty"),
    "ruled":     ("heritage",),
    "succeeded": ("heritage", "corporate"),
    "founded":   ("heritage", "talent"),
    "opposed":   ("heritage",),
    "part-of":   tuple(rules.THEMES),
    "related":   (),          # 출처 원자의 레이어에서 유도한다
}

REL_KO = {
    "operates": "운영", "supplies": "공급", "produces": "생산", "connects": "연결",
    "ruled": "통치", "succeeded": "계승", "founded": "후원", "opposed": "대립",
    "part-of": "소속", "related": "관련",
}


def _now() -> float:
    return time.time()


def _load_json(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def _save_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _batches(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def _run(tasks: list, settings, label: str) -> list:
    """LLM 호출 배치를 동시에 돌리고 예외는 그 자리에 담아 돌려준다."""
    t0 = time.monotonic()
    out = llm.gather(tasks, settings, limit=settings.max_parallel_llm)
    ok = sum(1 for r in out if not isinstance(r, Exception))
    print(f"   호출 {ok}/{len(tasks)} 성공 · {time.monotonic() - t0:.0f}초")
    for r in out:
        if isinstance(r, Exception):
            print(f"   ! {label} 배치 실패: {type(r).__name__}: {r}")
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


# ══════════════════════════════════════════════════════════════════
#  패스 A — 행위자 대장
# ══════════════════════════════════════════════════════════════════
_A_SYSTEM = """너는 지식 저장소의 엔티티 슬러그를 지도 위 '행위자'로 분류하는 사서다.

닫힌 세계: 함께 주는 원자(제목·본문 조각·태그)만이 근거다. 그 슬러그가 무엇인지
원자에서 확신할 수 없으면 추측하지 말고 type 을 place 로 두고 basis 에 "근거 부족"이라 적어라.

type 은 다음 8종 중 하나다.
  place   거점 — 도시·마을·구역·항구도시·개별 건물이나 유적
  polity  정치체 — 국가·제국·공화국·왕조국가, 그리고 주/도/현 같은 행정구역
  org     조직 — 대학·도서관·교단·수도회·가문·재단·축제 조직·관청
  company 기업 — 영리 회사·국영기업·브랜드
  person  인물 — 개인
  infra   인프라 — 다리·송유관·철도·항만시설·공장·발전소·터미널
  culture 문화 — 언어·방언·문자·건축양식·요리·전통·제도(개혁 등)
  nature  자연 — 바다·만·강·호수·산맥·화산·빙하·자연지형·국립공원

경계 판정:
  · 사람이 사는 장소(도시·섬·마을)는 place. 통치 단위(국가·주·제국)는 polity.
  · 역사적 지역명은 그 시대의 통치체로 서술되면 polity, 단순 지리 권역이면 place.
  · 지형 자체가 주제면 nature, 그 지형 위의 정착지면 place.
  · 대학·수도회·가문은 org, 이윤을 내는 회사는 company.

label_ko 는 한국에서 통용되는 표기(없으면 원어 음차), label_en 은 영문/원어 통용 표기다.
basis 는 어느 원자의 어떤 서술로 그렇게 판정했는지 40자 이내 한 조각.

배치로 준 슬러그를 하나도 빠뜨리지 말고 전부 돌려줘라."""

_A_TOOL = {
    "name": "actor_ledger",
    "description": "엔티티 슬러그 배치의 타입·표기를 한 번에 돌려준다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "actors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string", "description": "입력으로 준 슬러그 그대로"},
                        "type": {"type": "string", "enum": list(rules.TYPES)},
                        "label_ko": {"type": "string"},
                        "label_en": {"type": "string"},
                        "basis": {"type": "string", "description": "40자 이내 판정 근거"},
                    },
                    "required": ["slug", "type", "label_ko", "label_en", "basis"],
                },
            }
        },
        "required": ["actors"],
    },
}


def _actor_payload(slug: str, atom_ids: list[str], atoms: dict) -> dict:
    """분류에 필요한 최소한의 원자 증거만 싣는다(제목 + 본문 앞머리 + 태그)."""
    mine = [atoms[a] for a in atom_ids if a in atoms][:4]
    ev = []
    for a in mine:
        body = (atlas.atom_detail(a["id"]) or {}).get("body") or ""
        ev.append({
            "title": a.get("title"),
            "layer": a.get("layer"),
            "scope": a.get("scope"),
            "tags": (a.get("tags") or [])[:6],
            "body": re.sub(r"\s+", " ", body)[:220],
        })
    return {"slug": slug, "atom_count": len(atom_ids), "atoms": ev}


def _derive(slug: str, atom_ids: list[str], atoms: dict, atype: str) -> dict:
    """앵커·범위·시기·테마는 LLM 이 아니라 원자에서 계산한다(LLM 지오코딩 금지)."""
    mine = [atoms[a] for a in atom_ids if a in atoms]
    coords = [(a["lat"], a["lng"]) for a in mine
              if isinstance(a.get("lat"), (int, float)) and isinstance(a.get("lng"), (int, float))]
    anchor = atlas._median_anchor(coords)
    scope, p_start, p_end = "point", None, None
    layers: dict[str, int] = {}
    reports: set[str] = set()
    for a in mine:
        layers[a.get("layer", "?")] = layers.get(a.get("layer", "?"), 0) + 1
        reports.update(a.get("reports") or [])
        if atlas._SCOPE_ORDER.get(a.get("scope", "point"), 0) > atlas._SCOPE_ORDER.get(scope, 0):
            scope = a.get("scope", "point")
        if a.get("period_start") is not None:
            p_start = a["period_start"] if p_start is None else min(p_start, a["period_start"])
            pe = a.get("period_end")
            p_end = None if pe is None else (pe if p_end is None else max(p_end, pe))
    themes = {}
    for th in rules.THEMES:
        w = rules.THEME_MATRIX[th]
        score = sum(w.get(a.get("layer", ""), 0.0) for a in mine)
        score += rules.TYPE_THEME_BOOST.get(atype, {}).get(th, 0.0)
        themes[th] = round(score, 2)
    return {
        "anchor": ({"lat": round(anchor[0], 5), "lng": round(anchor[1], 5),
                    "from": f"원자 {len(coords)}개 좌표 중앙값"} if anchor else None),
        "scope": scope,
        "period": [p_start, p_end] if p_start is not None else None,
        "themes": sorted([t for t, v in themes.items() if v >= rules.ACTOR_THEME_MIN],
                         key=lambda t: -themes[t]),
        "theme_scores": themes,
        "layers": layers,
        "atoms": len(mine),
        "reports": len(reports),
    }


def pass_a(settings, *, redo: bool, batch: int, limit: int | None, dry: bool) -> dict:
    idx = atlas._load_index()
    atoms, ent_index = idx["atoms"], idx["entities"]
    ledger = _load_json(ENTITIES_JSON, {"meta": {}, "actors": {}})
    known = {} if redo else (ledger.get("actors") or {})

    todo = sorted(s for s, ids in ent_index.items()
                  if s not in known and any(a in atoms for a in ids))
    if limit:
        todo = todo[:limit]
    print("\n  == 패스A · 행위자 대장 ============================")
    print(f"   엔티티 {len(ent_index)} · 대장 기존 {len(known)} · 이번 추출 {len(todo)}")
    if not todo:
        print("   새 엔티티 없음 — 대장 그대로.\n")
        return ledger

    groups = _batches(todo, batch)
    print(f"   배치 {len(groups)}개 × 최대 {batch}슬러그 · 모델 {settings.model_fact}")
    if dry:
        print(json.dumps([_actor_payload(s, ent_index[s], atoms) for s in groups[0]],
                         ensure_ascii=False, indent=1)[:4000])
        return ledger

    def task(group: list[str]):
        payload = [_actor_payload(s, ent_index[s], atoms) for s in group]
        user = ("다음 엔티티들을 분류해라. slug 는 입력 그대로 돌려줘야 한다.\n\n"
                + json.dumps(payload, ensure_ascii=False)
                + "\n\nactor_ledger 를 한 번 호출해라.")
        return llm.call(
            settings,
            system=_A_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=[_A_TOOL],
            tool_choice={"type": "tool", "name": "actor_ledger"},
            max_tokens=8000,
            deadline_s=300,
            role="fact",
        )

    results = _run([(lambda g=g: task(g)) for g in groups], settings, "패스A")

    cost = 0.0
    got: dict[str, dict] = {}
    for resp in results:
        if isinstance(resp, Exception):
            continue
        cost += llm.spend(resp)
        for row in ((llm.tool_input(resp, "actor_ledger") or {}).get("actors") or []):
            slug = (row.get("slug") or "").strip()
            if slug not in ent_index or row.get("type") not in rules.TYPES:
                continue
            got[slug] = {
                "type": row["type"],
                "label_ko": (row.get("label_ko") or slug).strip(),
                "label_en": (row.get("label_en") or rules.label_of(slug)).strip(),
                "basis": (row.get("basis") or "").strip()[:60],
            }

    missed = [s for s in todo if s not in got]
    actors = dict(known)
    for slug, row in got.items():
        actors[slug] = {"slug": slug, **row,
                        **_derive(slug, ent_index[slug], atoms, row["type"]),
                        "extracted": _now(), "model": settings.model_fact}

    meta = dict(ledger.get("meta") or {})
    meta.update({
        "pass": "A", "built": _now(), "model": settings.model_fact,
        "count": len(actors), "last_extracted": len(got), "last_missed": missed,
        "cost_usd": round((meta.get("cost_usd") or 0.0) + cost, 4),
    })
    out = {"meta": meta, "actors": dict(sorted(actors.items()))}
    _save_json(ENTITIES_JSON, out)

    print(f"   분류 {len(got)} · 누락 {len(missed)} · ${cost:.3f}")
    print(f"   타입: {Counter(a['type'] for a in actors.values()).most_common()}")
    if missed:
        print(f"   ! 응답에서 빠진 슬러그: {missed[:10]}")
    print(f"   → {ENTITIES_JSON.relative_to(ROOT)}  (총 {len(actors)})\n")
    return out


# ══════════════════════════════════════════════════════════════════
#  패스 B — 관계
# ══════════════════════════════════════════════════════════════════
_B_SYSTEM = """너는 지식 원자 하나가 **명시적으로 서술한** 행위자 사이의 관계만 뽑는 추출기다.

닫힌 세계: 각 원자의 entities 목록에 있는 슬러그끼리만 잇는다. 목록 밖의 이름을 만들면 폐기된다.
문장 근거: 관계마다 원자 body 에서 **그대로 복사한** 구절(quote, 8자 이상)을 붙인다.
          body 에 없는 문장을 지어내면 그 관계는 폐기된다.
자제: 두 엔티티가 한 원자에 함께 나온다는 것만으로는 관계가 아니다. 서술이 둘의 관계를
      말하지 않으면 아무 관계도 내지 마라. 억지로 잇는 것보다 비는 편이 낫다.

type 10종:
  operates  A가 B를 소유·운영          dir "->"
  supplies  A가 B에 재화·에너지 공급    dir "->"
  produces  A가 특산물 B를 산출        dir "->"
  connects  A와 B를 잇는 노선·교량·항로  dir "<->"
  ruled     A가 B를 지배·통치          dir "->"
  succeeded A가 B에서 기원·계승        dir "->"  (계승자가 src)
  founded   A가 B를 설립·육성·후원      dir "->"
  opposed   A와 B가 충돌·대립          dir "<->"
  part-of   A가 B의 일부(행정·지리 포함) dir "->"  (부분이 src)
  related   위 어디에도 안 맞지만 서술이 분명한 관계 — 버리지 말고 여기에 담아라

흔한 오분류 — 아래는 관계가 아니거나 다른 유형이다. 파일럿에서 실제로 나온 것들이다.
  · 소재지 서술("A는 B에 있다", "B의 제약 클러스터로 A를 든다")은 operates 가 아니다.
    operates 는 본문이 소유·운영을 말할 때만(회사 → 공장·노선·터미널). 소재지뿐이면 related.
  · 비교·대조("A는 B와 달리", "B보다 고급인 A")는 대립이 아니다. opposed 는 두 세력이
    실제로 싸우거나 맞선 경우만. 단순 대조라면 관계를 내지 마라.
  · 전쟁·사건이 장소를 덮친 서술에서 opposed 로 잇지 마라. 점령·지배면 ruled(지배하는 쪽이 src),
    그 밖은 related.
  · "B는 어떤 산업의 중심지다 (예: A사)" 처럼 도시가 기업을 **예시로** 든 서술에서
    founded 나 operates 를 만들지 마라. 도시가 회사를 세운 것이 아니다 — related 다.
    founded 는 본문이 설립·창설·육성을 직접 말할 때만 쓴다.
  · part-of 는 본문이 포함을 실제로 말할 때만이다("Novalja on Pag island", "Kvarner islands (Krk...)").
    같은 나라·지역 이야기가 함께 나온다는 이유만으로 도시→국가, 지역→국가를 잇지 마라.
    quote 는 그 포함을 말하는 구절이어야 한다.

시기: body 에 연도가 있을 때만 period_start / period_end 를 채운다. 추정 금지.
같은 두 행위자 사이에 관계가 여럿이면 각각 따로 낸다."""

_B_TOOL = {
    "name": "atom_relations",
    "description": "원자 배치에서 행위자 사이의 관계를 뽑는다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "atom": {"type": "string", "description": "출처 원자 id (입력 그대로)"},
                        "src": {"type": "string", "description": "그 원자 entities 안의 슬러그"},
                        "dst": {"type": "string", "description": "그 원자 entities 안의 슬러그"},
                        "type": {"type": "string", "enum": list(REL_TYPES)},
                        "dir": {"type": "string", "enum": ["->", "<->"]},
                        "period_start": {"type": "integer"},
                        "period_end": {"type": "integer"},
                        "quote": {"type": "string", "description": "body 에서 그대로 복사한 근거 구절"},
                    },
                    "required": ["atom", "src", "dst", "type", "dir", "quote"],
                },
            }
        },
        "required": ["relations"],
    },
}


def _quote_ok(quote: str, body: str) -> bool:
    """인용이 본문에 실재하는지 — 완전 일치, 아니면 어절 70% 이상 포함."""
    q, b = _norm(quote), _norm(body)
    if len(q) < 8 or not b:
        return False
    if q in b:
        return True
    words = [w for w in re.findall(r"[0-9a-z\uac00-\ud7a3'\u2019-]+", q) if len(w) > 3]
    if not words:
        return False
    return sum(1 for w in words if w in b) / len(words) >= 0.7


# 유형이 성립하려면 양 끝의 행위자 타입이 말이 돼야 한다. 프롬프트로 두 번 막아도
# "게데온 리히터가 부다페스트를 운영한다" 같은 것이 재발해서, 대장 타입으로 결정적으로 건다.
# 위반은 버리지 않고 related 로 강등한다 — 서술 자체는 원자에 있기 때문이다(추출 손실 방지).
_TYPE_GUARD: dict[str, dict[str, set[str]]] = {
    "operates": {"dst": {"infra", "company", "org"}},          # 도시·국가를 운영할 수는 없다
    "founded":  {"src": {"person", "org", "company", "polity", "culture"}},
    "ruled":    {"src": {"polity", "org", "person"}},
}


def _guard(rtype: str, src_type: str, dst_type: str) -> str:
    rule = _TYPE_GUARD.get(rtype)
    if not rule:
        return rtype
    if "src" in rule and src_type not in rule["src"]:
        return "related"
    if "dst" in rule and dst_type not in rule["dst"]:
        return "related"
    return rtype


def _rel_themes(rtype: str, layer: str) -> list[str]:
    themes = list(REL_THEMES.get(rtype) or ())
    if not themes:
        themes = [t for t in rules.THEMES
                  if rules.THEME_MATRIX[t].get(layer, 0.0) >= rules.EDGE_THEME_MIN]
    return themes


def pass_b(settings, *, redo: bool, batch: int, limit: int | None, dry: bool) -> dict:
    idx = atlas._load_index()
    atoms = idx["atoms"]
    ledger = _load_json(ENTITIES_JSON, {"actors": {}})
    actors = ledger.get("actors") or {}
    if not actors:
        print("   ! 대장이 비어 있다 — 먼저 `extract.py entities` 를 돌려라.")
        return {}

    store = _load_json(RELATIONS_JSON, {"meta": {}, "relations": []})
    done: set[str] = set() if redo else set(((store.get("meta") or {}).get("atoms_done")) or [])
    keep = [] if redo else list(store.get("relations") or [])
    if redo:
        AUDIT_JSONL.unlink(missing_ok=True)   # 전면 재추출 = 근거 로그도 새로 쓴다

    cand = []
    for aid, a in atoms.items():
        if aid in done:
            continue
        ents = sorted({e for e in (a.get("entities") or []) if e in actors})
        if len(ents) >= 2:
            cand.append((aid, ents))
    cand.sort()
    if limit:
        cand = cand[:limit]

    print("\n  == 패스B · 관계 추출 =============================")
    print(f"   엔티티 ≥2 원자 {len(cand)} 처리 대상 · 이미 처리 {len(done)} · 기존 관계 {len(keep)}")
    if not cand:
        print("   새 원자 없음 — 관계 그대로.\n")
        return store

    def payload(aid: str, ents: list[str]) -> dict:
        a = atoms[aid]
        detail = atlas.atom_detail(aid) or {}
        return {
            "id": aid,
            "title": a.get("title"),
            "layer": a.get("layer"),
            "period": [a.get("period_start"), a.get("period_end")],
            "entities": [{"slug": s, "label": actors[s].get("label_en") or s,
                          "type": actors[s].get("type")} for s in ents],
            "body": re.sub(r"\s+", " ", detail.get("body") or ""),
        }

    groups = _batches(cand, batch)
    print(f"   배치 {len(groups)}개 × 최대 {batch}원자 · 모델 {settings.model_fact}")
    if dry:
        print(json.dumps([payload(aid, e) for aid, e in groups[0]],
                         ensure_ascii=False, indent=1)[:4000])
        return store

    def task(group):
        user = ("다음 원자들에서 관계를 뽑아라. atom·src·dst 는 입력에 있는 값 그대로 써야 한다.\n\n"
                + json.dumps([payload(aid, e) for aid, e in group], ensure_ascii=False)
                + "\n\natom_relations 를 한 번 호출해라. 관계가 없으면 빈 배열을 내라.")
        return llm.call(
            settings,
            system=_B_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=[_B_TOOL],
            tool_choice={"type": "tool", "name": "atom_relations"},
            max_tokens=8000,
            deadline_s=300,
            role="fact",
        )

    results = _run([(lambda g=g: task(g)) for g in groups], settings, "패스B")

    allowed = {aid: set(ents) for aid, ents in cand}
    bodies = {aid: (atlas.atom_detail(aid) or {}).get("body") or "" for aid, _ in cand}
    cost = 0.0
    raw, drop, demote = [], Counter(), Counter()
    for resp in results:
        if isinstance(resp, Exception):
            continue
        cost += llm.spend(resp)
        for r in ((llm.tool_input(resp, "atom_relations") or {}).get("relations") or []):
            aid = (r.get("atom") or "").strip()
            src = (r.get("src") or "").strip()
            dst = (r.get("dst") or "").strip()
            rtype = r.get("type")
            if aid not in allowed:
                drop["원자 밖"] += 1
                continue
            if src not in allowed[aid] or dst not in allowed[aid] or src == dst:
                drop["닫힌 세계 위반"] += 1
                continue
            if rtype not in REL_TYPES:
                drop["유형 불명"] += 1
                continue
            if not _quote_ok(r.get("quote", ""), bodies[aid]):
                drop["근거 구절 불일치"] += 1
                continue
            guarded = _guard(rtype, actors[src].get("type", "place"), actors[dst].get("type", "place"))
            if guarded != rtype:
                demote[f"{rtype}→related"] += 1
                rtype = guarded
            raw.append({"atom": aid, "src": src, "dst": dst, "type": rtype,
                        "dir": "<->" if rtype in SYMMETRIC else (r.get("dir") or "->"),
                        "period_start": r.get("period_start"), "period_end": r.get("period_end"),
                        "quote": (r.get("quote") or "").strip()[:400],
                        "layer": atoms[aid].get("layer", "?")})

    # ── 병합: 같은 (src, dst, type) 은 엣지 1개 · 인용 누적 ─────────
    merged: dict[tuple, dict] = {}
    for e in {(r["src"], r["dst"], r["type"], r["atom"]): r for r in raw}.values():
        s, d = e["src"], e["dst"]
        if e["type"] in SYMMETRIC and s > d:
            s, d = d, s
        key = (s, d, e["type"])
        cur = merged.get(key)
        if cur is None:
            cur = merged[key] = {"src": s, "dst": d, "type": e["type"], "dir": e["dir"],
                                 "period": [e["period_start"], e["period_end"]],
                                 "themes": _rel_themes(e["type"], e["layer"]),
                                 "atoms": [], "layers": {}}
        if e["atom"] not in cur["atoms"]:
            cur["atoms"].append(e["atom"])
        cur["layers"][e["layer"]] = cur["layers"].get(e["layer"], 0) + 1
        ps, pe = cur["period"]
        if e["period_start"] is not None:
            cur["period"][0] = e["period_start"] if ps is None else min(ps, e["period_start"])
        if e["period_end"] is not None:
            cur["period"][1] = e["period_end"] if pe is None else max(pe, e["period_end"])

    fresh = []
    for r in merged.values():
        r["strength"] = len(r["atoms"])
        r["layer"] = max(r["layers"], key=r["layers"].get)
        if r["period"] == [None, None]:
            r["period"] = None
        fresh.append(r)

    # 기존 관계와 합치기(증분) — 같은 키면 인용 원자를 누적한다
    index = {(r["src"], r["dst"], r["type"]): r for r in keep}
    for r in fresh:
        key = (r["src"], r["dst"], r["type"])
        cur = index.get(key)
        if cur is None:
            index[key] = r
            continue
        for aid in r["atoms"]:
            if aid not in cur["atoms"]:
                cur["atoms"].append(aid)
        for lay, n in r["layers"].items():
            cur["layers"][lay] = cur["layers"].get(lay, 0) + n
        cur["strength"] = len(cur["atoms"])
        cur["layer"] = max(cur["layers"], key=cur["layers"].get)
    relations = sorted(index.values(), key=lambda r: (-r["strength"], r["src"], r["dst"]))

    # 근거 구절은 그래프에 저장하지 않는다(기획서) — 검수 로그로만 남긴다
    DATA.mkdir(parents=True, exist_ok=True)
    with AUDIT_JSONL.open("a", encoding="utf-8") as f:
        for r in raw:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    meta = dict(store.get("meta") or {})
    prev_done = set() if redo else set((meta.get("atoms_done") or []))
    meta.update({
        "pass": "B", "built": _now(), "model": settings.model_fact,
        "atoms_done": sorted(prev_done | {aid for aid, _ in cand}),
        "relation_total": len(relations), "last_raw": len(raw),
        "last_dropped": dict(drop), "last_demoted": dict(demote),
        "cost_usd": round((meta.get("cost_usd") or 0.0) + cost, 4),
    })
    out = {"meta": meta, "relations": relations}
    _save_json(RELATIONS_JSON, out)

    kept_types = Counter(r["type"] for r in relations)
    print(f"   원시 관계 {len(raw)} · 폐기 {sum(drop.values())} {dict(drop) or ''}"
          f" · 강등 {sum(demote.values())} {dict(demote) or ''} · ${cost:.3f}")
    print(f"   병합 후 관계 {len(relations)} (신규 {len(fresh)} 병합 반영)")
    print(f"   유형: {kept_types.most_common()}")
    print(f"   → {RELATIONS_JSON.relative_to(ROOT)} · 근거 로그 {AUDIT_JSONL.name}\n")
    return out


# ══════════════════════════════════════════════════════════════════
#  패스 C — 사료권과 시대 층위
# ══════════════════════════════════════════════════════════════════
# 시대는 전 세계 공통 눈금이 아니다. 지역마다 무엇이 한 시대를 끝냈는지가 다르고,
# 그래서 그 지역의 역사책 목차도 다르다. 하나의 슬라이더로 세계를 자르는 대신
# **사료권마다 제 층위를 갖게** 한다. 사료권은 국경이 아니라 원자 좌표의 군집에서 나온다.
CLUSTER_KM = 400.0

# 민족주의 서사 린트 — 걸러 버리지 않고 표시해서 사람이 보게 한다(오탐이 있을 수 있다).
# 부분 문자열 매칭이라 오탐이 난다(실제로 "설국권"이 "국권"에 걸렸다) — 구(句)로 좁힌다.
NATIONALIST_LINT = ("민족의", "국권 회복", "국권 침탈", "민족 각성", "민족적 각성", "부활기",
                    "영광의 시대", "민족 수난", "정통성 회복", "민족의 숙원",
                    "회복영토", "회복 영토", "실지 회복", "고토 회복", "수복 영토",
                    "recovered territories", "reconquest of", "ancestral homeland",
                    "잃어버린", "되찾", "고유의 얼", "national awakening", "national revival",
                    "golden age of the nation", "liberation of the fatherland")


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _clusters(atoms: dict) -> list[dict]:
    """원자 좌표의 리더 군집 — 중심 거리 기준이라 사슬처럼 대륙을 삼키지 않는다."""
    items = sorted(atoms.values(), key=lambda a: (-len(a.get("entities") or []), a["id"]))
    out: list[dict] = []
    for a in items:
        if not isinstance(a.get("lat"), (int, float)):
            continue
        p = (a["lat"], a["lng"])
        best, bd = None, 1e9
        for c in out:
            d = _haversine(p, c["center"])
            if d < bd:
                bd, best = d, c
        if best is not None and bd <= CLUSTER_KM:
            best["pts"].append(p)
            best["atoms"].append(a["id"])
            n = len(best["pts"])
            best["center"] = (sum(x for x, _ in best["pts"]) / n,
                              sum(y for _, y in best["pts"]) / n)
        else:
            out.append({"center": p, "pts": [p], "atoms": [a["id"]]})
    out.sort(key=lambda c: -len(c["atoms"]))
    return out


_C_SYSTEM = """너는 한 지역의 사료(史料)를 읽고 **그 지역 역사서술의 시대 층위**를 세우는 사서다.

세계 공통의 눈금은 없다. 어떤 곳은 통치체 교체로, 어떤 곳은 교역로의 이동이나 관개·광산·
철도 같은 물질 조건으로 시대가 갈린다. 그 지역 역사책이 실제로 쓰는 구획을 따라라.

민족주의 시점은 배제한다 — 이건 강한 금지다.
  · 현재의 국경·국민국가를 과거에 투사하지 마라. 층의 이름은 그 시기에 실제로 작동한
    통치체·교역권·문화권·물질조건에서 가져와라("합스부르크 통치기", "무굴 통치기",
    "정착·자유국기", "회사령기", "후고전기" 같은 식).
  · 목적론 금지. 독립·건국을 역사의 종착점으로 두고 그리로 수렴하는 배열을 만들지 마라.
    "민족의 각성·부활·수난·영광·잃어버린 땅" 같은 어휘를 쓰지 마라.
  · 현대 국가를 고대·중세 정치체의 계승자로 서술하지 마라. 주민 집단의 연속성을 단정하지 마라.
  · 하나의 정통 명칭을 강요하지 마라. 경쟁하는 명칭이 있으면 also_called 에 병기해라
    (그 땅을 다르게 부르는 쪽의 이름도 함께).
  · **한 국가의 공식 명칭이 곧 중립 명칭은 아니다.** 예컨대 1945년 이후 오데르-나이세 동쪽을
    "회복영토(Ziemie Odzyskane)"라 부르는 것은 한쪽의 영유 주장이다. 대표 라벨은 그 시기에
    실제로 일어난 일로 붙이고("주민 교체·재정착기"), 그런 명칭은 also_called 에만 병기해라.
  · 서구 3분법(고대·중세·근대)을 기본값으로 밀어넣지 마라. 그 지역에 맞지 않으면 쓰지 마라.

층은 4~8개, 시간순으로 겹치지 않게. start/end 는 대략적이면 라운드 숫자로 쓰고,
마지막 층은 현재까지 열려 있으면 end 를 비워라. 근거 없는 연도를 지어내지 마라.
basis 에는 그 층을 앞뒤와 가르는 실제 변화(제도·교역·기술·건축·인구)를 한 줄로 적어라.
frame 은 이 사료권을 무엇으로 나눴는지, caution 은 이 구획이 놓치는 것을 한 줄로."""

_C_TOOL = {
    "name": "sphere_strata",
    "description": "한 사료권의 정체와 시대 층위를 세운다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "영문 소문자 슬러그 (예: east-adriatic)"},
            "label_ko": {"type": "string"}, "label_en": {"type": "string"},
            "frame": {"type": "string", "description": "무엇을 기준으로 시대를 갈랐는지 한 줄"},
            "caution": {"type": "string", "description": "이 구획이 놓치는 것 한 줄"},
            "strata": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "label_ko": {"type": "string"}, "label_en": {"type": "string"},
                        "start": {"type": "integer", "description": "시작 연도(기원전은 음수)"},
                        "end": {"type": "integer", "description": "끝 연도 · 현재까지면 생략"},
                        "basis": {"type": "string"},
                        "also_called": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["key", "label_ko", "label_en", "start", "basis"],
                },
            },
        },
        "required": ["key", "label_ko", "label_en", "frame", "caution", "strata"],
    },
}

_E_SYSTEM = """너는 지식 원자 하나가 **어느 시대 층에 놓이는지**를 정하는 사서다.

각 원자에 그 사료권의 층위 목록을 함께 준다. 원자 본문이 말하는 시대를 골라라.
  · 본문이 지금의 조건(현행 경제·인구·행정·관광)을 말하면 마지막(현재까지 열린) 층을 골라라.
  · 본문이 특정 통치기·건설기의 산물을 말하면 그 층을 골라라(예: 사회주의기 집합주택,
    무굴기 영묘, 회사령기 철도).
  · 지형·기후처럼 시대에 매이지 않는 서술이면 era 를 "atemporal" 로 둬라. 억지로 넣지 마라.
  · 판단 근거는 본문에서 그대로 복사한 구절(quote)로 붙여라. 본문에 없는 말을 지어내면 폐기된다.
  · 민족주의 서사로 시대를 읽지 마라. "누구의 땅이었나"가 아니라 "무엇이 작동했나"로 고른다."""

_E_TOOL = {
    "name": "atom_eras",
    "description": "원자 배치를 시대 층에 배정한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "atom": {"type": "string"},
                        "era": {"type": "string", "description": "층 key 또는 atemporal"},
                        "quote": {"type": "string"},
                    },
                    "required": ["atom", "era", "quote"],
                },
            }
        },
        "required": ["assignments"],
    },
}


# 모델이 도구 호출 문법을 **문자열 값 안에** 흘려 넣는 실패 모드가 있다. 실제로 중부유럽
# 군집에서 caution 값 뒤에 `</caution><parameter name="strata">[...]` 가 통째로 붙어 왔다.
# 버리면 그 군집이 통째로 사라지므로 값에서 되찾는다.
_PARAM_RE = re.compile(r'<parameter name="(\w+)">\s*(.*?)\s*(?:</parameter>|\Z)', re.S)


def _salvage_tool_input(d: dict) -> dict:
    out = dict(d)
    for k, v in list(d.items()):
        if not isinstance(v, str) or '<parameter name=' not in v:
            continue
        out[k] = re.split(r"</\w+>", v, maxsplit=1)[0].strip()
        for name, raw in _PARAM_RE.findall(v):
            if out.get(name):
                continue
            raw = raw.strip()
            try:
                out[name] = json.loads(raw)
            except ValueError:
                out[name] = raw
    return out


def _lint_nationalism(text: str) -> list[str]:
    low = (text or "").lower()
    return [w for w in NATIONALIST_LINT if w.lower() in low]


def _era_of_period(strata: list[dict], start, end) -> str | None:
    """연도가 있는 원자는 LLM 없이 층위와 겹쳐 본다 — 가장 많이 겹치는 층."""
    if start is None:
        return None
    s, e = start, (end if end is not None else start)
    best, bo = None, 0
    for st in strata:
        a, b = st.get("start"), st.get("end")
        b = b if b is not None else 3000
        ov = min(e, b) - max(s, a)
        if ov >= 0 and (best is None or ov > bo):
            best, bo = st["key"], ov
    return best


def pass_c(settings, *, redo: bool, batch: int, limit: int | None, dry: bool) -> dict:
    idx = atlas._load_index()
    atoms = idx["atoms"]
    ledger = _load_json(ENTITIES_JSON, {"actors": {}}).get("actors") or {}
    store = _load_json(PERIODS_JSON, {"meta": {}, "spheres": {}, "atom_era": {}})
    spheres: dict = {} if redo else (store.get("spheres") or {})
    atom_era: dict = {} if redo else (store.get("atom_era") or {})

    clusters = _clusters(atoms)
    if limit:
        clusters = clusters[:limit]
    print("\n  == 패스C · 사료권과 시대 층위 =====================")
    print(f"   원자 {len(atoms)} → 군집 {len(clusters)}개 (중심 {CLUSTER_KM:.0f}km)"
          f" · 기존 사료권 {len(spheres)}")

    def evidence(c: dict) -> dict:
        ents: dict[str, int] = {}
        titles, periods = [], []
        for aid in c["atoms"]:
            a = atoms[aid]
            titles.append(f"[{a.get('layer')}] {a.get('title')}")
            if a.get("period_start") is not None:
                periods.append([a["period_start"], a.get("period_end")])
            for e in a.get("entities") or []:
                ents[e] = ents.get(e, 0) + 1
        top = sorted(ents.items(), key=lambda x: -x[1])[:14]
        return {
            "center": [round(c["center"][0], 3), round(c["center"][1], 3)],
            "atom_count": len(c["atoms"]),
            "actors": [{"slug": s, "label": (ledger.get(s) or {}).get("label_en") or s,
                        "type": (ledger.get(s) or {}).get("type")} for s, _ in top],
            "atom_titles": titles[:24],
            "periods_seen": periods[:12],
        }

    # ── C1·C2 사료권 정체 + 층위 ────────────────────────────────
    todo = [c for c in clusters
            if not any(sp.get("center") == [round(c["center"][0], 3), round(c["center"][1], 3)]
                       for sp in spheres.values())]
    cost = 0.0
    if dry:
        print(json.dumps(evidence(clusters[0]), ensure_ascii=False, indent=1)[:3000])
        return store
    flagged, bad = [], []
    attempt, pending = 0, list(todo)
    while pending and attempt < 3:
        attempt += 1
        trim = 24 if attempt == 1 else 12       # 재시도는 증거를 줄여 출력이 넘치지 않게 한다

        def task(c, trim=trim):
            ev = evidence(c)
            ev["atom_titles"] = ev["atom_titles"][:trim]
            user = ("이 사료권의 정체와 시대 층위를 세워라. 근거는 아래 원자·행위자다.\n\n"
                    + json.dumps(ev, ensure_ascii=False)
                    + "\n\nsphere_strata 를 한 번 호출해라. 층은 4~6개면 충분하다.")
            return llm.call(settings, system=_C_SYSTEM,
                            messages=[{"role": "user", "content": user}],
                            tools=[_C_TOOL], tool_choice={"type": "tool", "name": "sphere_strata"},
                            max_tokens=8000, deadline_s=300, role="fact")

        results = _run([(lambda c=c: task(c)) for c in pending], settings, f"패스C 층위 {attempt}회")
        retry = []
        for c, resp in zip(pending, results):
            if isinstance(resp, Exception):
                retry.append(c)
                continue
            cost += llm.spend(resp)
            d = _salvage_tool_input(llm.tool_input(resp, "sphere_strata") or {})
            raw = d.get("strata")
            if isinstance(raw, str):            # 살려낸 값이 아직 문자열이면 한 번 더 푼다
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = []
            strata = [s for s in (raw or []) if isinstance(s, dict)
                      and s.get("key") and s.get("start") is not None]
            strata.sort(key=lambda s: s["start"])
            # 살려낸(_salvage) 입력은 필드가 빠질 수 있다 — 스키마가 required 로 걸어도
            # 잘린 출력에서 복구한 dict 는 그 보장을 못 받는다. 예전엔 여기서 d["caution"]
            # 이 KeyError 를 내며 이미 성공한 41개 호출의 결과까지 통째로 날렸다.
            if not strata or not d.get("key") or not d.get("label_ko") or not d.get("label_en"):
                retry.append(c)
                if attempt >= 3:
                    bad.append({"center": [round(c["center"][0], 2), round(c["center"][1], 2)],
                                "atoms": len(c["atoms"]), "got": sorted(d.keys()),
                                "stop": getattr(resp, "stop_reason", None)})
                continue
            key = d["key"]
            if key in spheres and spheres[key].get("center") != [round(c["center"][0], 3),
                                                                 round(c["center"][1], 3)]:
                n = 2                       # 다른 군집이 같은 이름을 냈다 — 덮어쓰지 않는다
                while f"{key}-{n}" in spheres:
                    n += 1
                key = f"{key}-{n}"
            hits = _lint_nationalism(" ".join(
                [d.get("label_ko", ""), d.get("frame", ""), d.get("caution", "")]
                + [f"{s.get('label_ko','')} {s.get('basis','')}" for s in strata]))
            if hits:
                flagged.append({"sphere": d["key"], "words": hits})
            spheres[key] = {
                "key": key, "label_ko": d["label_ko"], "label_en": d["label_en"],
                "frame": d.get("frame") or "(기준 미기재 — 재추출 대상)",
                "caution": d.get("caution") or "(한계 미기재 — 재추출 대상)",
                "center": [round(c["center"][0], 3), round(c["center"][1], 3)],
                "atoms": c["atoms"], "atom_count": len(c["atoms"]),
                "strata": strata, "lint": hits,
                "extracted": _now(), "model": settings.model_fact,
            }
        pending = retry
        if pending:
            print(f"   재시도 대상 군집 {len(pending)} (증거를 줄여 다시)")

    if todo:
        print(f"   사료권 {len(spheres)} · 민족주의 린트 {len(flagged)}건 {flagged or ''} · ${cost:.3f}")
        if bad:
            print(f"   ! 3회 시도에도 층위를 못 세운 군집 {len(bad)}: {bad}")

    # ── C3 원자 → 층 ────────────────────────────────────────────
    by_period = by_llm = 0
    pend: list[tuple[str, dict]] = []      # (sphere_key, atom)
    for key, sp in spheres.items():
        for aid in sp["atoms"]:
            if aid in atom_era or aid not in atoms:
                continue
            a = atoms[aid]
            era = _era_of_period(sp["strata"], a.get("period_start"), a.get("period_end"))
            if era:
                atom_era[aid] = {"sphere": key, "era": era, "by": "period",
                                 "basis": f"원자 시기 {a.get('period_start')}–{a.get('period_end') or ''}"}
                by_period += 1
            else:
                pend.append((key, a))

    if pend:
        groups = _batches(pend, batch)
        print(f"   시기 없는 원자 {len(pend)} → 배치 {len(groups)}개 (연도 있는 {by_period}건은 층위와 겹쳐 자동 배정)")

        def payload(key: str, a: dict) -> dict:
            sp = spheres[key]
            detail = atlas.atom_detail(a["id"]) or {}
            return {
                "id": a["id"], "title": a.get("title"), "layer": a.get("layer"),
                "sphere": sp["label_en"],
                "strata": [{"key": s["key"], "label": s["label_en"],
                            "span": [s["start"], s.get("end")]} for s in sp["strata"]],
                "body": re.sub(r"\s+", " ", detail.get("body") or "")[:900],
            }

        def task(group):
            user = ("각 원자를 그 사료권의 층 하나에 배정해라(또는 atemporal).\n\n"
                    + json.dumps([payload(k, a) for k, a in group], ensure_ascii=False)
                    + "\n\natom_eras 를 한 번 호출해라.")
            return llm.call(settings, system=_E_SYSTEM,
                            messages=[{"role": "user", "content": user}],
                            tools=[_E_TOOL], tool_choice={"type": "tool", "name": "atom_eras"},
                            max_tokens=6000, deadline_s=300, role="fact")

        results = _run([(lambda g=g: task(g)) for g in groups], settings, "패스C 배정")
        want = {a["id"]: k for k, a in pend}
        drop = Counter()
        for resp in results:
            if isinstance(resp, Exception):
                continue
            cost += llm.spend(resp)
            for r in ((llm.tool_input(resp, "atom_eras") or {}).get("assignments") or []):
                aid, era = (r.get("atom") or "").strip(), (r.get("era") or "").strip()
                if aid not in want:
                    drop["대상 밖"] += 1
                    continue
                sp = spheres[want[aid]]
                if era != "atemporal" and era not in {s["key"] for s in sp["strata"]}:
                    drop["층 밖"] += 1
                    continue
                body = (atlas.atom_detail(aid) or {}).get("body") or ""
                if era != "atemporal" and not _quote_ok(r.get("quote", ""), body):
                    drop["근거 구절 불일치"] += 1
                    continue
                atom_era[aid] = {"sphere": want[aid], "era": era, "by": "llm",
                                 "basis": (r.get("quote") or "").strip()[:200]}
                by_llm += 1
        print(f"   배정 {by_llm} · 폐기 {sum(drop.values())} {dict(drop) or ''}")

    meta = dict(store.get("meta") or {})
    meta.update({
        "pass": "C", "built": _now(), "model": settings.model_fact,
        "cluster_km": CLUSTER_KM,
        "sphere_total": len(spheres), "assigned": len(atom_era),
        "assigned_by_period": by_period, "assigned_by_llm": by_llm,
        "lint_flagged": [k for k, sp in spheres.items() if sp.get("lint")],
        "cost_usd": round((meta.get("cost_usd") or 0.0) + cost, 4),
        "note": ("시대 층위는 사료권마다 다르다 — 하나의 세계 눈금이 아니다. "
                 "민족주의 시점(국민국가 투사·목적론·계승 주장)은 프롬프트에서 배제하고 린트로 표시한다."),
    })
    out = {"meta": meta, "spheres": dict(sorted(spheres.items())), "atom_era": atom_era}
    _save_json(PERIODS_JSON, out)
    print(f"   → {PERIODS_JSON.relative_to(ROOT)} · 사료권 {len(spheres)} · 배정 {len(atom_era)}/{len(atoms)}"
          f" · ${cost:.3f}\n")
    return out


# ══════════════════════════════════════════════════════════════════
#  검수
# ══════════════════════════════════════════════════════════════════
def sample_relations(n: int, seed: int) -> None:
    rows = [json.loads(l) for l in AUDIT_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if AUDIT_JSONL.exists() else []
    if not rows:
        print("   근거 로그가 없다 — 패스B 를 먼저 돌려라.")
        return
    ledger = _load_json(ENTITIES_JSON, {"actors": {}}).get("actors") or {}
    rnd = random.Random(seed)
    pick = rnd.sample(rows, min(n, len(rows)))
    print(f"\n  == 관계 검수 표본 {len(pick)}/{len(rows)} (seed {seed}) ============")
    for i, r in enumerate(pick, 1):
        lab = lambda s: (ledger.get(s) or {}).get("label_ko") or s
        arrow = "↔" if r["dir"] == "<->" else "→"
        per = ""
        if r.get("period_start") or r.get("period_end"):
            per = f"  [{r.get('period_start') or '?'}–{r.get('period_end') or ''}]"
        print(f"\n  {i:2}. {lab(r['src'])} {arrow} {lab(r['dst'])}"
              f"   〈{REL_KO[r['type']]} {r['type']}〉{per}")
        print(f"      원자 {r['atom']} ({r['layer']})")
        print(f"      \"{r['quote'][:180]}\"")
    print()


def sample_actors(n: int, seed: int) -> None:
    actors = _load_json(ENTITIES_JSON, {"actors": {}}).get("actors") or {}
    if not actors:
        print("   대장이 없다 — 패스A 를 먼저 돌려라.")
        return
    rnd = random.Random(seed)
    pick = rnd.sample(sorted(actors), min(n, len(actors)))
    print(f"\n  == 대장 검수 표본 {len(pick)}/{len(actors)} (seed {seed}) ============")
    for i, slug in enumerate(pick, 1):
        a = actors[slug]
        anc = a.get("anchor") or {}
        print(f"  {i:2}. {slug:34} {a['type']:8} {a['label_ko']} / {a['label_en']}")
        print(f"      {anc.get('lat')}, {anc.get('lng')} ({anc.get('from')}) · 원자 {a['atoms']}"
              f" · {a.get('basis')}")
    print()


def sample_eras(n: int) -> None:
    d = _load_json(PERIODS_JSON, {})
    spheres = d.get("spheres") or {}
    era_of = d.get("atom_era") or {}
    if not spheres:
        print("   사료권이 없다 — 패스C 를 먼저 돌려라.")
        return
    counts: dict[tuple[str, str], int] = {}
    for a in era_of.values():
        key = (a["sphere"], a["era"])
        counts[key] = counts.get(key, 0) + 1
    order = sorted(spheres.values(), key=lambda s: -s["atom_count"])[:n]
    print(f"\n  == 사료권 {len(order)}/{len(spheres)} · 시대 층위 ==============")
    for sp in order:
        print(f"\n  ◆ {sp['label_ko']} ({sp['label_en']}) · 원자 {sp['atom_count']}"
              f" · {sp['center'][0]:.1f},{sp['center'][1]:.1f}")
        print(f"    기준: {sp['frame']}")
        print(f"    한계: {sp['caution']}")
        if sp.get("lint"):
            print(f"    ! 민족주의 린트: {sp['lint']}")
        for st in sp["strata"]:
            span = f"{st['start']}–{st.get('end') or ''}"
            cnt = counts.get((sp["key"], st["key"]), 0)
            alt = f"  ({' / '.join(st.get('also_called') or [])})" if st.get("also_called") else ""
            print(f"      {span:>12}  {st['label_ko']}{alt}   원자 {cnt}")
            print(f"                    └ {st['basis']}")
    at = sum(1 for a in era_of.values() if a["era"] == "atemporal")
    print(f"\n   배정 {len(era_of)} (무시기 {at}) · 연도 유도 "
          f"{sum(1 for a in era_of.values() if a['by'] == 'period')}\n")


def stats() -> None:
    led = _load_json(ENTITIES_JSON, {})
    rel = _load_json(RELATIONS_JSON, {})
    actors = led.get("actors") or {}
    relations = rel.get("relations") or []
    print("\n  == 사이드카 현황 =================================")
    if actors:
        m = led.get("meta") or {}
        print(f"   대장   {len(actors):4} 행위자 · ${m.get('cost_usd', 0):.3f}"
              f" · {Counter(a['type'] for a in actors.values()).most_common()}")
        print(f"          앵커 없음 {sum(1 for a in actors.values() if not a.get('anchor'))}")
    else:
        print("   대장   (없음)")
    if relations:
        m = rel.get("meta") or {}
        print(f"   관계   {len(relations):4} 엣지 · 원자 {len(m.get('atoms_done') or [])} 처리"
              f" · ${m.get('cost_usd', 0):.3f}")
        print(f"          {Counter(r['type'] for r in relations).most_common()}")
        print(f"          강도 ≥2: {sum(1 for r in relations if r['strength'] >= 2)}"
              f" · 최근 폐기 {m.get('last_dropped')} · 강등 {m.get('last_demoted')}")
    else:
        print("   관계   (없음)")
    per = _load_json(PERIODS_JSON, {})
    spheres = per.get("spheres") or {}
    if spheres:
        m = per.get("meta") or {}
        strata = sum(len(sp["strata"]) for sp in spheres.values())
        ae = per.get("atom_era") or {}
        by = Counter(v.get("by") for v in ae.values())
        print(f"   시대   {len(spheres):4} 사료권 · 층 {strata} · 배정 {len(ae)}"
              f" (연도 유도 {by.get('period', 0)} · 본문 판독 {by.get('llm', 0)}"
              f" · 무시기 {sum(1 for v in ae.values() if v.get('era') == 'atemporal')})"
              f" · ${m.get('cost_usd', 0):.3f}")
        print(f"          민족주의 린트: {m.get('lint_flagged') or '없음'}")
    else:
        print("   시대   (없음)")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="ALTAIYA 추출 — 패스A(대장) · 패스B(관계)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("entities", help="패스A — 행위자 대장")
    a.add_argument("--batch", type=int, default=24)
    a.add_argument("--limit", type=int, default=None, help="앞에서 N개만(파일럿)")
    a.add_argument("--redo", action="store_true", help="기존 대장 무시하고 전체 재추출")
    a.add_argument("--dry-run", action="store_true", help="호출 없이 첫 배치 입력만 보여준다")

    b = sub.add_parser("relations", help="패스B — 관계 추출")
    b.add_argument("--batch", type=int, default=12)
    b.add_argument("--limit", type=int, default=None)
    b.add_argument("--redo", action="store_true")
    b.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("eras", help="패스C — 사료권과 시대 층위")
    c.add_argument("--batch", type=int, default=12)
    c.add_argument("--limit", type=int, default=None, help="큰 군집 N개만(파일럿)")
    c.add_argument("--redo", action="store_true")
    c.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("sample", help="검수 표본")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--actors", action="store_true", help="관계 대신 대장 표본")
    s.add_argument("--eras", action="store_true", help="사료권 층위 표본")

    sub.add_parser("stats", help="사이드카 현황")

    ns = ap.parse_args()
    if ns.cmd == "stats":
        return stats()
    if ns.cmd == "sample":
        if ns.eras:
            return sample_eras(ns.n)
        return sample_actors(ns.n, ns.seed) if ns.actors else sample_relations(ns.n, ns.seed)

    settings = load_settings()
    if not settings.has_anthropic:
        print("   ! ANTHROPIC_API_KEY 가 없다(.env)."); sys.exit(1)
    kw = dict(redo=ns.redo, batch=ns.batch, limit=ns.limit, dry=ns.dry_run)
    if ns.cmd == "entities":
        pass_a(settings, **kw)
    elif ns.cmd == "relations":
        pass_b(settings, **kw)
    else:
        pass_c(settings, **kw)
    llm.close_client()


if __name__ == "__main__":
    main()
