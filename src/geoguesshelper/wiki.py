"""위키피디아 인제스트(패스 W) — 시대·왕조·국가 리스트를 원자로 만든다.

문제: 저장소의 원자는 방문한 장소와 그 확장에서만 자란다. 세계사의 뼈대 —
어느 대륙에 어떤 왕조·제국·국가가 언제 있었는가, 시대는 어떻게 구획되는가 —
는 방문과 무관하게 필요하다(강역 스케치의 대상 목록이기도 하다).

여기서 하는 것: 위키피디아의 큰 리스트 문서 4개를 위키텍스트로 받아 청크로
갈라, 모델이 원자로 압축한다.

    List of time periods            시대 구획 — 지질·고고·역사 시대와 연대
    List of dynasties               대륙·국가별 왕조 목록
    List of empires                 제국 목록
    List of former sovereign states 사라진 국가 목록

원칙:
  · 출처가 남는다 — 모든 원자의 sources 에 위키피디아 URL 이 실린다. 모델
    지식만 쓰는 확장(패스D, sources 비움)과 구분된다.
  · 원자 폭발을 막는다 — 왕조 목록 행마다 원자를 만들면 수천 개가 된다.
    나라·지역 단위 요약 원자(주요 왕조들을 본문에 연대와 함께 나열)를 기본으로,
    개별 원자는 큰 제국·장수 왕조에만 준다. 단, 청크에 나온 이름은 어느
    원자든 본문이나 엔티티로 실린다 — 요약은 압축이지 누락이 아니다.
  · 좌표 없음 — 리스트 문서에는 좌표가 없고, 지어내지 않는다(지오코딩 금지).
    scope=polity/period 로 저장돼 무장소로 서고, 국가 폴리곤 앵커나 손으로
    그린 강역이 나중에 지도 위치를 준다.
  · 체크포인트(wiki.json) — 청크 내용 해시로 완료를 기록한다. 중단해도 이어
    돌고, 문서가 개정되면 바뀐 청크만 다시 든다.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

from . import llm
from .config import Settings
from .knowledge import (
    LAYERS,
    SCOPES,
    _SCOPE_RADIUS_KM,
    Atom,
    _find_near_duplicate,
    _write_entity_note,
    atom_id,
    slug,
    store_for,
)

PAGES = (
    "List of time periods",
    "List of dynasties",
    "List of empires",
    "List of former sovereign states",
)
KINDS = ("period", "dynasty", "kingdom", "empire", "state", "group")
CHUNK_CHARS = 18_000

_API = "https://en.wikipedia.org/w/api.php"
_UA = {"User-Agent": "GeoGuessHelper-knowledge/0.1 (personal research tool)"}

_WIKI_TOOL = {
    "name": "wiki_atoms",
    "description": "Compress a chunk of a Wikipedia list article into reusable knowledge atoms.",
    "input_schema": {
        "type": "object",
        "required": ["atoms"],
        "properties": {
            "atoms": {
                "type": "array",
                "description": (
                    "3-10 atoms per chunk. Group minor entries into per-country/region summary "
                    "atoms; give individual atoms only to major long-lived polities or era "
                    "systems. EVERY named dynasty/state/period in the chunk must appear in some "
                    "atom's body or entities — summarizing compresses, it must not drop."
                ),
                "items": {
                    "type": "object",
                    "required": ["kind", "layer", "scope", "title", "body", "tags", "entities"],
                    "properties": {
                        "kind": {"type": "string", "enum": list(KINDS)},
                        "layer": {"type": "string", "enum": list(LAYERS)},
                        "scope": {
                            "type": "string", "enum": list(SCOPES),
                            "description": "'polity' for states/dynasties, 'period' for time systems.",
                        },
                        "title": {"type": "string", "description": "Short noun phrase, <=80 chars."},
                        "body": {
                            "type": "string",
                            "description": (
                                "2-5 sentences. Dense and factual, ONLY from this chunk: names, "
                                "years, regions, succession. A group atom names its members with "
                                "dates (e.g. 'Joseon (1392-1897)'). No outside knowledge, no "
                                "speculation."
                            ),
                        },
                        "tags": {"type": "array", "items": {"type": "string"},
                                 "description": "lowercase-hyphenated, 3-10. Include continent/country."},
                        "entities": {"type": "array", "items": {"type": "string"},
                                     "description": "Canonical lowercase-hyphenated slugs, <=8."},
                        "period_start": {"type": ["integer", "null"], "description": "Year; negative = BCE."},
                        "period_end": {"type": ["integer", "null"], "description": "null = ongoing/unknown."},
                    },
                },
            },
        },
    },
}

_WIKI_SYSTEM = (
    "You are a knowledge-base curator for a geography/history research tool. You receive one "
    "chunk of the wikitext of a Wikipedia LIST article (time periods, dynasties, empires, or "
    "former states). Compress it into reusable atoms.\n\n"
    "RULES\n"
    "- Write in English — this is the canonical store.\n"
    "- Use ONLY what the chunk states. Never add dates, rulers or territories from memory; "
    "never guess. If the chunk gives no dates for an entry, include the name without dates.\n"
    "- Granularity: prefer one GROUP atom per country/region/section that names its member "
    "dynasties or states WITH their years, over one atom per row. Give an individual atom only "
    "to entries the chunk treats as major (empires, dynasties of large states, era systems).\n"
    "- Completeness beats brevity here: every named entry of the chunk must appear in some "
    "atom's body or entities list. Do not silently drop rows.\n"
    "- Wikitext noise (templates, pipes, footnote markers) is not content — read through it.\n"
    "- Tags and entities are retrieval keys — stable canonical slugs (e.g. qing-dynasty, "
    "china, east-asia, list-of-dynasties). period_start/period_end: negative years = BCE."
)


def _fetch_wikitext(title: str) -> tuple[str, int]:
    """문서 위키텍스트와 revid. 리다이렉트는 따라간다.

    urllib 대신 curl 을 쓴다 — 이 환경의 파이썬 ssl 은 OPENSSL_Applink 오류로
    HTTPS 를 못 연다(경로에 얹힌 서드파티 OpenSSL DLL 탓). curl 은 자체 TLS 라 무관하다.
    """
    q = urllib.parse.urlencode({
        "action": "parse", "page": title, "prop": "wikitext|revid",
        "format": "json", "formatversion": "2", "redirects": "1",
    })
    r = subprocess.run(
        ["curl", "-sS", "--max-time", "60", "-A", _UA["User-Agent"], f"{_API}?{q}"],
        capture_output=True, timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl 실패({title}): {r.stderr.decode('utf-8', 'replace')[:200]}")
    d = json.loads(r.stdout.decode("utf-8"))
    p = d.get("parse") or {}
    return p.get("wikitext") or "", p.get("revid") or 0


_REF_RE = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.S | re.I)
_FILE_RE = re.compile(r"^\s*\[\[(?:File|Image):.*$", re.M)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _clean(wikitext: str) -> str:
    """각주·파일·주석을 걷어 토큰을 아낀다 — 본문 표·목록은 그대로 둔다."""
    t = _COMMENT_RE.sub("", wikitext)
    t = _REF_RE.sub("", t)
    t = _FILE_RE.sub("", t)
    return re.sub(r"\n{3,}", "\n\n", t)


def _chunks(text: str) -> list[str]:
    """섹션(== 헤더) 경계 우선으로 CHUNK_CHARS 이하 청크를 만든다."""
    sections = re.split(r"(?=\n==[^=])", text)
    out: list[str] = []
    buf = ""
    for sec in sections:
        while len(sec) > CHUNK_CHARS:            # 초대형 섹션은 줄 경계로 강제 분할
            cut = sec.rfind("\n", 0, CHUNK_CHARS)
            cut = cut if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
            if buf:
                out.append(buf)
                buf = ""
            out.append(sec[:cut])
            sec = sec[cut:]
        if len(buf) + len(sec) > CHUNK_CHARS and buf:
            out.append(buf)
            buf = ""
        buf += sec
    if buf.strip():
        out.append(buf)
    return [c for c in out if len(c.strip()) > 200]


def _state_path(settings: Settings) -> Path:
    return Path(settings.knowledge_dir) / "wiki.json"


def _load_state(settings: Settings) -> dict:
    p = _state_path(settings)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "chunks_done" in d:
                return d
        except Exception:  # noqa: BLE001
            pass
    return {
        "meta": {"pass": "W", "note": "위키피디아 리스트 인제스트 — 시대·왕조·제국·국가"},
        "chunks_done": {}, "created": 0, "merged": 0, "skipped": 0, "cost_usd": 0.0,
    }


def _save_state(settings: Settings, state: dict) -> None:
    state["meta"]["updated"] = time.time()
    p = _state_path(settings)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _absorb(settings: Settings, raw_atoms: list, url: str, state: dict) -> list[str]:
    """모델 산출 → 검증 → 중복 병합 또는 신규 저장. expand._absorb 와 같은 규율."""
    st = store_for(settings)
    touched: set[str] = set()
    out: list[str] = []
    for raw in raw_atoms or []:
        if not isinstance(raw, dict):
            continue
        body = (raw.get("body") or "").strip()
        title = (raw.get("title") or "").strip()
        kind = raw.get("kind") if raw.get("kind") in KINDS else "group"
        if not body or not title:
            state["skipped"] += 1
            continue
        layer = raw.get("layer") if raw.get("layer") in LAYERS else "history"
        scope = raw.get("scope") if raw.get("scope") in SCOPES else "polity"
        tags = sorted({slug(t) for t in (raw.get("tags") or []) if t}
                      | {"wiki", f"wiki-{kind}"})[:14]
        ents = sorted({slug(e) for e in (raw.get("entities") or []) if e})[:8]

        aid = atom_id(layer, ents, body)
        existing = st.load(aid) or _find_near_duplicate(st, layer, ents, tags, scope)
        if existing is not None:
            existing.tags = sorted(set(existing.tags) | set(tags))[:16]
            if url not in existing.sources:
                existing.sources = (existing.sources + [url])[-8:]
            st.save(existing)
            touched.update(existing.entities)
            state["merged"] += 1
            out.append(existing.id)
            continue

        atom = Atom(
            id=aid, layer=layer, scope=scope, title=title[:120], body=body,
            tags=tags, entities=ents,
            period_start=raw.get("period_start") if isinstance(raw.get("period_start"), int) else None,
            period_end=raw.get("period_end") if isinstance(raw.get("period_end"), int) else None,
            lat=None, lng=None,                      # 리스트 문서엔 좌표가 없다 — 지어내지 않는다
            radius_km=_SCOPE_RADIUS_KM.get(scope, 0),
            cell="", lang="en", sources=[url], refs=[], reports=[],
        )
        st.save(atom)
        touched.update(ents)
        state["created"] += 1
        out.append(atom.id)
    for e in touched:
        _write_entity_note(st, e)
    return out


def run(settings: Settings, *, limit: int = 0, parallel: int = 3, dry_run: bool = False,
        log=print) -> dict:
    """인제스트 실행. limit=0 이면 남은 청크 전부."""
    state = _load_state(settings)
    todo: list[tuple[str, str, str, str]] = []   # (page, url, chunk_key, chunk_text)
    total = 0
    for page in PAGES:
        wikitext, revid = _fetch_wikitext(page)
        if not wikitext:
            log(f"[wiki] 못 받음: {page}")
            continue
        url = "https://en.wikipedia.org/wiki/" + page.replace(" ", "_")
        chunks = _chunks(_clean(wikitext))
        total += len(chunks)
        for i, ch in enumerate(chunks):
            key = hashlib.sha256(ch.encode("utf-8")).hexdigest()[:12]
            if key in state["chunks_done"]:
                continue
            todo.append((page, url, key, ch))
        log(f"[wiki] {page} — rev {revid} · 청크 {len(chunks)}")
    if limit:
        todo = todo[:limit]
    log(f"[wiki] 전체 청크 {total} · 남은 것 {len(todo)} (완료 {len(state['chunks_done'])})")
    if dry_run or not todo:
        return state

    def _one(item):
        page, url, key, ch = item
        user = (f"ARTICLE: {page}\nSOURCE: {url}\n\n--- WIKITEXT CHUNK ---\n{ch}\n\n"
                "Compress this chunk into atoms. Call wiki_atoms once.")
        return llm.call(
            settings,
            system=_WIKI_SYSTEM,
            messages=[{"role": "user", "content": user}],
            tools=[_WIKI_TOOL],
            tool_choice={"type": "tool", "name": "wiki_atoms"},
            max_tokens=8000,
            effort=settings.knowledge_effort,
            deadline_s=max(180.0, settings.knowledge_timeout_s),
            role="fact",
        )

    for gi in range(0, len(todo), parallel):
        group = todo[gi:gi + parallel]
        results = llm.gather([lambda it=it: _one(it) for it in group], settings, limit=parallel)
        for item, resp in zip(group, results):
            page, url, key, _ch = item
            if isinstance(resp, Exception):
                log(f"[wiki] 청크 실패({page} {key}): {resp}")
                continue
            state["cost_usd"] = round(state["cost_usd"] + llm.spend(resp), 4)
            data = llm.tool_input(resp, "wiki_atoms") or {}
            ids = _absorb(settings, data.get("atoms"), url, state)
            state["chunks_done"][key] = len(ids)
            _save_state(settings, state)
            log(f"[wiki] {gi + group.index(item) + 1}/{len(todo)} {page[:28]} → 원자 {len(ids)} "
                f"(누적 신규 {state['created']} · 병합 {state['merged']} · ${state['cost_usd']})")
    return state
