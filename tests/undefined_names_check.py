"""미정의 이름 검사 — 함수를 쪼갤 때 지역 import 가 따라오지 않는 사고를 막는다.

왜 이 파일이 따로 있나. 260914 에 correction.py 의 4단계(적재·채점)가 별도 함수로 떨어져
나오면서 `from . import knowledge` 가 따라오지 않았다. 그 줄은 실행되기 전까지 아무 티가
나지 않는다 — 파이썬은 전역 이름을 **호출 시점에** 찾기 때문이다. 그래서:

  · 잡 1~3단계(LLM 호출 ~152초, 실제 과금)를 다 지나고 나서야
  · 4단계 첫 줄에서 NameError 가 나고
  · 그 예외는 except 로 잡혀 기록에만 남아(PARTIAL) 조용히 반복됐다 — 12건.

이 부류는 사람이 눈으로 잡을 게 아니라 도구가 잡아야 한다. pyflakes 는 이걸 즉시
집어낸다(당시 `undefined name 'knowledge'` 3건). 다른 경고(미사용 import 등)는 이
저장소가 의도적으로 쓰는 패턴(가용성 탐지용 import)과 겹치므로 **보지 않는다** —
검사가 시끄러우면 결국 아무도 안 본다.

실행:  uv run python tests/undefined_names_check.py
       pyflakes 가 없으면 skip 한다(설치: uv pip install pyflakes).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "geoguesshelper"


def main() -> int:
    try:
        import pyflakes  # noqa: F401
    except ModuleNotFoundError:
        print("SKIP - pyflakes 미설치 (uv pip install pyflakes)")
        return 0

    out = subprocess.run([sys.executable, "-m", "pyflakes", str(SRC)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout
    bad = [ln for ln in out.splitlines() if "undefined name" in ln]
    for ln in bad:
        print("  FAIL  " + ln.strip())
    if bad:
        print(f"\nFAIL - 미정의 이름 {len(bad)}건")
        print("   함수를 옮기거나 쪼갤 때 그 안의 지역 import 가 같이 왔는지 확인하십시오.")
        return 1
    print(f"  PASS  미정의 이름 0건 ({len(list(SRC.glob('*.py')))}개 모듈)")
    print("\nPASS - 실패 0건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
