"""원자 대화 — 문맥(원자·분자·이미지)만을 근거로 답하고, 제안은 인용 관문을 거쳐 claim 원자가 된다.

기획: docs/plan/atom-dialogue_260906.html §대화 계약·§제안→원자·§원칙.
명세: docs/plan/impl-spec_260907.md §4 (WP-A3).

왜 이 모듈이 있는가
    원자는 1,300개 가까이 쌓였는데, 원자와 **말할** 방법이 없었다. "이 두 원자는 같은 얘긴가",
    "이 지붕이면 핀란드 아닌가" 같은 질문은 회상(recall)·종합(synthesize)이 답하지 않는다.
    대화는 그 질문을 받되, 답의 근거는 **닫힌 세계**(이 세션에 실린 원자와 이미지)로 묶는다 —
    모든 문장이 [[atm_id]] 나 (image N) 을 인용하고, 문맥 밖 것은 "원자에 없다"고 말한다.

승인 관문이 없는 이유(사용자 결정 260907, 명세 §0)
    기획 260906 은 제안이 사람 승인을 거쳐야 원자가 됐다. 지금은 그 관문을 두 가지로 바꿨다:
      (1) 기계 관문 — 제안은 문맥 원자/이미지 인용이 ≥1 이어야 한다(knowledge.ingest_claims 가 검사).
      (2) 증거 채점 — 들어온 원자는 kind=claim·origin=dialogue 표식을 달고, 이후 정정 루프가
          회상돼 쓰인 원자의 hits/misses 를 채점해 오답만 뒷받침한 원자를 retracted 로 내린다.
    틀린 원자는 더 많은 정보를 흡수하면서 고쳐진다 — 사람이 하나씩 승인하는 것보다 빠르다.

프롬프트 캐시 배치(analyze.py 와 같은 이유)
    tools → system(고정) → [이미지들 … cache_control] → 원자 블록(cache_control) → 대화 이력 →
    마지막 사용자 메시지 + 언어 지시. 턴이 이어져도 이미지·원자 토큰은 캐시에 맞는다. 언어
    지시문을 system 에 넣으면 언어가 바뀔 때 접두사가 깨져 이미지가 한 번도 캐시되지 않는다.

이 모듈은 knowledge.py 의 저장 API 만 쓴다 — 원자 파일을 직접 만들거나 고치지 않는다.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import sys
import time
from pathlib import Path

from . import i18n, knowledge, llm
from .config import Settings, load_settings

_ATM_RE = re.compile(r"\[\[(atm_[0-9a-f]{12})\]\]")
_IMG_RE = re.compile(r"\(image\s+(\d+)\)", re.I)
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# ── 시스템 규칙(고정 — 캐시 접두사) ────────────────────────────────────────────
# 기획 260906 §4.2 의 규칙을 승인 없는 버전으로 고쳤다: 제안은 곧바로 원자가 되므로 "인용 없는
# 제안은 기계적으로 기각된다"는 것과 "재사용 가능한 것만"을 모델에게 분명히 말한다.
_SYSTEM = """You are a knowledge-atlas interlocutor for a geography research tool. Your evidence is a
CLOSED WORLD: the CONTEXT ATOMS and the IMAGES given in this conversation. Nothing else is evidence.

RULES
1. Cite. Every factual sentence in `answer` that rests on the context cites its source inline:
   [[atm_id]] for an atom, (image N) for the N-th image (0-based, in the order shown). Put every id
   you used into `cited_atoms` / `cited_images`. Never cite an id that is not in the context — such
   citations are removed mechanically and count against the answer.
2. Outside knowledge. If the atoms do not say it, say so plainly ("not in the atoms: ..."). You MAY
   then answer from general knowledge, clearly marked as general knowledge — but general knowledge
   can NEVER back a proposal.
