"""TLS 전략 — 사내 프록시/백신이 TLS 를 가로채는 환경 대응.

260912 정정 — 검증을 끌 필요가 없었다.

  오래 "이 환경에서는 인증서 검증이 불가능하다"고 적어 두고 verify=False 로 살았다.
  실제 오류 문구를 끝까지 읽자 답이 그 안에 있었다:

      CERTIFICATE_VERIFY_FAILED: Basic Constraints of CA cert not marked critical

  이것은 "루트가 없다"가 아니라 "있는데 규격 검사에서 걸렸다"는 뜻이다. Python 3.13 의
  `ssl.create_default_context()` 는 **VERIFY_X509_STRICT 를 기본으로 켠다**(3.12 까지는
  꺼져 있었다). strict 는 RFC 5280 위반을 거부하고, Avast 가 끼워 넣는 중간 CA 는
  Basic Constraints 가 non-critical 이라 정확히 여기서 막힌다.

  실측(260912, 같은 프로세스에서 네 조건을 나란히):
      ① create_default_context() 그대로            maps ❌  anthropic ❌
      ② 같은 컨텍스트 + VERIFY_X509_STRICT 해제     maps 200 ✅  anthropic 401 ✅
      ④ Windows ROOT 를 직접 적재(신뢰 목적 필터)   ❌ unable to get local issuer

  그래서 ②를 쓴다. 이것은 **검증을 끄는 것이 아니다** — 체인 검증·만료·호스트명 확인은
  그대로 살아 있고, 3.13 이 새로 추가한 규격 엄격성만 3.12 수준으로 되돌린다.
  중요한 이유: 예전 경로에서는 ANTHROPIC_API_KEY 가 **검증되지 않은** 연결로 나갔다.

모드는 셋이다.
  secure    strict 검증 통과 — 가로채기 없는 정상 네트워크
  relaxed   strict 만 해제하면 통과 — 가로채기 환경의 정상 상태(권장 종착지)
  insecure  GEOHELPER_INSECURE_TLS=1 로 **명시적으로 껐을 때만**.
            프로브가 완화로도 실패하면 relaxed 로 두고 호스트별 런타임 판정에 맡긴다 —
            프로브는 maps.googleapis.com 하나를 본 것이고, 그 실패는 api.anthropic.com
            의 인증서에 대해 아무것도 말해 주지 않는다. 예전 판은 여기서 전역 insecure 로
            뒤집어 **API 키가 검증 없는 연결로 나갔다**(260912 Codex 검토에서 잡힘).

강제: env `GEOHELPER_INSECURE_TLS=1`(끄기) / `=0`(항상 검증, strict 완화도 안 함).
"""
from __future__ import annotations

import os
import ssl

# None=미정, "secure" | "relaxed" | "insecure" — 시작 시 프로브가 1회 결정
_mode: str | None = None
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


def system_ssl_context(*, strict: bool | None = None) -> ssl.SSLContext:
    """Windows 시스템 스토어를 포함하는 기본 컨텍스트.

    strict=None 이면 시작 프로브가 정한 모드를 따른다(relaxed 면 완화).
    strict=True 는 3.13 기본 그대로, False 는 VERIFY_X509_STRICT 만 해제한다.
    """
    neutralize_keylog()      # create_default_context 보다 반드시 먼저
    ctx = ssl.create_default_context()
    if strict is None:
        strict = _mode != "relaxed"
    if not strict:
        _relax(ctx)
    return ctx


def _relax(ctx: ssl.SSLContext) -> ssl.SSLContext:
    """규격 엄격성만 끈다. 체인·만료·호스트명 검증은 건드리지 않는다."""
    flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if flag:
        ctx.verify_flags &= ~flag
    return ctx


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
    """TLS 모드 판정(1회). 반환 'secure' | 'relaxed' | 'insecure'.

    strict → relaxed → 포기 순으로 **한 단계씩만** 내려간다. 예전에는 strict 실패를
    곧바로 verify=False 로 읽어, 완화 한 칸이면 됐을 것을 검증 전면 해제로 갚았다.
    """
    global _mode
    if _mode is not None:
        return _mode

    forced = _forced()
    if forced is True:
        _mode = "insecure"
        return _mode
    if forced is False:
        _mode = "secure"     # 항상 검증 — strict 완화도 안 한다
        return _mode

    import httpx

    try:
        httpx.get(_PROBE_URL, verify=system_ssl_context(strict=True), timeout=timeout)
        _mode = "secure"
        return _mode
    except Exception as exc:  # noqa: BLE001
        if not is_cert_error(exc):
            _mode = "secure"      # 오프라인·DNS 등은 다운그레이드 사유가 아니다
            return _mode
        _strict_error.append(f"{type(exc).__name__}: {exc}")

    # 규격 엄격성만 풀어 본다 — 여기서 통과하면 검증은 계속 살아 있다.
    try:
        httpx.get(_PROBE_URL, verify=system_ssl_context(strict=False), timeout=timeout)
        _mode = "relaxed"
        return _mode
    except Exception as exc:  # noqa: BLE001
        _relaxed_error.append(f"{type(exc).__name__}: {exc}")

    # 완화로도 실패했다. 그래도 **전역으로 검증을 끄지 않는다** — 이 프로브는
    # maps.googleapis.com 하나를 본 것이고, 그 실패가 api.anthropic.com 의 인증서에
    # 대해 말해 주는 것은 없다. 예전 판은 여기서 _mode="insecure" 로 두었고,
    # is_insecure() 가 호스트를 가리지 않으므로 **API 키가 검증 없는 연결로 나갔다**.
    # 이제는 relaxed 로 두고, 실제로 실패하는 호스트만 aget/fetch 가 런타임에 내린다.
    _mode = "relaxed"
    return _mode


_strict_error: list[str] = []
_relaxed_error: list[str] = []


def strict_error() -> str:
    """strict 프로브가 낸 사유(배너·진단용). 없으면 빈 문자열."""
    return _strict_error[0] if _strict_error else ""


def probe_unverified() -> bool:
    """완화 프로브까지 실패했는가 — 배너가 '아직 확인 못 함'을 말하게 한다.

    이 값이 True 여도 검증을 끄지는 않는다(호스트별 런타임 판정에 맡긴다).
    """
    return bool(_relaxed_error)


def is_insecure(host: str = "") -> bool:
    """전역 판정이 insecure 이거나, 그 호스트가 런타임에 다운그레이드된 경우 True."""
    if decide_tls() == "insecure":
        return True
    return bool(host) and host.lower() in _insecure_hosts


def httpx_verify(host: str = ""):
    """httpx 의 `verify` 인자값. secure/relaxed → 시스템 컨텍스트, insecure → False.

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
        # 검증을 끄기 **전에** 규격 완화를 한 번 거친다. 예전에는 이 칸이 없어서
        # strict 하나에 걸린 요청이 곧바로 verify=False 로 떨어졌다.
        if decide_tls() == "secure":
            verifies.append(system_ssl_context(strict=False))
        verifies.append(False)

    last: BaseException | None = None
    for i, verify in enumerate(verifies):
        try:
            async with httpx.AsyncClient(
                verify=verify, follow_redirects=follow_redirects, timeout=timeout
            ) as client:
                return await client.get(url, params=params, headers=headers)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if verify is not False and is_cert_error(exc):
                # 마지막 검증 후보까지 실패했을 때만 이 호스트를 다운그레이드로 기록한다.
                if i == len(verifies) - 2:
                    mark_insecure(host)
                continue
            raise
    assert last is not None
    raise last
