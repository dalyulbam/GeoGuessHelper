"""Claude 호출 공통 계층.

analyze / research / translate / knowledge 가 각자 클라이언트를 만들고 각자 비용을
계산하던 것을 여기로 모은다. 얻는 것:

  · 클라이언트 재사용 — 호출마다 `httpx.Client` 를 새로 만들고 닫지 않던 누수를 제거.
    (프로세스당 1개, 커넥션 풀 재사용 → TLS 핸드셰이크도 아낌)
  · 스트리밍 기본  — 긴 호출이 httpx read timeout 에 걸려 통째로 실패하던 경로를 없앤다.
  · 프롬프트 캐싱  — 호출부가 cache_control 을 블록에 얹기만 하면 된다.
  · 일관된 비용 계산 — 캐시 write/read 단가와 web_search 요청 수까지 반영.

모델과 thinking 설정은 **건드리지 않는다**(settings.model 그대로, thinking 미지정).
"""
from __future__ import annotations

import threading
from typing import Any

from .config import Settings

# claude-opus-4-8 기준 단가 ($/MTok)
_IN_PER_MTOK = 5.0
_OUT_PER_MTOK = 25.0
_CACHE_WRITE_MULT = 1.25   # 캐시 기록은 입력가의 1.25배
_CACHE_READ_MULT = 0.1     # 캐시 적중은 입력가의 0.1배
_WEB_SEARCH_USD = 0.01     # $10 / 1000 requests

_client = None
_client_key: tuple | None = None
_client_lock = threading.Lock()

# ── 역할 → 모델 ──────────────────────────────────────────────────
# 호출부는 모델 이름을 몰라도 된다. "이건 비전 추론" / "이건 사실 검색" 만 말하면
# 라우팅은 여기서 한 곳에서 결정된다(설정에서 바꿀 수 있다).
ROLES = ("vision", "fact", "reason", "draft", "verify")


def model_for(settings: Settings, role: str | None) -> str:
    return {
        "vision": settings.model_vision,
        "fact": settings.model_fact,
        "reason": settings.model_reason,
        "draft": settings.model_draft,
        "verify": settings.model_verify,
    }.get(role or "", settings.model)


def _is_opus5_family(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith("claude-opus-5") or m.startswith("claude-fable-5") or m.startswith("claude-mythos-5")


# 모델별 단가 ($/MTok in, out). 모르는 모델은 opus 기준으로 보수적으로 잡는다.
_PRICES = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _price(model: str) -> tuple[float, float]:
    return _PRICES.get((model or "").lower(), (_IN_PER_MTOK, _OUT_PER_MTOK))


class LLMUnavailable(RuntimeError):
    """anthropic 미설치 / 키 없음 — 호출부가 사용자 메시지로 바꿔 쓴다."""


class LLMTimeout(TimeoutError):
    """호출이 벽시계 상한을 넘었다 — 스트림을 끊고 호출부가 soft-fail 로 처리한다."""


class LLMCanceled(RuntimeError):
    """사용자가 작업을 취소했다 — 진행 중인 스트림을 즉시 끊는다."""


def get_client(settings: Settings):
    """프로세스 전역 Anthropic 클라이언트(키·TLS 모드가 같으면 재사용)."""
    global _client, _client_key
    if not settings.has_anthropic:
        raise LLMUnavailable("ANTHROPIC_API_KEY 가 필요합니다(.env).")
    try:
        import anthropic  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise LLMUnavailable("anthropic 미설치 — `uv sync --extra analyze` 후 다시 시도하세요.") from exc

    import httpx

    from .tls import httpx_verify

    verify = httpx_verify()
    key = (settings.anthropic_api_key, verify is False)
    with _client_lock:
        if _client is None or _client_key != key:
            if _client is not None:
                try:
                    _client.close()
                except Exception:  # noqa: BLE001
                    pass
            _client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key,
                http_client=httpx.Client(
                    verify=verify,
                    timeout=httpx.Timeout(settings.llm_timeout_s, connect=15.0),
                    limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
                ),
                max_retries=2,
            )
            _client_key = key
    return _client


def close_client() -> None:
    global _client, _client_key
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = None
        _client_key = None


def cost_of(usage, model: str | None = None) -> float:
    """usage → USD. 캐시 write/read 와 web_search 요청 수까지 반영.

    model 을 주면 그 모델 단가로 계산한다 — 라우팅 후에는 호출마다 단가가 다르다.
    """
    if usage is None:
        return 0.0
    pin, pout = _price(model) if model else (_IN_PER_MTOK, _OUT_PER_MTOK)
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    c = (
        inp / 1_000_000 * pin
        + out / 1_000_000 * pout
        + cw / 1_000_000 * pin * _CACHE_WRITE_MULT
        + cr / 1_000_000 * pin * _CACHE_READ_MULT
    )
    st = getattr(usage, "server_tool_use", None)
    if st is not None:
        c += (getattr(st, "web_search_requests", 0) or 0) * _WEB_SEARCH_USD
    return c


