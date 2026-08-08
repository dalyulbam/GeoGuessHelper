"""geoguesshelper CLI — 서버 실행 또는 URL 단건 추출."""
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

    args = ap.parse_args()

    if args.cmd == "extract":
        from .linkresolver import extract

        pose = asyncio.run(extract(args.url))
        print(json.dumps(pose, ensure_ascii=False, indent=2))
        return

    # 기본: 서버 실행
    from .server import main as serve_main

    serve_main()


if __name__ == "__main__":
    main()
