"""TLS 전략 — 사내 프록시/백신이 TLS 를 가로채는 환경 대응.

이 PC 처럼 TLS 를 가로채는 프록시가 낀 경우:
  · Python(OpenSSL) 기본 certifi 번들엔 사내 루트가 없어 `CERTIFICATE_VERIFY_FAILED`.
  · Windows 트러스트 스토어엔 사내 루트가 있으나, 그 프록시 CA 인증서가 규격에 어긋나
    (Basic Constraints not critical) OpenSSL 이 거부한다. (uv --native-tls / curl 은 관대해 통과)
  · truststore 패키지는 이 환경에서 import 가 멈춘다.

그래서 시작 시 한 번 HTTPS 프로브로 검증 가능 여부를 판정하고, 사내 가로채기로 검증이
실패하면 그 세션에 한해 httpx/anthropic 의 인증서 검증을 끈다(verify=False). 정상 네트워크에선
검증을 유지(secure). 강제하려면 env `GEOHELPER_INSECURE_TLS=1`(끄기)/`=0`(항상 검증).
"""
from __future__ import annotations

import os
import ssl

_mode: str | None = None  # None=미정, "secure", "insecure"

# 실제로 쓰는 대상 호스트를 프로브해야 정확하다(프록시가 연결확인용 도메인은 통과시키기도 함).
_PROBE_URL = "https://maps.googleapis.com/maps/api/js"


def system_ssl_context() -> ssl.SSLContext:
    """Windows 시스템 스토어를 포함하는 기본 컨텍스트."""
    return ssl.create_default_context()


def _forced() -> bool | None:
    v = os.environ.get("GEOHELPER_INSECURE_TLS", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


def is_cert_error(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}".upper()
    return "CERTIFICATE" in msg or "SSL" in msg


def mark_insecure() -> None:
    """런타임 중 인증서 오류를 만나면 이 세션을 insecure 로 전환(이후 호출부터 verify=False)."""
    global _mode
    if _forced() is not False:
        _mode = "insecure"


def decide_tls(*, timeout: float = 5.0) -> str:
    """TLS 모드 판정(1회). 반환 'secure' | 'insecure'."""
    global _mode
    if _mode is not None:
        return _mode

    forced = _forced()
    if forced is True:
        _mode = "insecure"
        return _mode
    if forced is False:
        _mode = "secure"
        return _mode

    try:
        import httpx

        httpx.get(_PROBE_URL, verify=system_ssl_context(), timeout=timeout)
        _mode = "secure"
    except Exception as exc:  # noqa: BLE001
        _mode = "insecure" if is_cert_error(exc) else "secure"  # 오프라인 등은 다운그레이드 안 함
    return _mode


def is_insecure() -> bool:
    return decide_tls() == "insecure"


def httpx_verify():
    """httpx 의 `verify` 인자값. secure → 시스템 컨텍스트, insecure → False."""
    return False if is_insecure() else system_ssl_context()


async def aget(url, *, params=None, headers=None, follow_redirects=False, timeout=25.0):
    """httpx GET — 인증서 오류를 만나면 verify=False 로 1회 폴백하고 세션을 insecure 로 표시."""
    import httpx

    verifies = [httpx_verify()]
    if verifies[0] is not False and _forced() is not False:
        verifies.append(False)  # cert 오류 시 폴백 (forced-secure 면 폴백 안 함)

    last: BaseException | None = None
    for verify in verifies:
        try:
            async with httpx.AsyncClient(
                verify=verify, follow_redirects=follow_redirects, timeout=timeout
            ) as client:
                return await client.get(url, params=params, headers=headers)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if verify is not False and is_cert_error(exc):
                mark_insecure()
                continue
            raise
    assert last is not None
    raise last
