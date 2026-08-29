"""geoguesshelper CLI — 서버 실행, URL 단건 추출, 저장소 정리."""
from __future__ import annotations

import argparse
import asyncio
import json


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="geoguesshelper",
        description="지오게서 구글 로드뷰 2분할 뷰어 · 캡처 · 분석",
    )
    sub = ap.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="로컬 웹서버 실행 (브라우저 자동 오픈)")
    p_serve.set_defaults(cmd="serve")

    p_extract = sub.add_parser("extract", help="URL 하나에서 포즈만 파싱해 출력")
    p_extract.add_argument("url")

    p_clean = sub.add_parser(
        "clean",
        help="쌓인 캡처·임시파일·로그 정리 (기본은 모의 실행)",
        description=(
            "지울 수 있는 것을 범주별로 보여주고 정리한다. --apply 없이는 아무것도 지우지 "
            "않는다. docs/knowledge/ 와 git 이 추적 중인 파일은 어떤 옵션으로도 지워지지 않는다."
        ),
    )
    p_clean.add_argument("--apply", action="store_true",
                         help="실제로 삭제한다 (없으면 모의 실행)")
    p_clean.add_argument("--category", "-c", action="append", default=[],
                         help="정리할 범주(반복 지정 가능). 생략하면 기본 범주만")
    p_clean.add_argument("--all", action="store_true",
                         help="기본이 아닌 범주까지 전부 (참조된 캡처·pycache 포함)")
    p_clean.add_argument("--older-than", type=float, default=0.0, metavar="DAYS",
                         help="이 일수보다 오래된 파일만")
    p_clean.add_argument("--keep-last", type=int, default=0, metavar="N",
                         help="보고서 HTML 을 최신 N건만 남긴다")
    p_clean.add_argument("--json", action="store_true", help="JSON 으로 출력")

    p_rt = sub.add_parser(
        "retranslate",
        help="기존 보고서 HTML 의 미번역(영어 잔존) 텍스트를 사후 번역",
        description=(
            "번역 실패로 영어가 남은 언어 탭을 제자리에서 고친다. 원본은 .bak 으로 보존. "
            "--dry-run 이면 API 호출 없이 대상 개수만 보여준다."
        ),
    )
    p_rt.add_argument("paths", nargs="+", help="보고서 .html 파일 또는 폴더")
    p_rt.add_argument("--lang", "-l", action="append", default=[],
                      help="대상 언어(반복 지정 가능). 생략하면 문서에 든 언어 전부")
    p_rt.add_argument("--base", default="en", help="원문 언어 (기본 en)")
    p_rt.add_argument("--dry-run", action="store_true",
                      help="번역하지 않고 대상 개수만 보여준다")

    p_ex = sub.add_parser(
        "expand",
        help="지식 원자 전방위 확장 (worldwide/people/counterpart)",
        description=(
            "저장된 원자를 씨앗으로, 전세계 병렬 사례·같은 업의 인물·반대급부 축으로 "
            "새 원자를 만든다. 웹 검색 없이 모델 지식만 쓰고, 체크포인트로 이어서 돈다."
        ),
    )
    p_ex.add_argument("--limit", type=int, default=0, help="처리할 씨앗 수 (0=남은 전부)")
    p_ex.add_argument("--batch", type=int, default=6, help="호출당 씨앗 수")
    p_ex.add_argument("--parallel", type=int, default=3, help="동시 호출 수")
    p_ex.add_argument("--dry-run", action="store_true", help="호출 없이 남은 씨앗 수만 보여준다")

    p_wk = sub.add_parser(
        "wiki",
        help="위키피디아 인제스트 (시대·왕조·제국·국가 리스트 → 원자)",
        description=(
            "위키피디아 리스트 문서 4개(시대 구획·왕조·제국·사라진 국가)를 청크로 갈라 "
            "원자로 압축한다. 모든 원자에 출처 URL 이 남고, 체크포인트로 이어서 돈다."
        ),
    )
    p_wk.add_argument("--limit", type=int, default=0, help="처리할 청크 수 (0=남은 전부)")
    p_wk.add_argument("--parallel", type=int, default=3, help="동시 호출 수")
    p_wk.add_argument("--dry-run", action="store_true", help="호출 없이 청크 계획만 보여준다")

    p_ing = sub.add_parser(
        "ingest",
        help="지식 적재가 생략된 보고서를 찾아 사후 적재 (doctor)",
        description=(
            "보고서 HTML 과 지식 노트를 대사해 결손(예산 초과로 적재가 생략된 보고서 등)을 "
            "찾고, 잡 로그에 남은 분석 결과로 사후 적재한다. 인자 없이 부르면 결손 목록만 "
            "보여주는 모의 실행이고, --apply 를 붙여야 실제로 적재한다(LLM 호출·비용 발생). "
            "특정 보고서만 지정하려면 경로를 넘긴다."
        ),
    )
    p_ing.add_argument("paths", nargs="*", help="보고서 .html 파일 또는 폴더 (생략 = 전체 스캔)")
    p_ing.add_argument("--scan", action="store_true",
                       help="(기본 동작과 같음) 결손만 나열 — 과거 호환용 명시 플래그")
    p_ing.add_argument("--apply", action="store_true",
                       help="실제로 적재한다 (없으면 결손 목록만)")
    p_ing.add_argument("--redo", action="store_true",
                       help="이미 노트가 있는 보고서도 다시 적재")

    args = ap.parse_args()

    if args.cmd == "extract":
        from .linkresolver import extract

        pose = asyncio.run(extract(args.url))
        print(json.dumps(pose, ensure_ascii=False, indent=2))
        return

    if args.cmd == "clean":
        _clean(args)
        return

    if args.cmd == "retranslate":
        _retranslate(args)
        return

    if args.cmd == "expand":
        from . import expand
        from .config import load_settings

        state = expand.run(
            load_settings(),
            limit=args.limit, batch=args.batch, parallel=args.parallel, dry_run=args.dry_run,
        )
        print(json.dumps(
            {k: state[k] for k in ("created", "merged", "skipped", "cost_usd")}
            | {"seeds_done": len(state["seeds_done"])},
            ensure_ascii=False, indent=2))
        return

    if args.cmd == "wiki":
        from . import wiki
        from .config import load_settings

        state = wiki.run(
            load_settings(),
            limit=args.limit, parallel=args.parallel, dry_run=args.dry_run,
        )
        print(json.dumps(
            {k: state[k] for k in ("created", "merged", "skipped", "cost_usd")}
            | {"chunks_done": len(state["chunks_done"])},
            ensure_ascii=False, indent=2))
        return

    if args.cmd == "ingest":
        _ingest(args)
        return

    # 기본: 서버 실행
    from .server import main as serve_main

    serve_main()