3. Proposals become atoms WITHOUT human review. The only gate is mechanical: a proposal whose `cites`
   contains no context atom id and no valid image:N is rejected. So propose only what the cited atoms
   or images actually support, and only REUSABLE knowledge — never a bare restatement of a context
   atom, never something true of one street only. Title <= 80 chars; body 1-3 dense, self-contained
   sentences; English. Fewer, solid proposals beat many. Zero proposals is a fine answer.
   `type`: atom (a new claim) | link (two context atoms are the same fact or directly related —
   atom_a, atom_b, reason) | dispute (a context atom is wrong or misleading — target_atom, reason;
   this is scored as a miss against that atom, so be sure).
4. Counterfactual discriminators first. The valuable output is not "this is Norway" but "if this were
   Finland you would see X INSTEAD; here you see Y". When the user proposes a different place, say
   what would have appeared instead. A discriminator proposal puts BOTH candidates in `entities`.
5. Coordinates are ground truth. Never re-guess the country/region of a capture that carries
   coordinates; never geocode. Proposals carry no coordinates — they inherit them from cited atoms.
6. Faces and license-plate characters are blurred by design: never identify a person, never read a
   plate. Plate shape and colour as a country cue is fine.
7. A MOLECULE document, if given, is a machine-made hypothesis — background, not evidence. Cite its
   member atoms, not the document.
8. Vocabulary is closed: layer / category / scope only from the enums in the tool. Tags and entities
   are lowercase-hyphenated slugs (norway, roof-facade, utility-pole).
9. Call `dialogue_turn` exactly once. The output language for `answer` is stated at the end of the
   user message; proposal title / body / tags / entities stay English (the store is canonical English).
