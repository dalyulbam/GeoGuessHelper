"""자동 정정 루프 검증 — docs/plan/impl-spec_260907.md §3.6.

    uv run python tests/correction_check.py            # LLM 호출 없음: 순수 함수 · 기존 원장 스키마 · 서버 import
    uv run python tests/correction_check.py --live     # + 잡 1건 실행 → 같은 잡 --redo → 유도 확인 (비용 발생)

왜 이 파일이 저장소에 있는가
    정정 루프는 사람 승인 없이 원자를 쓴다. 그래서 (1) 적재된 원자가 정확히 어떤 표식(kind=discriminator ·
    origin=correction)을 달고 index 에 실렸는지, (2) 다음 blind 판단이 그 원자를 **실제로 받는지**(유도)를
    코드가 아니라 산출물로 확인해야 한다. 산출물이 없으면 해당 항목은 SKIP 으로 표시하고 PASS 로 속이지 않는다.

검증 항목
    ① --dry-run 이 image_panos 있는 잡 14건을 나열한다
    ② 판정(compare)·깨진 도구 입력 감지(is_corrupt/_rescue)·정리(_sanitize)·서버 재료 변환(job_from_result)
    ③ corr_*.json 스키마 키 전부 · corrections.jsonl ≥ 기록 수 · README 존재 · README "실행 N회" == jsonl 줄 수
    ④ 판별자 원자: atoms/ 에 kind=discriminator · origin=correction · index.json 에 실림
    ⑤ recall_log.jsonl 에 mode=blind-p2 줄(2패스가 돈 기록이 있으면)
    ⑥ 유도: redo 기록(induction)의 offered 가 비지 않는다
    ⑦ 서버 import 스모크 + "correct" 핸들러 등록 + _build_reports_sync 가 known=pre_known 을 넘긴다
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from geoguesshelper import correction                 # noqa: E402
from geoguesshelper.config import load_settings       # noqa: E402

_KEYS = ("job_id", "report_file", "label", "created", "lang", "images", "image_panos", "truth", "blind",
         "verdict", "correction", "atoms", "evidence", "cost_usd", "status")
_LEDGER_KEYS = ("at", "job_id", "report_file", "truth_iso", "blind_iso", "blind_country", "truth_country",
                "country_hit", "region_hit", "city_hit", "error_km", "bucket", "phase2_used", "n_offered", "n_relied",
                "revised", "n_discriminators", "atoms_created", "atoms_merged", "hits", "misses", "retracted",
                "cost_usd", "status")


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    return bool(cond)


def skip(name: str, why: str) -> None:
    print(f"  SKIP  {name}  — {why}")


def quiet(*_a, **_k) -> None:
    pass


def main(argv: list[str]) -> int:
    live = "--live" in argv
    settings = load_settings()
    ok = True

    print("① dry-run 대상 나열")
    dry = correction.run(settings, dry_run=True, redo=True, log=quiet)
    # 잡은 계속 늘어난다(E2E·실사용) — 기준 시점(260907) 14건 이상이면 통과, 정확한 수는 detail 로만(observe_check 의 STALE 관례)
    ok &= check("image_panos 있는 잡 ≥ 14건(260907 기준 14)", len(dry["targets"]) >= 14, f"실제 {len(dry['targets'])}")

    print("\n② 순수 함수")
    truth = {"lat": 65.398, "lng": -20.944, "iso": "IS", "country": "Iceland",
             "region": "Northwestern Region (Norðurland vestra), Húnaþing vestra municipality", "city": "Hvammstangi"}
    hit = {"best_guess": {"country": "Iceland", "country_iso": "IS", "region_or_state": "Norðurland vestra",
                          "city": "Hvammstangi", "coordinate_estimate": {"lat": 65.40, "lng": -20.95}}}
    v = correction.compare(hit, truth)
    ok &= check("국가 hit · 지역 hit(부분 문자열) · 도시 hit · 오차 <1 km",
                (v["country"], v["region"], v["city"], v["bucket"]) == ("hit", "hit", "hit", "<1"), str(v))
    miss = {"best_guess": {"country": "Norway", "country_iso": "NO", "region_or_state": "Troms", "city": None,
                           "coordinate_estimate": {"lat": 69.6, "lng": 18.9}}}
    v = correction.compare(miss, truth)
    ok &= check("국가 miss · 지역 miss · 도시 miss(추측 없음) · 오차 >=100",
                (v["country"], v["region"], v["city"], v["bucket"]) == ("miss", "miss", "miss", ">=100"), str(v))
    v = correction.compare(hit, {**truth, "region": None, "city": None})
    ok &= check("정답 쪽이 비면 unknown", (v["region"], v["city"]) == ("unknown", "unknown"), str(v))
    v = correction.compare({"best_guess": {"country": "Iceland", "country_iso": "IS"}}, truth)
    ok &= check("좌표 없음 → error_km None · bucket na", v["error_km"] is None and v["bucket"] == "na", str(v))

    good = {"summary": "x", "misleading_cues": [], "decisive_cues": [], "discriminators": [{"title": "t"}],
            "misled_atoms": [], "confirming_atoms": ["atm_a"], "retract_candidates": []}
    ok &= check("정상 도구 입력은 corrupt 아님", not correction.is_corrupt(good))
    bad1 = dict(good, discriminators='[{"title":"t"}]')
    ok &= check("배열 필드가 문자열이면 corrupt", correction.is_corrupt(bad1))
    bad2 = dict(good, summary='X was actually Y</summary><parameter name="misleading_cues">[]')
    ok &= check("<parameter 누출이면 corrupt", correction.is_corrupt(bad2))
    bad3 = dict(good, discriminators=[{"title": "t", "body": "<value>a</value>"}])
    ok &= check("중첩 문자열의 <value> 마커도 corrupt", correction.is_corrupt(bad3))
    ok &= check("None/빈 입력은 corrupt", correction.is_corrupt(None) and correction.is_corrupt({}))
    r = correction._rescue(dict(bad1, **{"summary": bad2["summary"]}))
    ok &= check("_rescue: 배열 문자열 → JSON 복구 · summary 꼬리 절단 · parse_rescued",
                r["discriminators"] == [{"title": "t"}] and r["summary"] == "X was actually Y"
                and r.get("parse_rescued") is True, json.dumps(r, ensure_ascii=False)[:200])
    r2 = correction._rescue(dict(good, misled_atoms="not json"))
    ok &= check("_rescue: 복구 불가 배열 문자열 → []", r2["misled_atoms"] == [])

    s = correction._sanitize(
        {"summary": " s ", "misled_atoms": ["atm_a", "atm_zzz", "atm_a"], "confirming_atoms": ["atm_b", 5],
         "retract_candidates": [{"atom_id": "atm_a", "reason": "r"}, {"atom_id": "atm_x", "reason": "r"}, "junk"],
         "discriminators": [{"title": str(i)} for i in range(12)] + ["junk"], "misleading_cues": ["junk"],
         "decisive_cues": None},
        {"atm_a", "atm_b"}, 8)
    ok &= check("_sanitize: offered 밖 id 제거 · 중복 제거 · dict 만 · 판별자 상한 8",
                s["misled_atoms"] == ["atm_a"] and s["confirming_atoms"] == ["atm_b"]
                and [x["atom_id"] for x in s["retract_candidates"]] == ["atm_a"]
                and len(s["discriminators"]) == 8 and s["misleading_cues"] == [] and s["decisive_cues"] == []
                and s["summary"] == "s", json.dumps(s, ensure_ascii=False)[:200])

    fake_result = {"reports": [{"file": "report_no_x_60.1_5.2_260907_000000_ko.html"}],
                   "primaryAnalysis": {"images": ["capture_a.jpg", "capture_b.jpg"], "lang": "ko",
                                       "image_panos": {"capture_a.jpg": {"lat": 60.1, "lng": 5.2, "heading": 10}},
                                       "analysis": {"best_guess": {"country": "Norway", "country_iso": "NO"}}}}
    j = correction.job_from_result("job_test", fake_result, "라벨")
    ok &= check("job_from_result: 잡 dict 조립(load_jobs 와 같은 키)",
                j is not None and j["job_id"] == "job_test" and j["report_file"].startswith("report_no_")
                and j["images"] == ["capture_a.jpg", "capture_b.jpg"] and "capture_a.jpg" in j["image_panos"]
                and j["aided_prior"]["best_guess"]["country_iso"] == "NO" and j["prior_lang"] == "ko", str(j))
    t = correction._truth_of(j)
    ok &= check("_truth_of: pano 평균 좌표 + 파일명 ISO + aided 국가",
                t["iso"] == "NO" and abs(t["lat"] - 60.1) < 1e-9 and t["country"] == "Norway" and t["source"] == "aided_prior", str(t))
    ok &= check("job_from_result: 재료 없으면 None", correction.job_from_result("j", {"primaryAnalysis": {}}) is None)
    r = correction.run_for_result(settings, source_job_id="job_test", result={}, label="", log=quiet)
    ok &= check("run_for_result: 재료 없으면 NO_IMAGES (LLM 호출·파일 쓰기 없음)",
                r["status"] == "NO_IMAGES" and not (correction._dir(settings) / "corr_job_test.json").exists(), str(r))

    if live:
        print("\n(live) 잡 1건 실행 → 같은 잡 --redo")
        todo = [t for t in dry["targets"]
                if not ((correction._corr_path(settings, t)).exists()
                        and json.loads(correction._corr_path(settings, t).read_text(encoding="utf-8")).get("status") == "OK")]
        target = (todo or dry["targets"])[0]
        r1 = correction.run(settings, only=[target], redo=True, max_job_usd=1.5)
        ok &= check("(live) 1차 실행 OK", r1["done"] and r1["done"][0]["status"] == "OK", str(r1))
        r2 = correction.run(settings, only=[target], redo=True, max_job_usd=1.5)
        ok &= check("(live) redo 실행 OK", r2["done"] and r2["done"][0]["status"] == "OK", str(r2))

    print("\n③ 원장 스키마")
    d = correction._dir(settings)
    recs = correction._load_records(settings)
    ledger = correction._load_ledger(settings)
    if not recs:
        skip("corr_*.json 스키마", "기록 없음 — `correct run --limit 1` 뒤 다시")
    else:
        missing = {r["job_id"]: [k for k in _KEYS if k not in r] for r in recs}
        missing = {k: v for k, v in missing.items() if v}
        ok &= check(f"corr_*.json {len(recs)}건 — 최상위 키 전부", not missing, str(missing))
        sub_bad = []
        for r in recs:
            if r.get("status") != "OK":
                continue
            b = r.get("blind") or {}
            if not (isinstance(b.get("phase1"), dict) and isinstance(b.get("final"), dict)):
                sub_bad.append((r["job_id"], "blind.phase1/final"))
            if b.get("phase2") is not None and not {"known_offered", "relied_on", "revised_from"} <= set(b["phase2"]):
                sub_bad.append((r["job_id"], "blind.phase2 keys"))
            if not {"country", "region", "city", "error_km", "bucket"} <= set(r.get("verdict") or {}):
                sub_bad.append((r["job_id"], "verdict keys"))
            c = r.get("correction") or {}
            if not {"summary", "discriminators", "misled_atoms", "confirming_atoms", "retract_candidates", "cost_usd", "model", "retries"} <= set(c):
                sub_bad.append((r["job_id"], "correction keys"))
            if not {"created", "merged"} <= set(r.get("atoms") or {}):
                sub_bad.append((r["job_id"], "atoms keys"))
            if not {"hits", "misses", "retracted", "restored"} <= set(r.get("evidence") or {}):
                sub_bad.append((r["job_id"], "evidence keys"))
        ok &= check("OK 기록의 하위 키(blind/verdict/correction/atoms/evidence)", not sub_bad, str(sub_bad))
        ok &= check(f"corrections.jsonl {len(ledger)}줄 ≥ 기록 {len(recs)}건", len(ledger) >= len(recs))
        lk_bad = [i for i, row in enumerate(ledger) if [k for k in _LEDGER_KEYS if k not in row]]
        ok &= check("corrections.jsonl 줄 스키마", not lk_bad, f"줄 {lk_bad[:5]}")
        readme = d / "README.md"
        ok &= check("README.md 존재", readme.exists())
        if readme.exists():
            m = re.search(r"실행 (\d+)회", readme.read_text(encoding="utf-8"))
            ok &= check("README '실행 N회' == jsonl 줄 수", bool(m) and int(m.group(1)) == len(ledger),
                        f"README {m.group(1) if m else None} vs jsonl {len(ledger)}")
            m2 = re.search(r"잡 (\d+)건", readme.read_text(encoding="utf-8"))
            ok &= check("README '잡 N건' == corr 파일 수", bool(m2) and int(m2.group(1)) == len(recs),
                        f"README {m2.group(1) if m2 else None} vs 파일 {len(recs)}")

    print("\n④ 판별자 원자 표식")
    from geoguesshelper import knowledge
    st = knowledge.store_for(settings)
    idx = st.index()
    created = [i for r in recs for i in ((r.get("atoms") or {}).get("created") or [])]
    merged = [i for r in recs for i in ((r.get("atoms") or {}).get("merged") or [])]
    if not created and not merged:
        skip("판별자 원자", "이번 기록에 적재된 원자가 없음")
    else:
        # 신규 원자는 정정 루프가 낳은 것 — 표식 전부. 병합된 원자는 **기존** 원자(보고서·redo 유래)라 origin 은
        # 원래 값을 유지하고, kind 는 혼동 태그(X>X2)가 있을 때만 discriminator 로 승격된다(knowledge.ingest_corrections).
        bad = []
        for aid in dict.fromkeys(created):
            a = st.load(aid)
            meta = (idx.get("atoms") or {}).get(aid)
            if a is None:
                bad.append((aid, "파일 없음"))
            elif a.kind != "discriminator" or a.origin != "correction":
                bad.append((aid, f"kind={a.kind} origin={a.origin}"))
            elif not meta or meta.get("kind") != "discriminator" or meta.get("origin") != "correction":
                bad.append((aid, "index.json 미반영"))
            elif "discriminator" not in a.tags or a.tier != "unaided":
                bad.append((aid, f"tags={a.tags} tier={a.tier}"))
        ok &= check(f"신규 원자 {len(set(created))}개 — kind=discriminator · origin=correction · tier=unaided · index 반영 · 태그",
                    not bad, str(bad[:5]))
        bad_m = []
        for aid in dict.fromkeys(merged):
            a = st.load(aid)
            meta = (idx.get("atoms") or {}).get(aid)
            if a is None or not meta:
                bad_m.append((aid, "파일/index 없음"))
            elif "discriminator" not in a.tags:
                bad_m.append((aid, "병합 태그(discriminator) 없음"))
            elif a.hits < 1:
                bad_m.append((aid, f"hits={a.hits} (병합은 hits+1)"))
        ok &= check(f"병합 원자 {len(set(merged))}개 — 존재 · discriminator 태그 병합 · hits ≥ 1", not bad_m, str(bad_m[:5]))
        allm = set(created) | set(merged)
        n_conf = sum(1 for aid in allm if (st.load(aid) or knowledge.Atom("x", "geography", "country", "", "")).confusions)
        n_kind = sum(1 for aid in allm if (st.load(aid) or knowledge.Atom("x", "geography", "country", "", "")).kind == "discriminator")
        print(f"        (kind=discriminator {n_kind}/{len(allm)} · confusions 있는 원자 {n_conf}/{len(allm)})")

    print("\n⑤ 회상 로그")
    rl = settings.knowledge_dir / "recall_log.jsonl"
    p2_recs = [r for r in recs if (r.get("blind") or {}).get("phase2")]
    if not p2_recs:
        skip("recall_log mode=blind-p2", "2패스가 돈 기록이 없음(회상 0건)")
    else:
        rows = [json.loads(l) for l in rl.read_text(encoding="utf-8").splitlines() if l.strip()] if rl.exists() else []
        p2_rows = [x for x in rows if x.get("mode") == "blind-p2"]
        ok &= check(f"recall_log.jsonl 에 mode=blind-p2 줄 ({len(p2_rows)}줄)", len(p2_rows) > 0)
        ctxs = {x.get("ctx") for x in p2_rows}
        ok &= check("blind-p2 줄의 ctx 가 2패스 잡 id 를 담는다", all(r["job_id"] in ctxs for r in p2_recs),
                    f"{[r['job_id'] for r in p2_recs if r['job_id'] not in ctxs]}")

    print("\n⑥ 유도 확인 (redo)")
    ind = [r for r in recs if isinstance(r.get("induction"), dict)]
    if not ind:
        skip("유도", "redo 기록 없음 — `correct run --redo --job <id>` 또는 --live")
    for r in ind:
        i = r["induction"]
        prior, off, rel = i.get("prior_atoms") or [], i.get("offered") or [], i.get("relied") or []
        has_p2 = bool((r.get("blind") or {}).get("phase2"))
        if not prior:
            skip(f"{r['job_id']} 유도", "직전 실행이 판별자를 만들지 않았음")
            continue
        ok &= check(f"{r['job_id']}: 직전 판별자 {len(prior)}개 중 offered {len(off)} (2패스 {'있음' if has_p2 else '없음'})",
                    len(off) > 0, f"via={i.get('via')}")
        print(f"        relied {len(rel)} · via {i.get('via')}")

    print("\n⑦ 서버")
    try:
        from geoguesshelper import server as srv
        app = srv.build_app(settings)
        ok &= check("build_app(load_settings()) 성공", app is not None)
        ok &= check("'correct' 잡 종류 등록", "correct" in getattr(srv._QUEUE, "_handlers", {}))
        src = Path(srv.__file__).read_text(encoding="utf-8")
        ok &= check("_build_reports_sync 가 analyze_captures(..., known=pre_known) 로 사전 회상을 넘긴다",
                    "known=pre_known" in src and '"mode": "aided"' in src)
        ok &= check("결과 knowledge 에 pre_recalled · relied_on", '"pre_recalled"' in src and '"relied_on"' in src)
        ok &= check("보고서 잡 뒤 정정 잡 제출(_queue_correction) · client_key", "_queue_correction" in src and 'client_key=f"correct:' in src)
    except Exception as exc:  # noqa: BLE001
        ok &= check("서버 import", False, f"{type(exc).__name__}: {exc}")

    print("\n" + ("전체 통과" if ok else "실패 있음"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
