"""구독 백엔드 — API 키 대신 이 PC 의 Claude 구독(OAuth)으로 호출한다.

`llm.call()` 의 세 번째 갈래다. anthropic/openai 는 HTTP 클라이언트를 하나 만들면 끝이지만
여기는 그렇지 않다. 이유 셋 — 전부 실측으로 확인한 것이다(260916, docs/plan/subscription-mode_260916.md).

① **영속 클라이언트를 공유하면 응답이 뒤바뀐다.**
   전용 루프 + 클라이언트 1개에 워커 4개를 물렸더니 2개는 영영 안 돌아오고, 나머지 둘은
   **서로의 답을 받았다**(워커3이 워커4의 질문에 대한 답). `receive_response()` 를 한
   클라이언트에서 동시에 돌리면 안 된다. 그래서 큐로 **배타 대여**한다 — 한 호출이
   한 클라이언트를 독점하고 반납할 때까지 아무도 그것을 못 본다.

② **문맥이 턴마다 쌓인다.** 같은 클라이언트로 이어 부르면 입력이 1187 → 1809 토큰으로
   오른다. 쿼터가 새는 것보다 나쁜 것은 **호출들이 서로를 본다**는 것이다 — 번역 호출이
   직전 리서치 답을 문맥으로 갖게 된다. 이 앱의 호출은 서로 독립이어야 한다.
   그래서 누적 입력이 recycle_tokens 를 넘으면 그 클라이언트를 버리고 새로 만든다.

③ **`total_cost_usd` 는 세션 누적값이다.** 0.0020 → 0.0040 → 0.0063 처럼 단조 증가한다.
   그대로 쓰면 원장이 중복 계상된다(특히 dialogue.py:443 은 재시도마다 **더하도록**
   일부러 고쳐진 자리다 — 누적을 또 누적한다). 그래서 클라이언트별 직전 값을 들고 **차분**을 낸다.

그리고 동기·비동기 경계가 있다. 이 앱은
    asyncio → server.py 전용 풀 → jobs.py:374 to_thread → 동기 잡 → llm.gather 스레드 → llm.call()
로 내려오므로 `call()` 은 **동기**여야 하는데 SDK 는 asyncio 다. `ClaudeSDKClient` 는
자기를 만든 루프에 묶이므로(같은 함정이 render_google.py:274 에 Playwright 로 적혀 있다)
전용 루프 스레드 하나를 두고 `run_coroutine_threadsafe` 로 넘긴다.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .config import Settings

# ── 시스템 프롬프트·도구를 반드시 좁힌다 ──────────────────────────
# 실측: --tools 기본값이면 캐시기록 19,215 토큰이 붙는다. 도구를 끄고 시스템 프롬프트를
# 우리 것으로 갈아치우면 입력 1,239 토큰이다. **15배 차이라 선택 사항이 아니다.**
#   --tools ""                 1,239
#   --tools WebSearch          1,739
#   --tools WebSearch,WebFetch 2,262
#   --tools default           19,215  (캐시기록)
_WEB_TOOLS = ["WebSearch"]

# ── 자식 프로세스에서 API 키를 걷어낸다 ──────────────────────────
# SDK 는 os.environ 을 **통째로** 물려준다(subprocess_cli.py: inherited_env). 그런데 이
# 프로젝트는 config.py 가 load_dotenv() 로 .env 의 ANTHROPIC_API_KEY 를 환경에 올린다.
# 그 상태로 claude 를 띄우면 CLI 가 경고를 찍고 **구독 대신 그 키를 쓴다**:
#     "ANTHROPIC_API_KEY ... is set and takes precedence over your claude.ai login"
# 구독 모드를 켜 놓고 API 요금을 내는 것 — 고치려던 바로 그 사고다(llm.py:59 와 같은 뿌리,
# 다른 문). options.env 는 상속분을 덮어쓰므로 여기서 빈 값으로 눌러 둔다.
# CLAUDE_CODE_OAUTH_TOKEN 은 **지우지 않는다** — 그건 `claude setup-token` 이 만든 구독 토큰이다.
_AUTH_OVERRIDE = {
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_AUTH_TOKEN": "",
    "ANTHROPIC_BEARER_TOKEN": "",
    # 3P 제공자로 새는 것도 막는다 — 구독 모드는 "내 claude.ai 로그인으로" 라는 뜻이다.
    "CLAUDE_CODE_USE_BEDROCK": "",
    "CLAUDE_CODE_USE_VERTEX": "",
}


class SubscriptionUnavailable(RuntimeError):
    """SDK 미설치 / claude 로그인 안 됨 — 호출부가 사용자 메시지로 바꿔 쓴다."""


@dataclass
class Response:
    """Anthropic 응답처럼 읽히는 얇은 껍데기.

    호출부(analyze·research·translate…)는 tool_input()·text_of()·spend() 셋만 쓴다.
    `_OpenAIResponse` 가 이미 같은 방식으로 붙어 있다(llm.py:361).
    """

    # 이 응답이 구독 갈래임을 알리는 표식. 클래스 이름으로 알아보면 검사용 대역이나
    # 하위 클래스가 조용히 API 갈래로 처리된다 — 값만 틀리고 예외는 안 난다.
    is_subscription = True

    structured: dict | None
    tool_name: str
    text: str
    usage: dict
    model: str | None
    cost_usd: float                     # **차분** — 세션 누적값이 아니다
    stop_reason: str | None = None
    # 검색이 실제로 돌려준 URL. 모델이 신고한 출처를 이것으로 교차 검증한다(환각 차단).
    harvested_urls: list[str] = field(default_factory=list)
    # 실제로 돈 WebSearch 횟수. 서버측 도구가 아니라 usage.server_tool_use 가 0 이므로
    # 스트림의 도구 호출을 직접 센다 — 출처 수로 대용하면 "검색 12건" 처럼 틀린 수가 나온다.
    web_searches: int = 0
    canceled: bool = False


# ── 전용 이벤트 루프 스레드 ───────────────────────────────────────
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        ready = threading.Event()

        def run() -> None:
            global _loop
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
            ready.set()
            _loop.run_forever()

        threading.Thread(target=run, daemon=True, name="ggh-subscription-loop").start()
        ready.wait(timeout=10)
        if _loop is None:
            raise SubscriptionUnavailable("구독 백엔드 이벤트 루프를 띄우지 못했습니다.")
        return _loop


def _submit(coro, timeout: float):
    return asyncio.run_coroutine_threadsafe(coro, _ensure_loop()).result(timeout=timeout)


# ── 클라이언트 풀 ─────────────────────────────────────────────────
class _Client:
    """클라이언트 하나 + 그 세션의 누적 상태. 대여 중에는 오직 한 호출만 본다."""

    def __init__(self, raw):
        self.raw = raw
        self.last_cost = 0.0        # 차분 계산용(③)
        self.used_tokens = 0        # 회수 판정용(②)
        self.calls = 0


class _Pool:
    def __init__(self, size: int, recycle_tokens: int):
        self.size = max(1, size)
        self.recycle_tokens = max(2000, recycle_tokens)
        self._q: queue.Queue = queue.Queue()
        self._made = 0
        self._lock = threading.Lock()

    def _new(self) -> _Client:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise SubscriptionUnavailable(
                "claude-agent-sdk 미설치 — `uv sync --extra subscription` 후 다시 시도하세요."
            ) from exc

        async def mk():
            c = ClaudeSDKClient(options=ClaudeAgentOptions(
                # 실제 옵션은 호출마다 바꿔야 하는 것이 많지만(system/schema/tools),
                # 그건 연결 후 바꿀 수 없다. 그래서 풀은 **옵션 조합별**로 나뉜다 — _key() 참조.
                **self.opts))
            await c.connect()
            return c

        return _Client(_submit(mk(), timeout=120))

    def acquire(self, timeout: float) -> _Client:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._made < self.size:
                self._made += 1
                try:
                    return self._new()
                except Exception:
                    self._made -= 1
                    raise
        # 풀이 다 나갔다 — 반납을 기다린다. 이게 max_parallel_llm 의 실질 상한이 된다.
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("구독 클라이언트를 얻지 못했습니다(모두 사용 중).") from exc

    def release(self, c: _Client, *, broken: bool = False) -> None:
        if broken or c.used_tokens >= self.recycle_tokens:
            self._drop(c)
            return
        self._q.put(c)

    def _drop(self, c: _Client) -> None:
        try:
            _submit(c.raw.disconnect(), timeout=30)
        except Exception:  # noqa: BLE001 — 회수 실패가 호출을 망치면 안 된다
            pass
        with self._lock:
            self._made = max(0, self._made - 1)

    def close(self) -> None:
        while True:
            try:
                self._drop(self._q.get_nowait())
            except queue.Empty:
                break


_pools: dict[tuple, _Pool] = {}
_pools_lock = threading.Lock()


def _pool_for(settings: Settings, opts: dict) -> _Pool:
    """옵션 조합별 풀. 연결 뒤에는 system_prompt·schema·tools 를 바꿀 수 없기 때문이다."""
    key = (opts.get("model"), opts.get("system_prompt"), tuple(opts.get("tools") or []),
           _schema_key(opts.get("output_format")), opts.get("effort"))
    with _pools_lock:
        p = _pools.get(key)
        if p is None:
            if len(_pools) >= 8:            # 조합이 무한히 늘지 않게 — 오래된 것부터 버린다
                _, old = _pools.popitem()
                old.close()
            p = _Pool(settings.subscription_pool, settings.subscription_recycle_tokens)
            p.opts = opts                    # noqa: B010 — _new() 가 쓴다
            _pools[key] = p
        return p


def _schema_key(of: dict | None) -> str:
    if not of:
        return ""
    import json

    return json.dumps(of, sort_keys=True)[:400]


def close_all() -> None:
    with _pools_lock:
        for p in _pools.values():
            p.close()
        _pools.clear()


# ── URL 수확 — 교차 검증의 기준이 된다 ────────────────────────────
import re as _re

_URL_RE = _re.compile(r"https?://[A-Za-z0-9._~:/?#@!$&*+,;=%()\[\]-]+")


def norm_url(u: str) -> str:
    """교차 검증용 정규화. 호스트+경로만 본다 — 추적 파라미터로 같은 글이 달라 보이면 안 된다."""
    try:
        p = urlsplit((u or "").strip())
    except ValueError:
        return ""
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").rstrip("/")
    return f"{host}{path}" if host else ""


def _harvest(block) -> list[str]:
    """도구 결과 블록에서 검색기가 실제로 돌려준 URL 을 긁는다."""
    import json

    out: list[str] = []
    for holder in (getattr(block, "content", None), getattr(block, "input", None)):
        if holder is None:
            continue
        try:
            s = holder if isinstance(holder, str) else json.dumps(holder, default=str)
        except (TypeError, ValueError):
            continue
        out.extend(_URL_RE.findall(s)[:80])
    return out


# ── 호출 ──────────────────────────────────────────────────────────
def call(
    settings: Settings,
    *,
    system: str,
    messages: list[dict],
    tools: list[dict] | None,
    tool_choice: dict | None,
    max_tokens: int,
    effort: str | None,
    deadline_s: float | None,
    should_stop,
    model: str,
) -> Response:
    """llm.call() 의 구독 갈래. 반환은 Response(위 껍데기)."""
    schema, tool_name, wants_web = _translate_tools(tools, tool_choice)

    opts: dict[str, Any] = {
        "model": model,
        # 시스템 프롬프트를 반드시 우리 것으로 — 안 하면 캐시기록 7,377 토큰이 붙는다.
        "system_prompt": system or "You are a precise assistant.",
        "tools": list(_WEB_TOOLS) if wants_web else [],
        "strict_mcp_config": True,
        "permission_mode": "bypassPermissions",
        "setting_sources": [],       # 이 저장소의 CLAUDE.md·훅이 딸려 들어오면 안 된다
        "env": dict(_AUTH_OVERRIDE),  # API 키가 구독을 가로채지 못하게(위 주석)
        "cwd": str(settings.captures_dir.parent),
    }
    if effort:
        opts["effort"] = effort
    if schema:
        opts["output_format"] = {"type": "json_schema", "schema": schema}

    pool = _pool_for(settings, opts)
    budget = deadline_s or settings.llm_timeout_s
    c = pool.acquire(timeout=max(30.0, budget))
    broken = False
    stopper = None
    try:
        prompt = _to_prompt(messages)

        if should_stop is not None:
            # 취소. 실측: interrupt() 는 0.02초에 진행 중인 호출을 푼다. 지금 경로(llm.py:337)는
            # 스트림 이벤트가 올 때만 should_stop() 을 볼 수 있어 조용한 구간에서는 못 끊었다.
            stopper = _Stopper(c, should_stop)
            stopper.start()

        res, harvested, searches = _submit(_run(c.raw, prompt), timeout=budget + 30)

        usage = dict(res.usage or {})
        total = float(res.total_cost_usd or 0.0)
        delta = max(0.0, total - c.last_cost)      # ③ 차분
        c.last_cost = total
        c.calls += 1
        c.used_tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)

        canceled = (getattr(res, "terminal_reason", None) == "aborted_streaming"
                    or (stopper is not None and stopper.fired))
        return Response(
            structured=res.structured_output if isinstance(res.structured_output, dict) else None,
            tool_name=tool_name,
            text=(res.result or "") if not schema else "",
            usage=usage,
            model=_model_of(res) or model,
            cost_usd=delta,
            stop_reason=getattr(res, "stop_reason", None),
            harvested_urls=harvested,
            web_searches=searches,
            canceled=canceled,
        )
    except BaseException:
        broken = True
        raise
    finally:
        if stopper is not None:
            stopper.stop()
        pool.release(c, broken=broken)


class _Stopper(threading.Thread):
    """should_stop() 을 폴링하다가 걸리면 interrupt() 를 친다."""

    def __init__(self, c: _Client, should_stop):
        super().__init__(daemon=True, name="ggh-sub-stopper")
        self.c, self.should_stop = c, should_stop
        self.fired = False
        self._done = threading.Event()

    def run(self) -> None:
        while not self._done.wait(0.5):
            try:
                if self.should_stop():
                    self.fired = True
                    _submit(self.c.raw.interrupt(), timeout=20)
                    return
            except Exception:  # noqa: BLE001 — 취소 시도 실패가 호출을 죽이면 안 된다
                return

    def stop(self) -> None:
        self._done.set()


async def _run(raw, prompt):
    """한 번의 질문 → (ResultMessage, 수확한 URL). 대여 중이므로 동시 진입이 없다."""
    from claude_agent_sdk import ResultMessage  # type: ignore

    await raw.query(prompt)
    urls: list[str] = []
    seen: set[str] = set()
    searches = 0
    res = None
    async for m in raw.receive_response():
        for b in (getattr(m, "content", None) or []):
            kind = type(b).__name__
            if kind not in ("ToolResultBlock", "ToolUseBlock"):
                continue
            if kind == "ToolUseBlock" and str(getattr(b, "name", "")).lower() in (
                    "websearch", "webfetch"):
                searches += 1
            for u in _harvest(b):
                n = norm_url(u)
                if n and n not in seen:
                    seen.add(n)
                    urls.append(u)
        if isinstance(m, ResultMessage):
            res = m
    if res is None:
        raise SubscriptionUnavailable("구독 호출이 결과 없이 끝났습니다.")
    return res, urls, searches


def _model_of(res) -> str | None:
    mu = getattr(res, "model_usage", None) or {}
    for name in mu:
        return name
    return None


def _translate_tools(tools, tool_choice) -> tuple[dict | None, str, bool]:
    """Anthropic 도구 선언 → (출력 스키마, 도구 이름, 웹검색 필요 여부).

    이 앱의 호출 20곳 중 19곳이 '강제 tool_choice 로 구조화 출력을 받는다' 한 가지 모양이라
    `output_format` 의 json_schema 로 그대로 옮겨진다. 옮겨지지 않는 것은 **커스텀 도구가
    둘 이상인 호출**(molecule.py:756)뿐이고, 그건 첫 번째 것을 출력 스키마로 쓴다.
    """
    wants_web = False
    custom: list[dict] = []
    for t in tools or []:
        if str(t.get("type") or "").startswith("web_search"):
            wants_web = True
        else:
            custom.append(t)
    if not custom:
        return None, "", wants_web
    want = (tool_choice or {}).get("name")
    pick = next((t for t in custom if t.get("name") == want), custom[0])
    return pick.get("input_schema"), pick.get("name", ""), wants_web


def _to_prompt(messages: list[dict]):
    """Anthropic 메시지 → SDK prompt. 이미지 블록은 **그대로** 간다(실측 확인).

    문자열 하나로 끝나면 문자열을 주고(빠르다), 이미지가 섞이면 stream-json 메시지로 준다.
    """
    if len(messages) == 1 and isinstance(messages[0].get("content"), str):
        return messages[0]["content"]

    payload = [{"type": "user", "message": {"role": m.get("role", "user"),
                                            "content": m.get("content")},
                "parent_tool_use_id": None, "session_id": "default"}
               for m in messages]

    async def gen():
        for p in payload:
            yield p

    return gen()


# ── 쿼터 ──────────────────────────────────────────────────────────
_rate: dict = {}
_rate_at: float = 0.0
_rate_lock = threading.Lock()


def rate_limit(settings: Settings, *, max_age_s: float = 120.0) -> dict:
    """5시간·7일 창의 사용률. 보고서 한 건이 15~20 호출이고 이 창은 **사용자가 코딩에 쓰는
    것과 같은 통**이다. 게다가 넘치면 넘어갈 밸브가 없다(overageStatus=rejected 실측).
    그래서 잡을 시작하기 **전에** 본다."""
    global _rate, _rate_at
    with _rate_lock:
        if _rate and (time.monotonic() - _rate_at) < max_age_s:
            return dict(_rate)
    try:
        info = _submit(_probe_rate(settings), timeout=90)
    except Exception as exc:  # noqa: BLE001 — 계기 고장이 잡을 막으면 안 된다
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    with _rate_lock:
        _rate, _rate_at = info, time.monotonic()
    return dict(info)


async def _probe_rate(settings: Settings) -> dict:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # type: ignore

    out: dict = {"ok": True}
    opts = ClaudeAgentOptions(model=settings.model_draft, system_prompt="Reply with: ok",
                              tools=[], strict_mcp_config=True, setting_sources=[],
                              permission_mode="bypassPermissions",
                              env=dict(_AUTH_OVERRIDE),
                              cwd=str(settings.captures_dir.parent))
    out["sawRateLimit"] = False
    async with ClaudeSDKClient(options=opts) as c:
        await c.query("ok")
        async for m in c.receive_response():
            info = getattr(m, "rate_limit_info", None)
            if info is None:
                continue
            # 쿼터 이벤트는 **구독 사용자에게만** 온다 — API 키로 붙었으면 안 온다.
            # 그래서 이 플래그가 "정말 구독으로 돌고 있는가" 의 증거가 된다.
            out["sawRateLimit"] = True
            raw = getattr(info, "raw", None) or {}
            w = raw.get("unifiedWindows") or {}
            for key in ("five_hour", "seven_day"):
                if isinstance(w.get(key), dict):
                    out[key] = {"utilization": w[key].get("utilization"),
                                "resetsAt": w[key].get("resetsAt")}
            out["status"] = getattr(info, "status", None)
            out["overageStatus"] = raw.get("overageStatus")
    return out


def available(settings: Settings) -> tuple[bool, str]:
    """구독 백엔드를 실제로 쓸 수 있는가(설치·로그인). 저장 전 확인용."""
    try:
        import claude_agent_sdk  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        return False, "claude-agent-sdk 가 설치되지 않았습니다 — `uv sync --extra subscription`."
    info = rate_limit(settings, max_age_s=0.0)
    if not info.get("ok"):
        return False, (f"이 PC 의 claude 로그인을 확인하지 못했습니다 — 터미널에서 "
                       f"`claude auth status` 를 확인하세요. ({info.get('error', '')})")
    return True, ""