def cache_stats(usage) -> dict:
    """캐시가 실제로 먹었는지 확인용(0이면 어딘가에서 프리픽스가 깨진 것)."""
    if usage is None:
        return {}
    return {
        "input": getattr(usage, "input_tokens", 0) or 0,
        "output": getattr(usage, "output_tokens", 0) or 0,
        "cacheWrite": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cacheRead": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


def call(
    settings: Settings,
    *,
    system,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
    max_tokens: int = 4096,
    effort: str | None = None,
    stream: bool = True,
    deadline_s: float | None = None,
    should_stop=None,
    role: str | None = None,
    model: str | None = None,
):
    """messages.create 래퍼. 반환은 SDK Message 그대로.

    effort 는 settings 에서 온 값만 넘긴다(None 이면 API 기본값 = high).

    thinking 은 **명시적으로** 넘긴다. Opus 5 는 미지정 시 adaptive 가 켜지는데(Opus 4.8 은
    반대로 꺼짐), 암묵적으로 놔두면 모델 문자열만 바꿔도 토큰과 동작이 조용히 달라진다.
    `thinking: disabled` 는 effort 가 high 이하일 때만 허용되므로 그 조합도 여기서 막는다.
    """
    client = get_client(settings)
    use_model = model or model_for(settings, role)
    kwargs: dict[str, Any] = {
        "model": use_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    if effort:
        kwargs["output_config"] = {"effort": effort}

    mode = (getattr(settings, "thinking_mode", "adaptive") or "adaptive").lower()
    if _is_opus5_family(use_model):
        # Opus 5 / Fable 5 계열. Fable 5 는 thinking 이 항상 켜져 있어 disabled 를 보내면 400 이다.
        if mode == "disabled" and effort not in ("xhigh", "max") and not use_model.lower().startswith(
            ("claude-fable-5", "claude-mythos-5")
        ):
            kwargs["thinking"] = {"type": "disabled"}
        else:
            kwargs["thinking"] = {"type": "adaptive"}
    else:
        # Sonnet 5 / Haiku 등. Sonnet 5 도 adaptive 를 지원하지만, 빠른 사실 조회에는
        # thinking 이 지연만 늘리므로 기본은 끈다(속도가 목적인 역할들이다).
        if role in ("fact", "draft") and not use_model.lower().startswith("claude-haiku"):
            kwargs["thinking"] = {"type": "disabled"}

    if not stream:
        return client.messages.create(**kwargs)

    # ── 벽시계 상한 ──────────────────────────────────────────────
    # httpx 의 read timeout 은 **청크 간격**이지 총 소요가 아니다. 서버측 도구(web_search)가
    # 오래 도는 동안에도 스트림은 keepalive 를 계속 보내므로 그 타임아웃은 영영 걸리지 않는다.
    # 실제로 리서치 호출 하나가 22분 넘게 끝나지 않고 큐 전체를 막은 사례가 있었다.
    # 그래서 이벤트를 직접 돌면서 총 경과를 재고, 넘으면 스트림을 닫아 요청을 버린다.
    import time as _time

    end = (_time.monotonic() + deadline_s) if deadline_s else None
    with client.messages.stream(**kwargs) as s:
        # 이벤트를 직접 도는 두 번째 이유: 취소. 예전에는 취소 플래그를 단계 경계에서만 봐서,
        # 20분째 안 끝나는 리서치 호출은 취소 버튼으로도 멈출 수 없었다.
        if end is not None or should_stop is not None:
            for _ in s:
                if end is not None and _time.monotonic() > end:
                    raise LLMTimeout(
                        f"{settings.model} 호출이 {deadline_s:.0f}초를 넘겨 중단했습니다."
                    )
                if should_stop is not None and should_stop():
                    raise LLMCanceled("호출이 취소되었습니다.")
        return s.get_final_message()


def used_model(resp) -> str | None:
    """응답이 실제로 어떤 모델로 처리됐는지(라우팅 후 비용 계산에 필요)."""
    return getattr(resp, "model", None)


def spend(resp) -> float:
    """응답 → USD. 실제 사용 모델 단가를 자동 적용."""
    return cost_of(getattr(resp, "usage", None), used_model(resp))


def gather(tasks: list, settings: Settings, *, limit: int | None = None) -> list:
    """LLM 호출들을 스레드로 동시에 돌린다. tasks = [callable, ...] → [결과 or Exception, ...]

    한 작업 **안에서** 독립 단계를 겹치기 위한 것이다. 큐 자체는 여전히 순차라
    여러 탭의 보고서 순서는 그대로 보장된다.
    예외는 raise 하지 않고 그 자리에 담아 돌려준다 — 한 샤드가 실패해도 나머지는 살린다.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not tasks:
        return []
    n = min(len(tasks), max(1, limit or settings.max_parallel_llm))
    if n == 1:
        out = []
        for t in tasks:
            try:
                out.append(t())
            except Exception as exc:  # noqa: BLE001
                out.append(exc)
        return out
    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="llm") as ex:
        futs = [ex.submit(t) for t in tasks]
        out = []
        for f in futs:
            try:
                out.append(f.result())
            except Exception as exc:  # noqa: BLE001
                out.append(exc)
        return out


def tool_input(resp, name: str | None = None) -> dict | None:
    """응답에서 tool_use 블록의 input 을 꺼낸다(name 지정 시 그 도구만)."""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) != "tool_use":
            continue
        if name is None or getattr(block, "name", None) == name:
            return block.input
    return None


def text_of(resp) -> str:
    return " ".join(
        b.text for b in (getattr(resp, "content", []) or []) if getattr(b, "type", None) == "text"
    ).strip()