def _ingest(args) -> None:
    """보고서↔지식 노트 대사 + 사후 적재.

    재료는 잡 로그(docs/jobs/jobs.jsonl)의 result.primaryAnalysis 다 — 분석은 이미
    끝나 있으므로 비전 재호출 없이 적재 LLM 한 번이면 된다. 리서치 결과는 잡 로그에
    저장되지 않아 사후 적재 원자는 분석 근거만 갖는다(한계를 노트에 명시).
    """
    import re
    from pathlib import Path

    from . import knowledge
    from .config import load_settings

    settings = load_settings()
    st = knowledge.store_for(settings)
    idx = st.index()
    noted = set((idx.get("reports") or {}).keys())

    # 잡 로그: 보고서 파일명 → 잡 결과 (뒤 항목이 더 최신이라 덮어쓴다)
    by_file: dict[str, dict] = {}
    jobs_log = settings.jobs_dir / "jobs.jsonl"
    if jobs_log.exists():
        for line in jobs_log.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            res = d.get("result") or {}
            for rep in (res.get("reports") or []):
                if isinstance(rep, dict) and rep.get("file"):
                    by_file[rep["file"]] = res

    # 대상: 지정 경로 또는 보고서 폴더 전체(하위 폴더 포함, 아틀라스/문서 폴더 제외)
    if args.paths:
        files: list[Path] = []
        for p in args.paths:
            pth = Path(p)
            files += sorted(pth.rglob("report_*.html")) if pth.is_dir() else [pth]
    else:
        files = sorted(settings.reports_dir.rglob("report_*.html"))

    targets = [f for f in files if args.redo or f.name not in noted]
    print(f"[ingest] 보고서 {len(files)}건 중 노트 결손 {len(targets)}건"
          + ("" if args.redo else f" (노트 있음 {len(files) - len(targets)}건은 건너뜀)"))

    coord_re = re.compile(r"_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)_(\d{6})_(\d{6})_([a-z-]+)\.html$")
    total_cost, done = 0.0, 0
    for f in targets:
        res = by_file.get(f.name)
        analysis = (res or {}).get("primaryAnalysis")
        m = coord_re.search(f.name)
        if not analysis or not m:
            why = "잡 로그에 분석 결과 없음" if not analysis else "파일명에서 좌표를 못 읽음"
            print(f"  - {f.name}: 재료 없음({why}) — 건너뜀")
            continue
        if not args.apply:
            print(f"  - {f.name}: 적재 가능 (--apply 로 실행)")
            continue

        lat, lng = float(m.group(1)), float(m.group(2))
        langs = [x for x in m.group(5).split("-") if x]
        a = analysis.get("analysis") if isinstance(analysis, dict) else {}
        g = (a or {}).get("best_guess") or {}
        place_label = ", ".join(x for x in [g.get("city"), g.get("region_or_state"),
                                            g.get("country")] if x)
        known = [x for x in (st.load(i) for i in
                             ((res.get("knowledge") or {}).get("recalled") or [])) if x]
        kb = knowledge.ingest(
            settings, analysis=analysis, research=None,
            lat=lat, lng=lng, report_file=f.name, known=known,
        )
        total_cost += kb.get("cost_usd") or 0.0
        summary = ""
        script_path = f.parent / (f.stem + ".script.json")
        if script_path.exists():
            try:
                summary = (json.loads(script_path.read_text(encoding="utf-8"))
                           .get("lede") or "")
            except ValueError:
                pass
        knowledge.write_report_note(
            settings, report_file=f.name, place=place_label, lat=lat, lng=lng,
            atoms=kb.get("atoms") or [], langs=langs,
            summary=(summary + "\n\n(사후 적재 — 리서치 결과 없이 분석만으로 적재됨)").strip(),
        )
        done += 1
        print(f"  + {f.name}: 원자 {kb.get('created', 0)} 신규 · {kb.get('merged', 0)} 병합"
              f" · ${kb.get('cost_usd') or 0.0:.3f}")
    if args.apply:
        print(f"[ingest] 적재 {done}건 · ${total_cost:.3f}")


