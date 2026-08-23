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

    # 기본: 서버 실행
    from .server import main as serve_main

    serve_main()


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
