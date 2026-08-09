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

_mode: str | None = None  # None=미정, "secure", "insecure" — 시작 시 프로브가 1회 결정
# 런타임 중 인증서 오류를 낸 **호스트만** 기록한다. 예전에는 이럴 때 전역 _mode 를
# "insecure" 로 뒤집었는데, 그러면 구글 정적 이미지 요청 한 번이 실패한 것만으로 이후
# 모든 연결(= ANTHROPIC_API_KEY 를 보내는 연결 포함)의 인증서 검증이 꺼졌다. 게다가 시작
# 배너는 계속 "TLS 검증 켬"이라고 표시해 아무도 알아채지 못했다.
_insecure_hosts: set[str] = set()
_downgrades: list[str] = []       # 배너/진단용 기록

# 실제로 쓰는 대상 호스트를 프로브해야 정확하다(프록시가 연결확인용 도메인은 통과시키기도 함).
_PROBE_URL = "https://maps.googleapis.com/maps/api/js"


_keylog_removed: str | None = None


def neutralize_keylog() -> str | None:
    """SSLKEYLOGFILE 을 제거한다. 반환값은 제거한 값(없었으면 None).

    왜 필요한가 — 이 PC 에서 서버가 시작조차 못 하던 원인:

      Avast 가 HTTPS 검사를 위해 감시 대상 프로세스 환경에
          SSLKEYLOGFILE=\\\\.\\aswMonFltProxy\\<id>
      를 주입한다. ssl.create_default_context() 는 이 값을 읽어 keylog_filename 에
      세팅하고, CPython 은 그 경로를 fopen() 해 FILE* 를 OpenSSL 의 BIO_new_fp() 로
      넘긴다. 그런데 이 Python 빌드는 OpenSSL 을 **정적 링크**하고 python.exe 는
      applink.c 를 갖고 있지 않아, OPENSSL_Uplink 가 "no OPENSSL_Applink" 를 찍고
      **ExitProcess(1)** 로 프로세스를 스스로 끝낸다. (외부에서 죽이는 게 아니다 —
      종료 코드가 1 이고, 변수를 지우면 즉시 통과한다.)

    지우는 게 안전한 이유: 이 값은 TLS 세션 키를 파일/디바이스로 흘려보내라는 지시다.
    우리 프로세스와 그 자식(Chromium 등)에는 필요 없고, 켜두면 Anthropic 키가 오가는
    연결의 세션 키까지 외부로 나간다. 앱 프로세스 안에서만 지우므로 시스템 설정은
    건드리지 않는다.
    """
    global _keylog_removed
    v = os.environ.pop("SSLKEYLOGFILE", None)
    if v:
        _keylog_removed = v
    return v


def keylog_removed() -> str | None:
    return _keylog_removed


def system_ssl_context() -> ssl.SSLContext:
    """Windows 시스템 스토어를 포함하는 기본 컨텍스트."""
    neutralize_keylog()      # create_default_context 보다 반드시 먼저
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


def _host_of(url) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(str(url)).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def mark_insecure(host: str = "") -> None:
    """런타임 인증서 오류 → **그 호스트에 한해서만** 이후 verify=False.

    전역 다운그레이드는 하지 않는다. 사내 프록시가 구글만 가로채는 흔한 경우에도
    api.anthropic.com 연결은 계속 검증된다.
    """
    if _forced() is False:
        return
    h = (host or "").lower()
    if not h:
        return
    if h not in _insecure_hosts:
        _insecure_hosts.add(h)
        _downgrades.append(h)


def insecure_hosts() -> list[str]:
    return sorted(_insecure_hosts)


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


def is_insecure(host: str = "") -> bool:
    """전역 판정이 insecure 이거나, 그 호스트가 런타임에 다운그레이드된 경우 True."""
    if decide_tls() == "insecure":
        return True
    return bool(host) and host.lower() in _insecure_hosts


def httpx_verify(host: str = ""):
    """httpx 의 `verify` 인자값. secure → 시스템 컨텍스트, insecure → False.

    host 를 주지 않으면 **시작 프로브의 판정만** 적용된다 — 다른 호스트의 런타임 실패가
    이 연결의 검증을 끄지 못한다(Anthropic 클라이언트가 이 경로를 쓴다).
    """
    return False if is_insecure(host) else system_ssl_context()


async def aget(url, *, params=None, headers=None, follow_redirects=False, timeout=25.0):
    """httpx GET — 인증서 오류를 만나면 verify=False 로 1회 폴백하고 세션을 insecure 로 표시."""
    import httpx

    host = _host_of(url)
    verifies = [httpx_verify(host)]
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
                mark_insecure(host)   # ← 이 호스트만. 전역 아님.
                continue
            raise
    assert last is not None
    raise last
