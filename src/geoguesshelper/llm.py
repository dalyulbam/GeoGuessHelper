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

import contextvars
import threading
from dataclasses import dataclass
from typing import Any

from .config import Settings

# ── 누구의 키로 부르는가 ─────────────────────────────────────────
# 다중 사용자 서버에서는 호출마다 주인이 다르다. 그런데 call() 을 부르는 곳이
# analyze·research·translate·script·knowledge·correction·dialogue 로 흩어져 있어
# 시그니처에 자격증명을 끼워 넣으면 전부 고쳐야 하고, 한 곳만 빠뜨리면 **남의 키로
# 남의 돈을 쓰는** 사고가 난다. 그래서 컨텍스트 변수로 흘린다 — asyncio 태스크와
# asyncio.to_thread 로 그대로 전파되므로 잡 파이프라인 전체가 한 번에 덮인다.


@dataclass(frozen=True)
class Creds:
    """이 호출에 쓸 제공자와 키. provider 는 "anthropic" | "openai" | "subscription"."""
    provider: str
    api_key: str
    label: str = ""          # 진단용(마스킹된 표시). 평문은 담지 않는다.

    @property
    def usable(self) -> bool:
        """이 자격증명으로 실제로 부를 수 있는가.

        구독은 **키가 없는 것이 요점**이다. 예전에는 판정이 `c.api_key` 하나였고,
        그래서 구독 Creds 가 falsy 로 걸려 **서버의 API 키로 조용히 떨어졌다** —
        사용자는 구독으로 도는 줄 알고 요금을 냈다. 판정을 여기 한 곳에 모은다.
        """
        return self.provider == "subscription" or bool(self.api_key)


_creds: contextvars.ContextVar[Creds | None] = contextvars.ContextVar("ggh_creds", default=None)


def set_credentials(c: Creds | None):
    """이 컨텍스트의 자격증명을 바꾼다. 반환 토큰을 reset_credentials 에 넘겨 되돌린다."""
    return _creds.set(c)


def reset_credentials(token) -> None:
    try:
        _creds.reset(token)
    except ValueError:      # 다른 컨텍스트에서 만든 토큰 — 무시해도 안전하다
        pass


def current_credentials_or_none() -> Creds | None:
    """지금 컨텍스트의 자격증명(없으면 None). 예외를 던지지 않는다 — 호출부가 판단한다."""
    return _creds.get()


def current_credentials(settings: Settings) -> Creds:
    """지금 쓸 자격증명. 컨텍스트에 없으면 서버 설정(단독 소유자 모드)으로 물러선다."""
    c = _creds.get()
    if c is not None and c.usable:
        return c
    if getattr(settings, "llm_backend", "api") == "subscription":
        return Creds("subscription", "", "이 PC 의 Claude 구독")
    if settings.anthropic_api_key:
        return Creds("anthropic", settings.anthropic_api_key, "server")
    raise LLMUnavailable(
        "이 요청에 쓸 API 키가 없습니다. 화면 오른쪽 위 '내 키'에서 Claude 또는 "
        "ChatGPT 키를 넣어 주세요."
    )

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


class _UsageView:
    """dict usage 를 속성으로 읽히게 하는 어댑터(중첩 dict 도 같은 방식으로 감싼다).

    cost_of()·cache_stats()·research.py 가 전부 `getattr(usage, ...)` 로 읽기 때문에
    호출부를 고치는 대신 모양을 맞춘다.
    """

    __slots__ = ("_d",)

    def __init__(self, d: dict):
        self._d = d

    def __getattr__(self, name: str):
        v = self._d.get(name)
        return _UsageView(v) if isinstance(v, dict) else v


class LLMUnavailable(RuntimeError):
    """anthropic 미설치 / 키 없음 — 호출부가 사용자 메시지로 바꿔 쓴다."""


class LLMTimeout(TimeoutError):
    """호출이 벽시계 상한을 넘었다 — 스트림을 끊고 호출부가 soft-fail 로 처리한다."""


class LLMCanceled(RuntimeError):
    """사용자가 작업을 취소했다 — 진행 중인 스트림을 즉시 끊는다."""


_clients: dict[tuple, Any] = {}


