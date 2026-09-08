"""원자 대화 실증 — 닫힌 세계·인용 관문·적재·API 경로를 **실제로 돌려** 확인한다 (impl-spec_260907 §4.3).

왜 이 파일이 저장소에 있는가
    대화 제안은 사람 승인 없이 곧바로 원자가 된다(사용자 결정 260907). 그러니 관문이 기계적으로
    확실히 작동하는지 — 문맥 밖 인용이 걷히고, 인용 없는 제안이 기각되고, 들어온 원자에
    kind=claim·origin=dialogue 표식이 붙는지 — 를 코드가 아니라 결과 파일로 확인해야 한다.

두 부분
    A. 오프라인(기본): 임시 지식 저장소 + 가짜 LLM 응답으로 후처리·적재·로그·API(503/422/200)를 검사한다.
       모델 호출 없음, 비용 0, 실제 docs/knowledge 는 건드리지 않는다(분자 문맥 읽기만).
    B. --live: 실제 키로 같은 엔티티의 원자 2개를 두고 1~2턴 묻는다. 제안이 실제 docs/knowledge/atoms/ 에
       claim 원자로 들어가는지, 기각된 제안이 보고되는지, 비용이 얼마인지 기록한다.

실행
    UV_NATIVE_TLS=1 PYTHONUTF8=1 uv run python tests/dialogue_check.py
    UV_NATIVE_TLS=1 PYTHONUTF8=1 uv run python tests/dialogue_check.py --live [--entity faroe-islands] [--effort medium]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "altaiya" / "altaiya-backend"))

from geoguesshelper import dialogue, knowledge, llm   # noqa: E402
from geoguesshelper.config import Settings, load_settings  # noqa: E402

SPEC_KEYS = {"answer", "cited_atoms", "cited_images", "proposals", "ingested", "dropped_citations",
             "cost_usd", "model", "effort", "session", "context"}


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    return bool(cond)


# ── 가짜 LLM ──────────────────────────────────────────────────────────────────
class _Usage:
    input_tokens = 1200
    output_tokens = 300
    cache_creation_input_tokens = 900
    cache_read_input_tokens = 0
    server_tool_use = None


class _Block:
    def __init__(self, data):
        self.type = "tool_use"
        self.name = "dialogue_turn"
        self.input = data


class _Resp:
    def __init__(self, data, model="claude-fable-5"):
        self.content = [_Block(data)] if data is not None else []
        self.usage = _Usage()
        self.model = model


class FakeLLM:
    """llm.call 대역 — 준비한 도구 출력을 차례로 돌려주고, 받은 kwargs 를 기록한다."""

    def __init__(self, outputs: list):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def __call__(self, settings, **kw):
        self.calls.append(kw)
        data = self.outputs.pop(0) if self.outputs else None
        return _Resp(data)


def sandbox() -> tuple[Settings, Path, knowledge.Atom, knowledge.Atom]:
    """임시 지식 저장소 — 같은 엔티티(testland)를 가진 원자 2개 + 캡처 1장."""
    tmp = Path(tempfile.mkdtemp(prefix="ggh_dlg_"))
    s = dataclasses.replace(load_settings(), knowledge_dir=tmp / "knowledge", captures_dir=tmp / "captures",
                            anthropic_api_key="x")
    s.captures_dir.mkdir(parents=True)
    (s.captures_dir / "cap_a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    st = knowledge.store_for(s)
    a1 = knowledge.Atom(id="atm_aaaaaaaaaaa1", layer="architecture", scope="country", category="roof-facade",
                        title="Testland steep dark roofs", body="Testland houses carry steep dark roofs.",
                        tags=["roof-facade", "test-tag-a"], entities=["testland"], lat=60.0, lng=10.0,
                        cell=knowledge.geohash(60.0, 10.0, 7))
    a2 = knowledge.Atom(id="atm_aaaaaaaaaaa2", layer="language", scope="country", category="signage-language",
                        title="Testland signage uses ø", body="Testland road signs use the letter ø.",
                        tags=["signage-language", "test-tag-b"], entities=["testland"])
    st.save(a1)
    st.save(a2)
    return s, tmp, a1, a2


def part_a() -> bool:
    ok = True
    print("① normalize_messages — 교대·마지막 user")
    h = dialogue.normalize_messages([{"role": "assistant", "content": "x"}, {"role": "user", "content": "a"},
                                     {"role": "user", "content": "b"}, {"role": "assistant", "content": "r"},
                                     {"role": "user", "content": [{"type": "text", "text": "c"}]}])
    ok &= check("앞선 assistant 제거·연속 user 병합·블록 배열 텍스트화",
                [m["role"] for m in h] == ["user", "assistant", "user"] and h[0]["content"] == "a\n\nb"
                and h[-1]["content"] == "c", str(h))
    for bad in ([], [{"role": "assistant", "content": "x"}], [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]):
        try:
            dialogue.normalize_messages(bad)
            ok &= check(f"ValueError: {bad!s:.40}", False, "예외가 안 났다")
        except ValueError:
            ok &= check(f"ValueError: {bad!s:.40}", True)

    print("\n② molecule_context — 실제 분자 index/문서 읽기")
    real = load_settings()
    idx_p = real.knowledge_dir / "molecule" / "index.json"
    if idx_p.exists():
        mols = json.loads(idx_p.read_text(encoding="utf-8")).get("molecules") or {}
        named = next((k for k, v in mols.items() if v.get("named") and v.get("doc")), None)
        if named:
            ids, info = dialogue.molecule_context(real, named)
            ok &= check(f"{named}: 멤버 {len(info['members'])} + 주변 {len(info['periphery'])} = {len(ids)}",
                        ids and ids[:len(info["members"])] == info["members"] and info.get("doc"))
            ok &= check("문서 본문에 프론트매터·멤버 목록 절 없음",
                        not info["doc"].startswith("---") and "## 멤버 원자" not in info["doc"])
            atoms, mi, missing = dialogue.load_context_atoms(real, [], named)
            ok &= check(f"load_context_atoms(molecule) → {len(atoms)}개 (상한 {real.dialogue_max_context_atoms})",
                        0 < len(atoms) <= real.dialogue_max_context_atoms)
        ids, info = dialogue.molecule_context(real, "mol_000000000000")
        ok &= check("모르는 분자 → ([], error)", ids == [] and info and info.get("error"))
    else:
        print("  SKIP  분자 index 없음")

    print("\n③ resolve_images — 존재하는 것만, 폴더 탈출 금지, 상한")
    s, tmp, a1, a2 = sandbox()
    try:
        imgs = dialogue.resolve_images(s, ["cap_a.jpg", "nope.jpg", "../knowledge/index.json", "cap_a.jpg"])
        ok &= check("cap_a 만 1개", [p.name for p in imgs] == ["cap_a.jpg"], str(imgs))
        s_small = dataclasses.replace(s, dialogue_max_images=0)
        ok &= check("상한 0 → 빈 목록", dialogue.resolve_images(s_small, ["cap_a.jpg"]) == [])

        print("\n④ chat() — 가짜 LLM 으로 후처리·인용 관문·적재·로그")
        outside = "atm_0000000fffff"
        turn1 = {
            "answer": f"둘은 다른 얘기다 [[{a1.id}]] [[{a2.id}]]. 지붕은 (image 0) 에 보인다. 밖 인용 [[{outside}]] (image 5).",
            "cited_atoms": [a1.id, outside, a1.id],
            "cited_images": [0, 5],
            "proposals": [
                {"type": "atom", "title": "Testland vs Otherland housing typology", "layer": "architecture",
                 "category": "housing-typology", "scope": "country", "tags": ["housing-typology", "discriminator"],
                 "entities": ["testland", "otherland"], "cites": [a1.id, "image:0"],
                 "body": "Testland houses are detached with steep dark roofs; Otherland would show flat-roofed terraces instead."},
                {"type": "atom", "title": "Uncited claim", "layer": "culture", "category": "other", "scope": "region",
                 "tags": ["x"], "entities": ["testland"], "cites": [], "body": "No citation at all."},
                {"type": "atom", "title": "Outside-cited claim", "layer": "culture", "category": "other", "scope": "region",
                 "tags": ["x"], "entities": ["testland"], "cites": [outside, "image:9"], "body": "Cites things not in context."},
                {"type": "atom", "title": "Near duplicate of a1", "layer": "architecture", "category": "roof-facade",
                 "scope": "country", "tags": ["roof-facade", "test-tag-a"], "entities": ["testland"], "cites": [a1.id],
                 "body": "Steep dark roofs are typical of Testland houses (restated)."},
                {"type": "link", "atom_a": a1.id, "atom_b": a2.id, "cites": [a1.id, a2.id], "reason": "same country"},
                {"type": "link", "atom_a": a1.id, "atom_b": outside, "cites": [a1.id], "reason": "outside"},
                {"type": "dispute", "target_atom": a2.id, "cites": [a2.id], "reason": "ø is not exclusive"},
                "not-a-dict",
            ],
        }
        fake = FakeLLM([None, turn1])          # 첫 응답은 도구 입력 없음 → 재시도 1회
        real_call = llm.call
        llm.call = fake
        try:
            r1 = dialogue.chat(s, atom_ids=[a1.id, a2.id, "atm_missing00000"], images=["cap_a.jpg"],
                               messages=[{"role": "user", "content": "이 둘은 같은 얘긴가?"}], effort="low",
                               lang="ko", session="dlg_test_a")
        finally:
            llm.call = real_call
        ok &= check("도구 입력이 비면 1회 재시도(호출 2회)", len(fake.calls) == 2, str(len(fake.calls)))
        kw = fake.calls[-1]
        ok &= check("이미지가 있으면 role=vision · 강제 tool_choice · 고정 system",
                    kw.get("role") == "vision" and kw.get("tool_choice") == {"type": "tool", "name": "dialogue_turn"}
                    and kw.get("system") == dialogue._SYSTEM and kw.get("effort") == "low")
        first = kw["messages"][0]["content"]
        ok &= check("첫 user 메시지 = [이미지(cache_control)] + 문맥 블록(cache_control) + 질문(+언어 지시)",
                    first[0]["type"] == "image" and first[0].get("cache_control")
                    and first[1]["type"] == "text" and first[1].get("cache_control")
                    and first[1]["text"].startswith("CONTEXT ATOMS (2)")
                    and "OUTPUT LANGUAGE: write `answer` in Korean" in first[-1]["text"])
        ok &= check("반환 키 ⊇ 명세", SPEC_KEYS <= set(r1), str(sorted(SPEC_KEYS - set(r1))))
        ok &= check("cited_atoms ⊂ 문맥 · 순서 유지 · 중복 제거", r1["cited_atoms"] == [a1.id, a2.id], str(r1["cited_atoms"]))
        ok &= check("cited_images 범위 안만", r1["cited_images"] == [0], str(r1["cited_images"]))
        ok &= check("dropped_citations = 문맥 밖 원자 + 범위 밖 이미지",
                    r1["dropped_citations"] == [outside, "image:5"], str(r1["dropped_citations"]))
        ing = r1["ingested"]
        ok &= check("created 1 (인용된 새 주장)", ing["created"] == 1, str(ing))
        ok &= check("merged 1 (a1 의 근중복은 새 원자를 만들지 않는다)", ing["merged"] == 1, str(ing))
        ok &= check("links 1 · disputes 1", ing["links"] == 1 and ing["disputes"] == 1, str(ing))
        whys = sorted(x["why"] for x in ing["rejected"])
        ok &= check("rejected 3 = uncited ×2 + link atom not in context",
                    whys == ["atom not in context", "uncited", "uncited"], str(whys))
        ok &= check("context.missing_atoms 에 없는 원자 보고", r1["context"]["missing_atoms"] == ["atm_missing00000"])
        st = knowledge.store_for(s)
        new_ids = [i for i in ing["atoms"] if i not in (a1.id, a2.id)]
        ok &= check("새 원자 1개가 atoms/ 에 있다", len(new_ids) == 1 and st.path_of(new_ids[0]).is_file(), str(new_ids))
        if new_ids:
            c = st.load(new_ids[0])
            ok &= check("kind=claim · origin=dialogue · status=active · 태그 claim · refs=[a1] · 좌표는 a1 에서 상속",
                        c.kind == "claim" and c.origin == "dialogue" and c.status == "active" and "claim" in c.tags
                        and c.refs == [a1.id] and c.lat == 60.0 and c.category == "housing-typology",
                        json.dumps(c.meta(), ensure_ascii=False)[:300])
            ok &= check("index.json 에 실렸다", new_ids[0] in st.index()["atoms"])
        a1n, a2n = st.load(a1.id), st.load(a2.id)
        ok &= check("link → 양쪽 refs", a2.id in a1n.refs and a1.id in a2n.refs)
        ok &= check("dispute → a2.misses == 1 (아직 active)", a2n.misses == 1 and a2n.status == "active")
        ok &= check("merged → a1.uses 2", a1n.uses == 2, str(a1n.uses))
        log = s.knowledge_dir / "dialogue" / "dlg_test_a.jsonl"
        ok &= check("로그 1줄", log.is_file() and len(log.read_text(encoding="utf-8").splitlines()) == 1)
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        ok &= check("로그 키 ⊇ {at, effort, model, cost_usd, user, answer, cited_atoms, proposals_n, ingested}",
                    {"at", "effort", "model", "cost_usd", "user", "answer", "cited_atoms", "proposals_n", "ingested"} <= set(row))
        ok &= check("로그 ingested == 반환 ingested · proposals_n 7", row["ingested"] == ing and row["proposals_n"] == 7)
        ok &= check("evidence_log 에 dispute 기록", (s.knowledge_dir / "evidence_log.jsonl").is_file())
        ok &= check("cost_usd 계산됨(가짜 usage)", r1["cost_usd"] > 0 and r1["model"] == "claude-fable-5")

        print("\n⑤ 2턴 — 이력 전달·turn 번호·이미지 없으면 role=reason")
        fake2 = FakeLLM([{"answer": "두 번째", "cited_atoms": [], "cited_images": [], "proposals": []}])
        llm.call = fake2
        try:
            r2 = dialogue.chat(s, atom_ids=[a1.id], messages=[
                {"role": "user", "content": "q1"}, {"role": "assistant", "content": r1["answer"]},
                {"role": "user", "content": "q2"}], session="dlg_test_a")
        finally:
            llm.call = real_call
        kw2 = fake2.calls[-1]
        ok &= check("메시지 3개 · 마지막 user 에 언어 지시 · role=reason · effort 기본값",
                    len(kw2["messages"]) == 3 and "OUTPUT LANGUAGE" in kw2["messages"][-1]["content"]
                    and kw2["role"] == "reason" and kw2["effort"] == s.dialogue_effort)
        ok &= check("turn 2 · 로그 2줄", r2["turn"] == 2 and len(log.read_text(encoding="utf-8").splitlines()) == 2)
        ok &= check("빈 제안 → ingested 0/0/0/0 rejected []",
                    r2["ingested"] == {"atoms": [], "created": 0, "merged": 0, "links": 0, "disputes": 0, "rejected": []})
        try:
            dialogue.chat(s, atom_ids=["atm_missing00000"], messages=[{"role": "user", "content": "q"}])
            ok &= check("문맥 없음 → ValueError", False, "예외가 안 났다")
        except ValueError as exc:
            ok &= check("문맥 없음 → ValueError", "atm_missing00000" in str(exc))

        print("\n⑥ dialogue_api — TestClient: health · 503 · 422 · 200")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import dialogue_api
        app = FastAPI()
        app.include_router(dialogue_api.router)
        cl = TestClient(app)
        body = {"atoms": [a1.id, a2.id], "molecule": None, "images": [], "effort": "low", "lang": "ko",
                "session": "dlg_test_api", "messages": [{"role": "user", "content": "같은 얘긴가?"}]}
        dialogue_api.SETTINGS_OVERRIDE = dataclasses.replace(s, anthropic_api_key="")
        h = cl.get("/api/dialogue/health").json()
        ok &= check("health(키 없음) → available False + reason", h.get("available") is False and h.get("reason"), str(h))
        r = cl.post("/api/dialogue", json=body)
        ok &= check("POST(키 없음) → 503 {error}", r.status_code == 503 and "error" in r.json(), f"{r.status_code} {r.text[:120]}")
        dialogue_api.SETTINGS_OVERRIDE = s
        r = cl.post("/api/dialogue", json={"atoms": "notalist", "messages": "nope"})
        ok &= check("깨진 본문 → 422", r.status_code == 422, str(r.status_code))
        r = cl.post("/api/dialogue", json={**body, "messages": []})
        ok &= check("messages 비어 있음 → 422", r.status_code == 422, str(r.status_code))
        r = cl.post("/api/dialogue", json={**body, "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]})
        ok &= check("마지막이 assistant → 422", r.status_code == 422 and "error" in r.json(), f"{r.status_code} {r.text[:120]}")
        r = cl.post("/api/dialogue", json={**body, "effort": "ultra"})
        ok &= check("모르는 effort → 422", r.status_code == 422, str(r.status_code))
        r = cl.post("/api/dialogue", json={**body, "atoms": ["atm_missing00000"]})
        ok &= check("문맥 없음(없는 원자만) → 422 {error}", r.status_code == 422 and "error" in r.json(), f"{r.status_code} {r.text[:120]}")
        h = cl.get("/api/dialogue/health").json()
        ok &= check("health(키 있음) → available True · knowledge_dir 는 주입한 경로",
                    h.get("available") is True and h.get("knowledge_dir") == str(s.knowledge_dir), str(h))
        fake3 = FakeLLM([{"answer": f"API 경로 [[{a1.id}]]", "cited_atoms": [a1.id], "cited_images": [], "proposals": []}])
        llm.call = fake3
        try:
            r = cl.post("/api/dialogue", json=body)
        finally:
            llm.call = real_call
        ok &= check("정상 경로 → 200 + 명세 키", r.status_code == 200 and SPEC_KEYS <= set(r.json()), f"{r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            j = r.json()
            ok &= check("context.atoms == 요청 원자 · session 유지", j["context"]["atoms"] == [a1.id, a2.id] and j["session"] == "dlg_test_api")
        dialogue_api.SETTINGS_OVERRIDE = None
        h = cl.get("/api/dialogue/health").json()
        ok &= check("health(실제 settings) 응답 형식", "available" in h and "reason" in h, str(h))
        print(f"       실제 환경 health: {json.dumps(h, ensure_ascii=False)[:200]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


# ── B. 실제 호출 ────────────────────────────────────────────────────────────────
def pick_pair(settings: Settings, entity: str) -> list[knowledge.Atom]:
    st = knowledge.store_for(settings)
    idx = st.index()
    ids = (idx.get("entities") or {}).get(entity) or []
    out: list[knowledge.Atom] = []
    for aid in ids:
        a = st.load(aid)
        if a and (a.status or "active") == "active" and (a.kind or "fact") == "fact":
            out.append(a)
        if len(out) == 2:
            break
    return out


def part_b(entity: str, effort: str, session: str | None) -> bool:
    ok = True
    s = load_settings()
    if not s.has_anthropic:
        print("  SKIP  ANTHROPIC_API_KEY 없음 — --live 는 키가 있어야 한다")
        return True
    pair = pick_pair(s, entity)
    if len(pair) < 2:
        print(f"  FAIL  엔티티 {entity} 에 active fact 원자가 2개 없다")
        return False
    ids = [a.id for a in pair]
    print(f"  문맥 원자({entity}):")
    for a in pair:
        print(f"    [[{a.id}]] ({a.layer}/{a.scope}) {a.title}")
    session = session or dialogue.new_session_id()
    total = 0.0
    n_before = len(list((s.knowledge_dir / "atoms").glob("atm_*.md")))

    q1 = "이 두 원자는 같은 얘긴가? 다르다면 무엇이 둘을 가르는 판별자인가 — 다른 후보였다면 대신 무엇이 보였을지로 말해 달라."
    print(f"\n  턴 1 (effort={effort}): {q1}")
    t0 = time.time()
    r1 = dialogue.chat(s, atom_ids=ids, messages=[{"role": "user", "content": q1}], effort=effort, lang="ko", session=session)
    total += r1["cost_usd"]
    print(f"    {time.time() - t0:.0f}s · ${r1['cost_usd']:.4f} · {r1['model']} · cache {r1['cache']}")
    print("    답변: " + r1["answer"][:600].replace("\n", " "))
    print(f"    인용: {r1['cited_atoms']} · 걷어낸 인용: {r1['dropped_citations']}")
    print(f"    제안 {len(r1['proposals'])}건 → ingested {json.dumps(r1['ingested'], ensure_ascii=False)}")
    ok &= check("answer 비어 있지 않음", bool(r1["answer"].strip()))
    ok &= check("cited_atoms ⊂ 문맥", set(r1["cited_atoms"]) <= set(ids), str(r1["cited_atoms"]))
    ok &= check("session 유지", r1["session"] == session)
    results = [r1]

    ing = r1["ingested"]
    if not r1["proposals"] or (ing["created"] + ing["merged"] + ing["links"] + ing["disputes"]) == 0:
        eff2 = "low" if r1["cost_usd"] > 0.25 else effort
        q2 = ("문맥 원자 두 개가 뒷받침하는, 다른 장소의 보고서가 재사용할 수 있는 원자를 하나 제안해 달라. "
              "cites 에 그 원자 id 를 반드시 넣어라. 새로 지어내지 말고 두 원자를 함께 읽을 때 성립하는 것만.")
        print(f"\n  턴 2 (effort={eff2}): {q2}")
        t0 = time.time()
        r2 = dialogue.chat(s, atom_ids=ids, messages=[
            {"role": "user", "content": q1}, {"role": "assistant", "content": r1["answer"]},
            {"role": "user", "content": q2}], effort=eff2, lang="ko", session=session)
        total += r2["cost_usd"]
        print(f"    {time.time() - t0:.0f}s · ${r2['cost_usd']:.4f} · {r2['model']} · cache {r2['cache']}")
        print("    답변: " + r2["answer"][:600].replace("\n", " "))
        print(f"    인용: {r2['cited_atoms']} · 걷어낸 인용: {r2['dropped_citations']}")
        print(f"    제안 {len(r2['proposals'])}건 → ingested {json.dumps(r2['ingested'], ensure_ascii=False)}")
        ok &= check("2턴 answer 비어 있지 않음 · cited ⊂ 문맥", bool(r2["answer"].strip()) and set(r2["cited_atoms"]) <= set(ids))
        ok &= check("turn == 2", r2["turn"] == 2, str(r2["turn"]))
        results.append(r2)

    print("\n  적재 검증")
    st = knowledge.store_for(s)
    created_ids: list[str] = []
    for r in results:
        for p in r["proposals"]:
            print(f"    제안[{p['type']}] {str(p.get('title') or p.get('reason') or '')[:80]} · cites={p['cites']}")
        for why in r["ingested"]["rejected"]:
            print(f"    기각: {json.dumps(why, ensure_ascii=False)[:160]}")
        for aid in r["ingested"]["atoms"]:
            a = st.load(aid)
            if a is None:
                ok &= check(f"{aid} 파일 존재", False)
                continue
            if aid in ids:
                print(f"    병합: [[{aid}]] uses={a.uses} refs={a.refs[:4]}")
                continue
            created_ids.append(aid)
            print(f"    원자: [[{aid}]] kind={a.kind} origin={a.origin} status={a.status} layer={a.layer}/{a.scope}/{a.category} "
                  f"refs={a.refs} · {a.title}")
            ok &= check(f"{aid} kind=claim · origin=dialogue · refs ⊂ 문맥 · 태그 claim",
                        a.kind == "claim" and a.origin == "dialogue" and set(a.refs) <= set(ids) and "claim" in a.tags)
            ok &= check(f"{aid} index.json 에 실림", aid in st.index()["atoms"])
    n_after = len(list((s.knowledge_dir / "atoms").glob("atm_*.md")))
    n_created = sum(r["ingested"]["created"] for r in results)
    ok &= check(f"atoms/ 파일 수 증가 == created 합({n_created})", n_after - n_before == n_created, f"{n_before} → {n_after}")
    log = s.knowledge_dir / "dialogue" / f"{session}.jsonl"
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []
    ok &= check(f"로그 {log.name} 줄 수 == 턴 수({len(results)})", len(rows) == len(results), str(len(rows)))
    ok &= check("로그 ingested == 반환 ingested (턴별)",
                all(row["ingested"] == r["ingested"] for row, r in zip(rows, results)))
    print(f"\n  총비용 ${total:.4f} · 세션 {session} · 새 claim 원자 {created_ids}")
    ok &= check("총비용 < $0.5", total < 0.5, f"${total:.4f}")
    return ok


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="실제 모델 호출(1~2턴, 실제 docs/knowledge 에 claim 원자가 생긴다)")
    ap.add_argument("--entity", default="faroe-islands", help="문맥 원자 2개를 고를 엔티티(index.json entities 키)")
    ap.add_argument("--effort", default="medium", choices=("low", "medium", "high"))
    ap.add_argument("--session", default=None)
    ap.add_argument("--skip-offline", action="store_true")
    a = ap.parse_args()
    ok = True
    if not a.skip_offline:
        print("A. 오프라인 — 임시 저장소 + 가짜 LLM")
        ok &= part_a()
    if a.live:
        print("\nB. 실제 호출")
        ok &= part_b(a.entity, a.effort, a.session)
    print("\n" + ("전체 통과" if ok else "실패 있음"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