"""

_TOOL = {
    "name": "dialogue_turn",
    "description": "Your reply for this turn: the answer with its citations, plus zero or more proposals "
                   "that will be ingested as claim-grade atoms if (and only if) they cite the context.",
    "input_schema": {
        "type": "object",
        "required": ["answer", "cited_atoms", "cited_images", "proposals"],
        "properties": {
            "answer": {
                "type": "string",
                "description": "The reply, in the requested output language. Cite inline as [[atm_id]] and "
                               "(image N). Say 'not in the atoms' where the context does not cover something.",
            },
            "cited_atoms": {
                "type": "array", "items": {"type": "string"},
                "description": "Every context atom id used in `answer` (atm_...). Context atoms only.",
            },
            "cited_images": {
                "type": "array", "items": {"type": "integer"},
                "description": "Every image index used in `answer` (0-based).",
            },
            "proposals": {
                "type": "array",
                "description": "Reusable, citable knowledge that this turn established. Empty is fine.",
                "items": {
                    "type": "object",
                    "required": ["type", "cites"],
                    "properties": {
                        "type": {"type": "string", "enum": ["atom", "link", "dispute"]},
                        "title": {"type": "string", "description": "atom: short noun phrase, <= 80 chars, English."},
                        "body": {"type": "string", "description": "atom: 1-3 sentences, English. A discriminator "
                                                                  "says what appears INSTEAD in the other candidate."},
                        "layer": {"type": "string", "enum": list(knowledge.LAYERS)},
                        "category": {"type": "string", "enum": sorted(knowledge.ALL_CATEGORIES),
                                     "description": "Closed vocabulary. 'other' only when nothing fits."},
                        "scope": {"type": "string", "enum": list(knowledge.SCOPES)},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "2-8 slugs."},
                        "entities": {"type": "array", "items": {"type": "string"},
                                     "description": "Slugs of the places/polities/styles this is about. "
                                                    "A discriminator lists BOTH candidates."},
                        "cites": {
                            "type": "array", "items": {"type": "string"},
                            "description": "REQUIRED, >= 1: context atom ids ('atm_...') and/or 'image:N'. "
                                           "Proposals with no valid citation are rejected mechanically.",
                        },
                        "atom_a": {"type": "string", "description": "link: first context atom id."},
                        "atom_b": {"type": "string", "description": "link: second context atom id."},
                        "target_atom": {"type": "string", "description": "dispute: the context atom being disputed."},
                        "reason": {"type": "string", "description": "link/dispute: why, one or two sentences."},
                    },
                },
            },
        },
    },
}


# ── 세션·문맥 조립 ────────────────────────────────────────────────────────────
def new_session_id() -> str:
    return f"dlg_{time.strftime('%y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


def _safe_session(session: str | None) -> str:
    """세션 id 는 로그 파일명이 된다 — 경로 조각이 섞이면 만들지 않고 새 id 를 준다."""
    s = (session or "").strip()
    return s if s and _SESSION_RE.match(s) else new_session_id()


def _b64(path: Path) -> tuple[str, str]:
    """analyze._b64 와 같은 규칙(중복 6줄 — 대화 모듈이 비전 분석 모듈에 기대지 않게 한다)."""
    data = base64.standard_b64encode(path.read_bytes()).decode()
    suf = path.suffix.lower()
    media = "image/jpeg" if suf in (".jpg", ".jpeg") else "image/webp" if suf == ".webp" else "image/png"
    return data, media


def resolve_images(settings: Settings, images) -> list[Path]:
    """captures_dir 안의 실제 파일만. 하위 폴더(blind/ 크롭)는 허용, 폴더 탈출은 거른다.

    상한은 dialogue_max_images — 이미지 한 장이 수천 토큰이라 대화가 길어질수록 매 턴 비용을
    좌우한다(캐시가 맞으면 0.1배지만, 어긋난 턴은 전액이다).
    """
    root = Path(settings.captures_dir).resolve()
    # 0 은 "이미지 없이"라는 뜻이 있는 값이다(예산 초과 시 이미지를 내리고 원자만으로 계속) — or 로 뭉개지 않는다.
    cap = 4 if settings.dialogue_max_images is None else max(0, int(settings.dialogue_max_images))
    out: list[Path] = []
    for name in images or []:
        if len(out) >= cap:
            break
        if not isinstance(name, str) or not name.strip():
            continue
        rel = Path(name.strip().replace("\\", "/"))
        p = (root / rel).resolve() if not rel.is_absolute() else rel
        try:
            p.relative_to(root)
        except ValueError:
            continue
        if p.is_file() and p not in out:
            out.append(p)
    return out


def molecule_context(settings: Settings, molecule_id: str | None) -> tuple[list[str], dict | None]:
    """분자 → (멤버+주변 원자 id, 문서 요약). 분자 index/문서가 없으면 ([], None).

    분자는 기계 산출 가설이라 문서 본문은 배경으로만 실린다(system 규칙 7). 원자 id 는 멤버가
    먼저, 주변(periphery)이 뒤 — 문맥 상한에 걸리면 주변부터 빠지도록.
    """
    if not molecule_id:
        return [], None
    mol_dir = Path(settings.knowledge_dir) / "molecule"
    try:
        idx = json.loads((mol_dir / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {"id": molecule_id, "error": "molecule index missing"}
    m = (idx.get("molecules") or {}).get(molecule_id)
    if not isinstance(m, dict):
        return [], {"id": molecule_id, "error": "unknown molecule"}
    members = [a for a in (m.get("atoms") or []) if isinstance(a, str)]
    periphery = [a for a in (m.get("periphery") or []) if isinstance(a, str) and a not in members]
    doc_text = ""
    doc = m.get("doc")
    if isinstance(doc, str) and doc:
        try:
            raw = (mol_dir / Path(doc).name).read_text(encoding="utf-8")
            body = re.sub(r"^---\r?\n.*?\r?\n---(?:\r?\n|$)", "", raw, count=1, flags=re.S)
            # 멤버·주변 원자 목록 절은 문맥 원자 블록과 중복이라 잘라낸다.
            body = re.split(r"\n## 멤버 원자\b", body, maxsplit=1)[0]
            doc_text = body.strip()[:6000]
        except OSError:
            doc_text = ""
    info = {
        "id": molecule_id,
        "name_ko": m.get("name_ko"), "name_en": m.get("name_en"), "slug": m.get("slug"),
        "named": bool(m.get("named")), "n": m.get("n"), "cohesion": m.get("cohesion"),
        "confidence": m.get("confidence"), "status": "hypothesis",
        "members": members, "periphery": periphery,
        "missing_atoms": [x for x in (m.get("missing_atoms") or []) if isinstance(x, str)][:8],
        "doc": doc_text,
    }
    return members + periphery, info


def load_context_atoms(settings: Settings, atom_ids, molecule_id: str | None = None):
    """문맥 원자 로드 — 지정 원자 → 분자 멤버 → 분자 주변 순, dialogue_max_context_atoms 상한.

    retracted 원자도 사용자가 **명시적으로** 지정했다면 실린다(그 원자를 두고 말하고 싶을 수
    있다) — 단 블록에 status 가 찍혀 모델이 안다. 분자 경로로 들어오는 것은 active 만.
    """
    st = knowledge.store_for(settings)
    cap = int(settings.dialogue_max_context_atoms or 24)
    mol_ids, mol_info = molecule_context(settings, molecule_id)
    explicit = [a for a in (atom_ids or []) if isinstance(a, str) and a.startswith("atm_")]
    atoms: list[knowledge.Atom] = []
    seen: set[str] = set()
    missing: list[str] = []
    for aid in list(dict.fromkeys(explicit)) + [a for a in mol_ids if a not in explicit]:
        if len(atoms) >= cap:
            break
        if aid in seen:
            continue
        seen.add(aid)
        a = st.load(aid)
        if a is None:
            missing.append(aid)
            continue
        if aid not in explicit and (a.status or "active") == "retracted":
            continue
        atoms.append(a)
    return atoms, mol_info, missing


def _atom_lines(atoms: list[knowledge.Atom]) -> str:
    """as_prompt_block 과 같은 꼴 + kind/status/confusions — 대화는 이 표식으로 '주장'과 '사실'을 가른다."""
    lines = []
    for a in atoms:
        period = ""
        if a.period_start is not None or a.period_end is not None:
            period = f" [{a.period_start or '?'}–{a.period_end or '?'}]"
        flags = []
        if (a.kind or "fact") != "fact":
            flags.append(a.kind)
        if (a.status or "active") != "active":
            flags.append(a.status)
        if a.confusions:
            flags.append("confusions=" + ",".join(a.confusions[:3]))
        if a.hits or a.misses:
            flags.append(f"hits={a.hits} misses={a.misses}")
        flag = f" {{{' '.join(flags)}}}" if flags else ""
        tags = " ".join(f"#{t}" for t in a.tags[:8])
        ents = " ".join(f"@{e}" for e in a.entities[:6])
        coord = f" @({a.lat:.3f},{a.lng:.3f})" if a.lat is not None and a.lng is not None else ""
        lines.append(f"- [[{a.id}]] ({a.layer}/{a.scope}/{a.category or 'other'}){period}{flag}{coord} "
                     f"**{a.title}** — {a.body} {tags} {ents}".rstrip())
    return "\n".join(lines)


def context_block(atoms: list[knowledge.Atom], mol_info: dict | None, n_images: int) -> str:
    parts = [f"CONTEXT ATOMS ({len(atoms)}) — the closed world. Cite as [[atm_id]]."]
    parts.append(_atom_lines(atoms) if atoms else "(none)")
    if mol_info and not mol_info.get("error"):
        head = (f"MOLECULE {mol_info['id']} — {mol_info.get('name_ko') or '(unnamed)'} / "
                f"{mol_info.get('name_en') or ''} · status hypothesis (machine-made; background, not evidence)")
        mem = " ".join(f"[[{a}]]" for a in mol_info.get("members") or [])
        per = " ".join(f"[[{a}]]" for a in mol_info.get("periphery") or [])
        parts.append("\n" + head + f"\nmembers: {mem or '-'}\nperiphery (absorbed neighbours, not members): {per or '-'}")
        if mol_info.get("missing_atoms"):
            parts.append("missing atoms the molecule document hypothesised: " + " · ".join(mol_info["missing_atoms"]))
        if mol_info.get("doc"):
            parts.append("molecule document:\n" + mol_info["doc"])
    if n_images:
        parts.append(f"\nIMAGES: {n_images} capture(s) above, cite as (image N), N = 0..{n_images - 1}.")
    else:
        parts.append("\nIMAGES: none in this session.")
    return "\n".join(parts)


def normalize_messages(messages) -> list[dict]:
    """대화 이력 → user/assistant 교대 문자열 메시지. 마지막은 user 여야 한다(ValueError)."""
    out: list[dict] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):  # 블록 배열이 오면 텍스트만 이어 붙인다
            content = "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
        text = str(content or "").strip()
        if not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text     # 같은 역할 연속은 한 메시지로(API 는 교대를 요구한다)
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if not out or out[-1]["role"] != "user":
        raise ValueError("messages 는 비어 있지 않고 마지막이 user 여야 한다")
    return out


def _lang_directive(lang: str) -> str:
    name = {c: e for c, _, e in i18n.LANGUAGES}.get(i18n.normalize(lang), "English")
    return (f"\n\nOUTPUT LANGUAGE: write `answer` in {name}. Keep [[atm_id]] and (image N) citations verbatim. "
            "Proposal title/body/tags/entities stay English slugs/prose (the store is canonical English).")


def build_messages(history: list[dict], *, image_paths: list[Path], ctx_text: str, lang: str) -> list[dict]:
    """캐시 배치대로 메시지를 짠다 — 첫 user 메시지 = [이미지…(마지막 cache_control), 문맥 블록(cache_control), 첫 질문]."""
    first: list[dict] = []
    for i, p in enumerate(image_paths):
        data, media = _b64(p)
        block: dict = {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
        if i == len(image_paths) - 1:
            block["cache_control"] = {"type": "ephemeral"}
        first.append(block)
    first.append({"type": "text", "text": ctx_text, "cache_control": {"type": "ephemeral"}})
    msgs: list[dict] = []
    for i, m in enumerate(history):
        text = m["content"]
        if i == len(history) - 1:
            text += _lang_directive(lang) + "\n\nCall dialogue_turn once."
        if i == 0:
            msgs.append({"role": "user", "content": first + [{"type": "text", "text": text}]})
        else:
            msgs.append({"role": m["role"], "content": text})
    return msgs


# ── 후처리 ────────────────────────────────────────────────────────────────────
def _post(data: dict, *, ctx_ids: set[str], n_images: int, fallback_text: str) -> tuple[dict, list[str]]:
    """도구 출력을 정규화하고 문맥 밖 인용을 걷어낸다. 반환 (정규화 결과, dropped_citations)."""
    answer = str(data.get("answer") or "").strip() or fallback_text
    dropped: list[str] = []
    cited: list[str] = []
    for c in list(data.get("cited_atoms") or []) + _ATM_RE.findall(answer):
        c = str(c).strip()
        if not c:
            continue
        if c in ctx_ids:
            if c not in cited:
                cited.append(c)
        elif c not in dropped:
            dropped.append(c)
    imgs: list[int] = []
    for c in list(data.get("cited_images") or []) + _IMG_RE.findall(answer):
        try:
            n = int(c)
        except (TypeError, ValueError):
            continue
        if 0 <= n < n_images:
            if n not in imgs:
                imgs.append(n)
        elif f"image:{n}" not in dropped:
            dropped.append(f"image:{n}")
    props: list[dict] = []
    for p in data.get("proposals") or []:
        if not isinstance(p, dict):
            continue
        q = {k: p.get(k) for k in ("type", "title", "body", "layer", "category", "scope",
                                   "atom_a", "atom_b", "target_atom", "reason")}
        q["type"] = q["type"] if q["type"] in ("atom", "link", "dispute") else "atom"
        q["tags"] = [str(t) for t in (p.get("tags") or []) if t]
        q["entities"] = [str(e) for e in (p.get("entities") or []) if e]
        q["cites"] = [str(c) for c in (p.get("cites") or []) if c]
        # 좌표는 받지 않는다 — 인용 원자에서 물려받는다(LLM 지오코딩 금지, system 규칙 5).
        props.append(q)
    return {"answer": answer, "cited_atoms": cited, "cited_images": imgs, "proposals": props}, dropped


def _gate_links(proposals: list[dict], ctx_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """link 는 두 원자가 모두 문맥에 있어야 한다 — ingest_claims 는 저장소 존재만 보므로 닫힌 세계는 여기서 지킨다."""
    keep, rejected = [], []
    for p in proposals:
        if p["type"] == "link" and not (p.get("atom_a") in ctx_ids and p.get("atom_b") in ctx_ids):
            rejected.append({"type": "link", "why": "atom not in context",
                             "atom_a": p.get("atom_a"), "atom_b": p.get("atom_b")})
            continue
        keep.append(p)
    return keep, rejected


def _append_log(settings: Settings, session: str, row: dict) -> Path:
    d = Path(settings.knowledge_dir) / "dialogue"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session}.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return p


# ── 한 턴 ─────────────────────────────────────────────────────────────────────
def chat(settings: Settings, *, atom_ids=(), molecule_id: str | None = None, images=(), messages=(),
         effort: str | None = None, lang: str = "ko", session: str | None = None) -> dict:
    """대화 한 턴. 문맥을 조립해 모델을 한 번 부르고, 제안을 knowledge.ingest_claims 로 적재한다.

    반환: {answer, cited_atoms, cited_images, proposals, ingested, dropped_citations, cost_usd, model,
           effort, session, context:{atoms, molecule, images}, lang, cache, turn}
    예외: ValueError(문맥 없음·messages 형식) · llm.LLMUnavailable(키 없음) · llm.LLMTimeout · 기타 API 예외.
    호출부(API)가 각각 422/503/504/500 으로 옮긴다.
    """
    session = _safe_session(session)
    lang = i18n.normalize(lang)
    effort = effort if effort in _EFFORTS else (settings.dialogue_effort or None)
    history = normalize_messages(messages)

    atoms, mol_info, missing = load_context_atoms(settings, atom_ids, molecule_id)
    image_paths = resolve_images(settings, images)
    if not atoms and not image_paths:
        raise ValueError("문맥이 비어 있다 — 존재하는 원자 id(atoms), 분자 id(molecule) 또는 캡처(images)가 하나는 있어야 한다"
                         + (f" (없는 원자: {', '.join(missing[:5])})" if missing else ""))
    ctx_ids = {a.id for a in atoms}
    ctx_text = context_block(atoms, mol_info, len(image_paths))
    msgs = build_messages(history, image_paths=image_paths, ctx_text=ctx_text, lang=lang)
    role = "vision" if image_paths else "reason"

    resp = None
    data = None
    total_cost = 0.0   # 재시도해도 첫 호출은 이미 과금됐다 — 마지막 응답 것만 남기면 비용이 새어나간다(codex 감사 260908)
    for attempt in range(2):   # 도구 입력이 비면(모델이 본문으로 써버린 경우) 강제 tool_choice 로 1회 재시도
        resp = llm.call(
            settings, system=_SYSTEM, messages=msgs, tools=[_TOOL],
            tool_choice={"type": "tool", "name": "dialogue_turn"},
            max_tokens=int(settings.dialogue_max_tokens or 6000), effort=effort,
            deadline_s=settings.dialogue_timeout_s, role=role,
        )
        total_cost += llm.spend(resp)
        data = llm.tool_input(resp, "dialogue_turn")
        if isinstance(data, dict) and (data.get("answer") or data.get("proposals")):
            break
    cost = round(total_cost, 4)
    model = llm.used_model(resp)
    data = data if isinstance(data, dict) else {}

    out, dropped = _post(data, ctx_ids=ctx_ids, n_images=len(image_paths), fallback_text=llm.text_of(resp))
    proposals, pre_rejected = _gate_links(out["proposals"], ctx_ids)
    ingested = knowledge.ingest_claims(
        settings, proposals=proposals, cite_pool=set(ctx_ids), n_images=len(image_paths),
        ctx={"job": session, "mode": "dialogue"}, report_file="",
    )
    ingested["rejected"] = list(ingested.get("rejected") or []) + pre_rejected

    log_path = Path(settings.knowledge_dir) / "dialogue" / f"{session}.jsonl"
    turn = 1
    if log_path.exists():
        try:
            turn = sum(1 for _ in log_path.open(encoding="utf-8")) + 1
        except OSError:
            pass
    user_text = history[-1]["content"]
    _append_log(settings, session, {
        "at": time.time(), "turn": turn, "effort": effort, "model": model, "cost_usd": cost, "lang": lang,
        "user": user_text[:2000], "answer": out["answer"][:4000],
        "cited_atoms": out["cited_atoms"], "cited_images": out["cited_images"],
        "dropped_citations": dropped, "proposals_n": len(out["proposals"]),
        "proposals": [{k: p.get(k) for k in ("type", "title", "cites", "atom_a", "atom_b", "target_atom")}
                      for p in out["proposals"]],
        "ingested": ingested,
        "context": {"atoms": sorted(ctx_ids), "molecule": molecule_id, "images": [p.name for p in image_paths],
                    "missing_atoms": missing},
    })
    return {
        "answer": out["answer"],
        "cited_atoms": out["cited_atoms"],
        "cited_images": out["cited_images"],
        "proposals": out["proposals"],
        "ingested": ingested,
        "dropped_citations": dropped,
        "cost_usd": cost,
        "model": model,
        "effort": effort,
        "session": session,
        "context": {"atoms": [a.id for a in atoms], "molecule": molecule_id,
                    "images": [p.name for p in image_paths], "missing_atoms": missing,
                    "molecule_info": ({k: mol_info.get(k) for k in ("id", "name_ko", "name_en", "n", "error")}
                                      if mol_info else None)},
        "lang": lang,
        "cache": llm.cache_stats(getattr(resp, "usage", None)),
        "turn": turn,
    }


# ── CLI (수동 시험) ────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="python -m geoguesshelper.dialogue",
                                 description="원자 대화 — 문맥 원자·분자·이미지를 두고 한 턴 묻는다(결과 JSON 출력)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ask", help="한 턴 질문")
    p.add_argument("question", help="질문(마지막 user 메시지)")
    p.add_argument("--atom", action="append", default=[], help="문맥 원자 id(반복 가능)")
    p.add_argument("--molecule", default=None, help="문맥 분자 id(멤버·주변 원자·문서가 실린다)")
    p.add_argument("--image", action="append", default=[], help="captures/ 안의 파일명(반복 가능, 최대 dialogue_max_images)")
    p.add_argument("--effort", default=None, choices=_EFFORTS, help="기본 settings.dialogue_effort")
    p.add_argument("--lang", default="ko")
    p.add_argument("--session", default=None, help="이어서 말할 세션 id(로그 docs/knowledge/dialogue/<session>.jsonl)")
    p.add_argument("--history", default=None,
                   help="이전 대화 JSON 파일([{role,content},…]) — 마지막 질문은 question 으로 덧붙인다")
    a = ap.parse_args(argv)

    settings = load_settings()
    if a.cmd == "ask":
        history: list[dict] = []
        if a.history:
            history = json.loads(Path(a.history).read_text(encoding="utf-8"))
        history.append({"role": "user", "content": a.question})
        try:
            res = chat(settings, atom_ids=a.atom, molecule_id=a.molecule, images=a.image,
                       messages=history, effort=a.effort, lang=a.lang, session=a.session)
        except llm.LLMUnavailable as exc:
            print(json.dumps({"error": str(exc), "status": "NO_KEY"}, ensure_ascii=False))
            raise SystemExit(2)
        except ValueError as exc:
            print(json.dumps({"error": str(exc), "status": "BAD_REQUEST"}, ensure_ascii=False))
            raise SystemExit(2)
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