def get_client(settings: Settings, creds: "Creds | None" = None):
    """이 호출의 제공자 클라이언트. (제공자·키·TLS 모드)가 같으면 재사용한다.

    예전에는 전역 1개였다. 다중 사용자에서는 회원마다 키가 다르므로 키별로 캐시한다 —
    다만 키 자체를 사전 키로 쓰지 않는다(메모리 덤프에 평문이 남는다). 해시를 쓴다.
    """
    import hashlib

    import httpx

    from .tls import httpx_verify

    c = creds or current_credentials(settings)
    verify = httpx_verify()
    ck = (c.provider, hashlib.sha256(c.api_key.encode("utf-8")).hexdigest(), verify is False)
    with _client_lock:
        got = _clients.get(ck)
        if got is not None:
            return got
        http = httpx.Client(
            verify=verify,
            timeout=httpx.Timeout(settings.llm_timeout_s, connect=15.0),
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )
        if c.provider == "openai":
            try:
                import openai  # type: ignore
            except ModuleNotFoundError as exc:  # pragma: no cover
                raise LLMUnavailable(
                    "openai 미설치 — `uv sync --extra server` 후 다시 시도하세요."
                ) from exc
            got = openai.OpenAI(api_key=c.api_key, http_client=http, max_retries=2)
        else:
            try:
                import anthropic  # type: ignore
            except ModuleNotFoundError as exc:  # pragma: no cover
                raise LLMUnavailable(
                    "anthropic 미설치 — `uv sync --extra analyze` 후 다시 시도하세요."
                ) from exc
            got = anthropic.Anthropic(api_key=c.api_key, http_client=http, max_retries=2)
        # 무한히 쌓이지 않게 — 회원이 많아지면 오래된 것부터 버린다.
        if len(_clients) >= 32:
            old = next(iter(_clients))
            try:
                _clients.pop(old).close()
            except Exception:  # noqa: BLE001
                pass
        _clients[ck] = got
    return got