def _clean(args) -> None:
    from . import cleanup
    from .config import load_settings

    settings = load_settings()
    cats = list(args.category)
    if args.all:
        cats = [c["key"] for c in cleanup.scan(settings)["categories"]]

    result = cleanup.sweep(
        settings,
        categories=cats or None,
        older_than_days=args.older_than,
        keep_last=args.keep_last,
        apply=args.apply,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    report = cleanup.scan(settings, older_than_days=args.older_than, keep_last=args.keep_last)
    report.pop("_cats", None)
    print(cleanup.format_report(report))
    print()
    verb = "삭제함" if result["applied"] else "삭제 예정"
    print(f"[{', '.join(result['categories'])}] {verb}: "
          f"{result['removed']}개 · {result['freedHuman']}")
    if result["failed"]:
        print(f"실패 {len(result['failed'])}건:")
        for f in result["failed"][:5]:
            print(f"  {f['path']} — {f['error']}")
    if not result["applied"] and result["removed"]:
        print("\n실제로 지우려면 --apply 를 붙이세요.")


def _retranslate(args) -> None:
    from pathlib import Path

    from . import retranslate
    from .config import load_settings

    settings = load_settings()
    files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(p.glob("*.html"))    # *.html.bak 은 패턴에 안 걸린다
        elif p.suffix.lower() == ".html" and p.exists():
            files.append(p)
        else:
            print(f"건너뜀(대상 아님): {raw}")
    if not files:
        print("대상 .html 이 없습니다.")
        return

    total = 0.0
    for f in files:
        r = retranslate.repair_file(f, settings, base_lang=args.base,
                                    langs=args.lang or None, dry_run=args.dry_run)
        total += r["cost_usd"]
        per = " · ".join(
            f"{lang}: {v['found']}건"
            + ("" if args.dry_run else
               f" → {v.get('translated', 0)}건 번역"
               + (f" (실패 {v['failed_chunks']}덩이)" if v.get("failed_chunks") else ""))
            for lang, v in r["per_lang"].items()
        )
        print(f"{f.name}: {per} · 치환 {r['edits']}곳"
              + ("" if args.dry_run else f" · ${r['cost_usd']:.4f}"))
    if args.dry_run:
        print("\n(모의 실행 — 실제 번역은 --dry-run 없이)")
    else:
        print(f"\n총비용 ${total:.4f} · 원본은 *.html.bak 으로 보존됨")


if __name__ == "__main__":
    main()
