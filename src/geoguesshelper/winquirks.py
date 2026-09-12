"""이 PC 를 멈추게 하는 Windows 결함 우회.

`tls.neutralize_keylog()` 과 같은 성격의 모듈이다 — 코드 버그가 아니라 **머신 환경**이
파이썬을 멈추게 하는 자리를, 앱 프로세스 안에서만 비껴간다.

── WMI 조회가 멈춘다 ────────────────────────────────────────────────

260904 실측. anthropic SDK 호출이 20분 넘게 끝나지 않던 원인을 스택으로 잡았다:

    platform.system()  →  platform.uname()  →  win32_ver()  →  _win32_ver()
                       →  platform._wmi_query('OS', ...)     ← 여기서 멈춤

SDK 는 `x-stainless-os` **텔레메트리 헤더** 한 줄을 채우려고 `platform.system()` 을
부른다(`anthropic/_base_client.py: get_platform()`). 즉 네트워크가 아니라 요청을
**만들다가** 멈춘 것이고, 그래서 `deadline_s`/`LLMTimeout` 도 걸리지 않았다 — 벽시계
마감은 스트림을 도는 동안만 재는데, 스트림이 시작조차 못 했다.

이것이 오래 "httpx 가 느리다"로 오진됐던 이유: 같은 순간 curl 은 2.4초에 돌아왔다.
curl 은 파이썬 `platform` 모듈을 거치지 않으니 당연했다. 실제로 아래 우회만 적용하면
**순정 httpx 로도** 같은 호출이 2.7초에 끝난다 — httpx 는 처음부터 멀쩡했다.

왜 그냥 꺼도 되는가: `_win32_ver` 은 WMI 가 실패하면 레지스트리와
`sys.getwindowsversion()` 으로 폴백한다(WMI 가 없는 Windows 에서 늘 도는 정상 경로).
이 PC 에서 폴백 값은 `Windows-11-10.0.26200-SP0` 로 정확했고, 이 앱은 WMI 에서만 얻을
수 있는 값을 어디에서도 쓰지 않는다. 잃는 것은 없다.

되돌리려면 `GEOHELPER_WMI=1`.
"""
from __future__ import annotations

import os
import sys

_wmi_neutralized = False


def _wanted() -> bool:
    if sys.platform != "win32":
        return False
    return os.environ.get("GEOHELPER_WMI", "").strip().lower() not in ("1", "true", "yes", "on")


def neutralize_wmi() -> bool:
    """`platform._wmi_query` 를 즉시 실패시킨다. 반환은 '이번에 껐는가'.

    멱등하다 — 여러 진입점(서버·CLI)에서 불러도 한 번만 바꾼다. `platform.uname()` 은
    결과를 캐시하므로, 캐시가 차기 **전에** 부르는 것이 중요하다(= 시작 지점에서).
    """
    global _wmi_neutralized
    if _wmi_neutralized or not _wanted():
        return False
    import platform

    if not hasattr(platform, "_wmi_query"):   # 다른 파이썬 빌드/버전 — 건드릴 것이 없다
        return False

    def _no_wmi(table, *keys):
        # _win32_ver 이 잡아 폴백하는 바로 그 예외. 원본도 WMI 부재 시 이것을 낸다.
        raise OSError("not supported")

    platform._wmi_query = _no_wmi   # type: ignore[attr-defined]
    platform._uname_cache = None    # type: ignore[attr-defined]  캐시에 이미 담겼다면 다시 뜨게
    _wmi_neutralized = True
    return True


def wmi_neutralized() -> bool:
    return _wmi_neutralized