def verify_key(settings: Settings, provider: str, api_key: str) -> tuple[bool, str]:
    """키가 실제로 동작하는지 **한 번** 확인한다(값싼 호출).

    저장 전에 확인하는 이유 — 오타 난 키를 받아 두면 그 뒤 모든 보고서가 실패하고,
    사용자는 왜인지 모른다. 여기서 걸러 그 자리에서 말한다.
    """
    c = Creds(provider, api_key)
    try:
        client = get_client(settings, c)
        if provider == "openai":
            client.models.list()
        else:
            client.messages.create(
                model=settings.model_draft, max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
        return True, ""
    except Exception as exc:  # noqa: BLE001 — 사유를 그대로 사용자에게 보여 준다
        msg = str(exc)
        if "authentication" in msg.lower() or "401" in msg or "invalid_api_key" in msg:
            return False, "키가 거부됐습니다(인증 실패). 값을 다시 확인해 주세요."
        return False, f"키 확인에 실패했습니다: {type(exc).__name__}: {msg[:160]}"


def close_client() -> None:
    global _client, _client_key
    try:
        from . import subscription

        subscription.close_all()
    except Exception:  # noqa: BLE001 — 종료 정리 실패가 종료를 막으면 안 된다
        pass
    with _client_lock:
        for c in list(_clients.values()):
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        _clients.clear()
        _client = None
        _client_key = None


def cost_of(usage, model: str | None = None) -> float:
    """usage → USD. 캐시 write/read 와 web_search 요청 수까지 반영.

    model 을 주면 그 모델 단가로 계산한다 — 라우팅 후에는 호출마다 단가가 다르다.
    """
    if usage is None:
        return 0.0
    # 구독(claude-agent-sdk)의 usage 는 **dict** 다. 아래 getattr 들이 전부 빗나가
    # 조용히 0.0 을 돌려주고 ReportRow.cost_usd 원장이 0 으로 채워졌다. 먼저 정규화한다.
    if isinstance(usage, dict):
        usage = _UsageView(usage)
    # OpenAI usage 는 필드 이름이 다르다(prompt_tokens/completion_tokens).
    if getattr(usage, "prompt_tokens", None) is not None:
        pin, pout = _OPENAI_PRICE.get(model or "", _OPENAI_PRICE["gpt-4o"])
        return ((getattr(usage, "prompt_tokens", 0) or 0) / 1_000_000 * pin
                + (getattr(usage, "completion_tokens", 0) or 0) / 1_000_000 * pout)
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
    creds = current_credentials(settings)
    if creds.provider == "openai":
        return _call_openai(settings, system=system, messages=messages, tools=tools,
                            tool_choice=tool_choice, max_tokens=max_tokens, role=role,
                            model=model, deadline_s=deadline_s, should_stop=should_stop,
                            creds=creds)
    if creds.provider == "subscription":
        return _call_subscription(settings, system=system, messages=messages, tools=tools,
                                  tool_choice=tool_choice, max_tokens=max_tokens, role=role,
                                  model=model, effort=effort, deadline_s=deadline_s,
                                  should_stop=should_stop)
    client = get_client(settings, creds)
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


# ── 구독 경로 ────────────────────────────────────────────────────
def _call_subscription(settings: Settings, *, system, messages, tools, tool_choice,
                       max_tokens, role, model, effort, deadline_s, should_stop):
    """이 PC 의 Claude 구독으로 부른다. 자세한 사정은 subscription.py 머리말 참조.

    max_tokens 는 **넘길 자리가 없다**(ClaudeAgentOptions 에 없다). 상한이 사라지는 쪽이라
    잘림 위험은 오히려 줄지만 폭주 제동도 같이 사라진다는 것을 알고 쓴다.
    """
    from . import subscription

    use_model = model or model_for(settings, role)
    try:
        r = subscription.call(
            settings, system=system, messages=messages, tools=tools,
            tool_choice=tool_choice, max_tokens=max_tokens, effort=effort,
            deadline_s=deadline_s, should_stop=should_stop, model=use_model,
        )
    except subscription.SubscriptionUnavailable as exc:
        raise LLMUnavailable(str(exc)) from exc
    except TimeoutError as exc:
        raise LLMTimeout(f"{use_model} 구독 호출이 상한을 넘겼습니다: {exc}") from exc
    # 취소는 결과가 is_error 로 돌아온다 — 그대로 두면 사용자가 누른 취소가 고장으로 보인다.
    if r.canceled:
        raise LLMCanceled("호출이 취소되었습니다.")
    return r


# ── OpenAI 경로 ──────────────────────────────────────────────────
# 이 앱의 호출은 대부분 "강제 도구 호출로 구조화 출력을 받는다"는 한 가지 모양이다.
# 그 모양은 OpenAI 의 chat.completions + tools + tool_choice 로 그대로 옮겨진다.
# 옮겨지지 **않는** 것 하나: Anthropic 의 서버측 web_search 도구다. OpenAI 쪽에 같은
# 것이 없으므로 리서치는 검색 없이 돌고, research.py 가 그 사실을 보고서에 적는다.
_OPENAI_MODELS = {
    "vision": "gpt-4o", "fact": "gpt-4o-mini", "reason": "gpt-4o",
    "draft": "gpt-4o-mini", "verify": "gpt-4o",
}
# gpt-4o 기준 단가 ($/MTok) — 응답의 usage 로 계산한다.
_OPENAI_PRICE = {"gpt-4o": (2.5, 10.0), "gpt-4o-mini": (0.15, 0.6)}


class _OpenAIResponse:
    """Anthropic 응답처럼 읽히는 얇은 껍데기.

    호출부(analyze·research·translate…)는 `tool_input(resp)`·`text_of(resp)`·`spend(resp)`
    만 쓴다. 그 셋이 같은 모양이면 호출부를 한 줄도 고치지 않아도 된다.
    """

    def __init__(self, raw, model: str):
        self.raw = raw
        self.model = model
        self.usage = getattr(raw, "usage", None)

    @property
    def _msg(self):
        return self.raw.choices[0].message if self.raw.choices else None


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Anthropic 메시지 → OpenAI 메시지. 이미지 블록도 옮긴다."""
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": m.get("role", "user"), "content": content})
            continue
        parts: list[dict] = []
        for b in content or []:
            t = b.get("type")
            if t == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif t == "image":
                src = b.get("source") or {}
                if src.get("type") == "base64":
                    url = f"data:{src.get('media_type', 'image/jpeg')};base64,{src.get('data', '')}"
                    parts.append({"type": "image_url", "image_url": {"url": url}})
        out.append({"role": m.get("role", "user"), "content": parts or ""})
    return out


def _call_openai(settings: Settings, *, system, messages, tools, tool_choice,
                 max_tokens, role, model, deadline_s, should_stop, creds):
    import time as _time

    client = get_client(settings, creds)
    use_model = model if (model and model.startswith("gpt")) else _OPENAI_MODELS.get(
        role or "", "gpt-4o")
    kwargs: dict[str, Any] = {
        "model": use_model,
        "max_tokens": max_tokens,
        "messages": _to_openai_messages(system, messages),
    }
    if tools:
        # Anthropic tool → OpenAI function tool. input_schema 이름만 다르다.
        kwargs["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }} for t in tools if t.get("type") is None or t.get("type") == "custom"]
        if tool_choice and tool_choice.get("type") == "tool" and tool_choice.get("name"):
            kwargs["tool_choice"] = {"type": "function",
                                     "function": {"name": tool_choice["name"]}}
        elif kwargs["tools"]:
            kwargs["tool_choice"] = "auto"
        if not kwargs["tools"]:
            kwargs.pop("tools", None)
            kwargs.pop("tool_choice", None)
    if should_stop is not None and should_stop():
        raise LLMCanceled("호출이 취소되었습니다.")
    t0 = _time.monotonic()
    raw = client.chat.completions.create(**kwargs)
    # 스트리밍을 쓰지 않으므로 마감은 사후 확인이다 — httpx 타임아웃이 1차 방어선이고,
    # 여기서는 넘긴 사실을 호출부에 알려 다음 단계를 건너뛰게 한다.
    if deadline_s and (_time.monotonic() - t0) > deadline_s:
        raise LLMTimeout(f"{use_model} 호출이 {deadline_s:.0f}초를 넘겼습니다.")
    return _OpenAIResponse(raw, use_model)


def used_model(resp) -> str | None:
    """응답이 실제로 어떤 모델로 처리됐는지(라우팅 후 비용 계산에 필요)."""
    return getattr(resp, "model", None)


def _is_subscription(resp) -> bool:
    return bool(getattr(resp, "is_subscription", False))


def spend(resp) -> float:
    """응답 → USD. 실제 사용 모델 단가를 자동 적용.

    구독은 **청구되지 않는다** — 여기서 나오는 값은 "API 였다면 얼마"(정가 환산)다.
    그리고 SDK 의 total_cost_usd 는 세션 누적값이라 subscription.py 가 이미 차분을 냈다.
    합산하는 호출부(dialogue.py:443)가 있으므로 그 차분을 그대로 돌려줘야 한다.
    """
    if _is_subscription(resp):
        return float(getattr(resp, "cost_usd", 0.0) or 0.0)
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
    """응답에서 tool_use 블록의 input 을 꺼낸다(name 지정 시 그 도구만).

    OpenAI 응답은 모양이 다르다(choices[0].message.tool_calls[].function.arguments 가
    **문자열 JSON**). 호출부를 고치지 않으려고 여기서 흡수한다.
    """
    if _is_subscription(resp):
        # 구조화 출력은 스키마 검증까지 끝난 dict 로 온다. 이름이 다르면 준 적 없는 것이다.
        if name is not None and getattr(resp, "tool_name", "") != name:
            return None
        s = getattr(resp, "structured", None)
        return s if isinstance(s, dict) else None
    if isinstance(resp, _OpenAIResponse):
        import json as _json

        msg = resp._msg
        for tc in (getattr(msg, "tool_calls", None) or []):
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            if name is None or getattr(fn, "name", None) == name:
                try:
                    return _json.loads(fn.arguments or "{}")
                except ValueError:
                    return None
        return None
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) != "tool_use":
            continue
        if name is None or getattr(block, "name", None) == name:
            return block.input
    return None


def text_of(resp) -> str:
    if _is_subscription(resp):
        return (getattr(resp, "text", "") or "").strip()
    if isinstance(resp, _OpenAIResponse):
        msg = resp._msg
        return (getattr(msg, "content", None) or "").strip()
    return " ".join(
        b.text for b in (getattr(resp, "content", []) or []) if getattr(b, "type", None) == "text"
    ).strip()
