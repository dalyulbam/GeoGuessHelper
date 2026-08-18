"""ALTAIYA 추출기 — 패스A(행위자 대장) · 패스B(관계).

기획서 `docs/plan/knowledge-atlas_260816.html` 의 2패스를 그대로 구현한다.
원자는 **읽기만** 한다 — 산출물은 사이드카다.

    altaiya/data/entities.json           패스A · 행위자 대장 (슬러그 → 타입·표기·앵커)
    altaiya/data/relations.json          패스B · 관계 (유형·방향·시기·인용 원자)
    altaiya/data/relations.audit.jsonl   근거 구절 로그 (검수 전용 · 그래프는 쓰지 않는다)

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

    s = sub.add_parser("sample", help="검수 표본")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--actors", action="store_true", help="관계 대신 대장 표본")

    sub.add_parser("stats", help="사이드카 현황")

    ns = ap.parse_args()
    if ns.cmd == "stats":
        return stats()
    if ns.cmd == "sample":
        return sample_actors(ns.n, ns.seed) if ns.actors else sample_relations(ns.n, ns.seed)

    settings = load_settings()
    if not settings.has_anthropic:
        print("   ! ANTHROPIC_API_KEY 가 없다(.env)."); sys.exit(1)
    if ns.cmd == "entities":
        pass_a(settings, redo=ns.redo, batch=ns.batch, limit=ns.limit, dry=ns.dry_run)
    else:
        pass_b(settings, redo=ns.redo, batch=ns.batch, limit=ns.limit, dry=ns.dry_run)
    llm.close_client()


if __name__ == "__main__":
    main()
