# 구독 모드 — API 키 대신 Claude 구독으로 돌리기 (계획서 v2)

작성 260916 · 대상 `llm.py` / `config.py` / `webapi.py` / `webui`
상태 **1~5단계 구현 완료 (260916)** — 6단계(캐시 회복·분자)는 미착수, codex 반박 미수령

> **구현 후 추가로 드러난 것** — 계획에 없던 결함 둘을 구현 중에 잡았다.
>
> **(가)** `.env` 의 `ANTHROPIC_API_KEY` 가 자식 `claude` 프로세스로 상속되면 CLI 가
> **구독보다 그 키를 우선한다**(`"ANTHROPIC_API_KEY ... takes precedence over your
> claude.ai login"`). 구독 모드를 켜 놓고 API 요금을 내게 된다 — §2-1 과 같은 뿌리의
> 사고가 다른 문으로 들어온 것이다. `options.env` 로 자식에서 걷어내고,
> **쿼터 이벤트가 오는지로 실제 구독인지 확인**한다(그 이벤트는 구독 사용자에게만 온다).
>
> **(나)** `_reset_label` 을 `main()` 안에 두고 `build_app()` 의 게이트에서 불렀다.
> 그 `NameError` 는 **쿼터가 실제로 찼을 때만** 터진다 — 가장 늦게 발견되는 종류라
> 모듈 수준으로 올렸다. pyflakes 검사에 걸리는 모양이다.

---

## 0. 한 줄 답

**된다. 단, v1 초안의 핵심 설계는 틀렸고 코드에는 블로커가 셋 있다.**

관문 셋(구조화 출력·비전·웹검색)은 전부 실측으로 통과했다. 그러나
v1 이 제안한 "영속 클라이언트 하나를 공유한다" 는 **실제로 돌려 보니 응답이 스레드
사이에서 뒤바뀌었다**(§1-6). 그리고 기존 코드에 구독 모드를 조용히 망가뜨리는
자리가 셋 있다(§2). 그 넷을 고치는 것이 이 계획서다.

---

## 1. 실측 (260916, 이 PC) — 추측 없음

### 1-1. 인증은 이미 구독이다

```
$ claude auth status
  loggedIn: true · authMethod: "claude.ai" · apiProvider: "firstParty" · subscriptionType: "max"
```

`~/.claude/.credentials.json` 의 `claudeAiOauth.scopes` 에 **`user:inference`** 가 있다.
셸 환경에 `ANTHROPIC_API_KEY` 는 **없다** — 아래 실측은 전부 구독으로 돈 것이다.

### 1-2. 구조화 출력이 된다 — 이게 관문이었다

LLM 호출 20곳 중 19곳이 `tool_choice={"type":"tool", ...}` 강제 도구 호출이다.
이게 안 되면 계획 자체가 성립하지 않는다. 된다:

```
--json-schema '{"type":"object", ...}'
→ {"stop_reason":"tool_use", "structured_output":{"country":"Jordan","iso2":"JO"}}
```

스키마 **검증까지** 하고 파싱된 객체로 온다. `llm.tool_input(resp, name)` 자리다.

### 1-3. 비전이 된다 — base64 블록 그대로

`captures/blind/capture_0275f28547dd.jpg`(306KB) 를 지금 코드가 만드는 것과 **같은 모양**의
`{"type":"image","source":{"type":"base64",...}}` 로 넣어 `{"continent":"Asia","country":"Kazakhstan"}`
를 받았다. `analyze.py:315` · `correction.py:659` · `dialogue.py:336` 의 블록을 그대로 흘릴 수 있다.

주의: `--input-format stream-json` → `--output-format stream-json` → `--verbose` 가 **연쇄 강제**다.

### 1-4. 도구를 끄지 않으면 15배를 낸다

| 도구 설정 | 입력 토큰 | 캐시기록 |
|---|---|---|
| `--tools ""` | 1,239 | 0 |
| `--tools ""` + `--exclude-dynamic-…` | 1,239 | 0 (효과 없음) |
| `--tools WebSearch` | 1,739 | 0 |
| `--tools WebSearch,WebFetch` | 2,262 | 0 |
| **`--tools default`(기본값)** | 20 | **19,215** |

그리고 시스템 프롬프트를 `--system-prompt` 로 **반드시 갈아치운다** — 안 하면
`--tools ""` 여도 캐시기록 7,377 토큰이 붙는다. 교체하면 0 이 된다.

### 1-5. 웹검색 + 구조화 출력이 **동시에** 된다 — 단 느리고 계수기가 죽는다

```
--tools WebSearch + --json-schema
→ structured_output 정상.  num_turns=7 · 52.7초 · in=7,596 · 정가환산 $0.139
   server_tool_use.web_search_requests = 0    ← 서버측 도구가 아니므로 0 이다
```

무검색 호출이 ~4초인데 검색 1건에 **52.7초**다. 턴이 클라이언트에서 도는 대가다.

### 1-6. v1 §2-1 이 틀렸다 — 영속 클라이언트 공유는 **응답을 뒤섞는다**

전용 이벤트루프 스레드 + 영속 `ClaudeSDKClient` 1개에 워커 스레드 4개가
`run_coroutine_threadsafe` 로 동시에 물었다. 결과:

```
워커1  120.01s  TimeoutError          ← 영영 안 돌아옴
워커2  120.01s  TimeoutError          ← 영영 안 돌아옴
워커3    9.00s  {"country":"New Zealand"}   ← 워커4의 질문에 대한 답
워커4    3.99s  {"country":"Jordan"}        ← 워커1의 질문에 대한 답
```

**성능 문제가 아니라 정합성 사고다.** `receive_response()` 를 한 클라이언트에서
동시에 돌리면 응답이 엉뚱한 호출자에게 간다. 실제 잡이라면 번역 호출이 리서치
호출의 답을 받는다. `interrupt()` 도 그 상태를 풀지 못했다.

### 1-7. 수정안 — 배타 대여 풀. 검증 통과

클라이언트 N개를 큐에 넣고 **한 호출이 한 클라이언트를 배타 점유**한다.
워커 6개 → 풀 2개:

```
풀 2개 기동 10.45s
  1  OK   3.88s Jordan       cost=0.001982 in=1187
  2  OK   7.82s Iceland      cost=0.002058 in=1188
  3  OK   7.26s Nigeria      cost=0.004043 in=1411
  4  OK  10.55s New Zealand  cost=0.006326 in=1608
  5  OK  11.64s Jordan       cost=0.004385 in=1427
  6  OK  14.05s Jordan       cost=0.008825 in=1809
  총 14.05s · 정답 6/6
```

여기서 두 가지가 더 보인다(§2-2, §2-3 의 근거):
**`in` 이 1187 → 1809 로 계속 오른다**(문맥 누적), **`cost` 가 클라이언트별 누적값이다.**

### 1-8. 프로세스 값 — 헤드리스 클라이언트 1개 ≈ 210 MB

사용자의 대화형 세션을 제외하고 헤드리스만 센 값이다.

```
connect 직후   207 MB
호출 5회 후    233 MB     (누적 in=2222, cost=$0.013425)
disconnect 후  프로세스 회수됨 — 누수 없음
```

풀 2개 ≈ 470MB, 3개 ≈ 700MB, 6개 ≈ 1.4GB. (현재 여유 12GB / 31.7GB.
260915 에 2.5GB 까지 떨어져 서버가 저메모리 감시에 죽은 적이 있으므로 상한은 둔다.)

### 1-10. 취소는 오히려 좋아진다 — `interrupt()` 검증 통과

깨끗한 클라이언트에 3,000단어 에세이를 시키고 8초 뒤 다른 스레드에서 `interrupt()`:

```
[ 8.00s] interrupt() 호출
[ 8.00s] interrupt() 반환 (0.00s)
[ 8.02s] 호출이 풀렸다 — terminal_reason=aborted_streaming · is_error=True · out=0
         같은 클라이언트 재사용 OK
```

**0.02초에 풀린다.** 지금 경로(llm.py:337-344)는 스트림 이벤트가 올 때만
`should_stop()` 을 볼 수 있어 조용한 구간에서는 못 끊는다. 구독 경로가 더 낫다.

**단서**: 결과가 `is_error=True` 로 온다. `call()` 이 `terminal_reason == "aborted_streaming"`
을 **`LLMCanceled` 로 번역**해야 한다. 안 하면 사용자가 누른 취소가 고장으로 보인다.

### 1-9. 쿼터를 읽을 수 있다 — 그리고 지금 62% 다

```json
"unifiedWindows": { "five_hour": {"utilization":0.62, "resetsAt":1789496400},
                    "seven_day": {"utilization":0.13, "resetsAt":1790085600} },
"overageStatus":"rejected", "overageDisabledReason":"org_level_disabled"
```

5시간 창이 차면 **넘어갈 밸브가 없다.** 그리고 그 창은 사용자가 코딩에 쓰는 것과 같은 통이다.

---

## 2. 기존 코드의 블로커 셋 — 계획서가 아니라 **코드가** 막는다

### 2-1. ⚠ BLOCKER — 토글을 켜면 조용히 API 키로 돈다

`llm.py:59-69`

```python
def current_credentials(settings: Settings) -> Creds:
    c = _creds.get()
    if c is not None and c.api_key:        # Creds("subscription", "") → "" 는 falsy
        return c
    if settings.anthropic_api_key:         # ← 여기로 떨어진다
        return Creds("anthropic", settings.anthropic_api_key, "server")
```

키가 없는 것이 구독 모드의 요점인데, 바로 그 이유로 폴백에 걸린다.
사용자는 구독으로 도는 줄 알고 **API 키 요금을 낸다.**

**수정**: 판정 기준을 `c.api_key` 에서 `c.provider == "subscription" or c.api_key` 로 바꾼다.
이 함수는 "남의 키로 남의 돈을 쓰는 사고" 를 막으려고 만든 것이므로(llm.py:26-28 주석),
분기를 늘리지 말고 `Creds.usable` 같은 속성 하나로 뜻을 모은다.

### 2-2. ⚠ BLOCKER — 비용이 조용히 0 이 된다

`llm.py:231-238` 은 usage 를 **객체**로 읽는다.

```python
if getattr(usage, "prompt_tokens", None) is not None: ...
inp = getattr(usage, "input_tokens", 0) or 0
```

`ResultMessage.usage` 는 `dict[str, Any]` 다. 모든 `getattr` 이 빗나가 `0.0` 이 나오고
`ReportRow.cost_usd` 원장이 0 으로 채워진다. 실패하지 않고 **조용히** 틀린다.

**수정**: `cost_of()` 초입에 dict 정규화를 둔다. 그리고 §2-3 과 함께 다뤄야 한다.

### 2-3. ⚠ BLOCKER — `total_cost_usd` 는 세션 누적값이다

실측(§1-7): 같은 클라이언트에서 0.001982 → 0.004043 → 0.006326 으로 **단조 증가**한다.
그대로 `spend()` 에 넣으면 원장이 중복 계상된다. 더 나쁜 자리가 있다 —

`dialogue.py:443-448`

```python
total_cost = 0.0   # 재시도해도 첫 호출은 이미 과금됐다 — 마지막 응답 것만 남기면
                   # 비용이 새어나간다(codex 감사 260908)
for attempt in range(2):
    resp = llm.call(...)
    total_cost += llm.spend(resp)
```

이 줄은 260908 감사로 **일부러** 누적하게 고친 것이다. 누적값을 다시 누적하므로
구독 모드에서는 **정확히 반대 방향으로** 틀린다.

**수정**: 클라이언트별 직전 `total_cost_usd` 를 들고 **차분**을 반환한다.
`_SubscriptionResponse` 가 그 차분을 `spend()` 용으로 들고 있게 한다.

---

## 3. 설계 — 세 번째 provider + 배타 대여 풀

`llm.Creds.provider` 에 **`"subscription"`** 을 더한다. `llm.call()` 이 이미 provider 로
갈라지고(llm.py:288) 호출부는 `tool_input()`·`text_of()`·`spend()` 만 보므로,
그 셋의 모양만 맞추면 대부분의 호출부가 그대로 산다(**전부는 아니다 — §4-1**).

### 3-1. 동기·비동기 경계

이 앱의 실행 경로는 이렇다:

```
asyncio 루프
  └ server.py:639 전용 ThreadPoolExecutor  (Chromium·16분짜리 호출 전용)
     └ jobs.py:374  asyncio.to_thread(fn)
        └ 동기 잡 함수
           └ llm.gather()  ThreadPoolExecutor(max_parallel_llm=6)   ← 7곳에서 쓴다
              └ llm.call()   ← **동기여야 한다**
```

`claude-agent-sdk` 는 asyncio 이고 `ClaudeSDKClient` 는 **자기를 만든 루프에 묶인다**.
(같은 함정이 `render_google.py:274` 에 이미 적혀 있다 — "Playwright 동기 API 객체는
만든 스레드에 묶인다".) 그래서:

```
전용 루프 스레드 1개 (daemon)         ← 모든 클라이언트를 여기서 만든다
  ClaudeSDKClient × N  →  queue.Queue  ← 배타 대여
llm.call()  =  q.get() → run_coroutine_threadsafe(...).result(timeout) → q.put()
```

`.result(timeout=deadline_s)` 가 `deadline_s` 를 그대로 받아 준다 — 스트림을 손으로
돌던 llm.py:331-345 의 벽시계 상한이 여기서는 공짜다.

### 3-2. 세션 회수 — 문맥이 쌓이기 때문에

한 클라이언트를 계속 쓰면 `in` 이 1187 → 1809 로 오른다(§1-7). 두 가지가 나빠진다:
쿼터가 새고, **호출들이 서로를 본다**(번역 호출이 직전 리서치 답을 문맥으로 갖는다).
이 앱의 호출은 서로 독립이어야 한다.

**규칙**: 대여 반납 시 `get_context_usage()` 또는 누적 `input_tokens` 가 임계를 넘으면
`disconnect()` 하고 새로 만든다. 회수는 깨끗하다(§1-8). 임계 초기값 **8,000 토큰**.

### 3-3. 매핑표

| 지금 (Anthropic API) | 구독 경로 | 위험 |
|---|---|---|
| `system=` | `options.system_prompt` (**반드시 교체** §1-4) | — |
| `messages=[...]` | stream-json user 메시지 | — |
| 이미지 base64 블록 | 같은 블록 그대로 | 없음 (§1-3) |
| `tools=[T]` + 강제 `tool_choice` | `options.output_format` = `{json_schema: T.input_schema}` | 도구 2개 이상 → §4-2 |
| `effort=` | `options.effort` | — |
| `thinking=` | `options.thinking` | — |
| `max_tokens=` | **없다** | §4-4 |
| `cache_control` 블록 | **없다** | §4-3 |
| 서버측 `web_search_20260209` | `WebSearch`(클라이언트 루프) | §4-1 |
| `deadline_s` | `Future.result(timeout=)` | 개선 |
| `should_stop()` | `client.interrupt()` | 개선 (§1-10) |
| `resp.usage` → `cost_of()` | `ResultMessage.usage`(dict) **차분** | §2-2 §2-3 |
| 429 재시도 | `RateLimitEvent` 선제 확인 | §5-4 |

---

## 4. 옮겨지지 않는 것

### 4-1. ⚠ 리서치 출처 목록이 통째로 빈다

`research.py:145`

```python
def _sources_from(resp) -> list[dict]:
    for b in resp.content:
        if getattr(b, "type", None) != "web_search_tool_result":
            continue
        ...
```

응답 블록에서 검색 결과 URL·제목을 직접 뽑는다. 구독 경로에는 이 블록이 없다
(§1-5 에서 `web_search_requests` 가 0 인 것과 같은 이유 — 서버측 도구가 아니다).
**증거로 인용된 URL 이 보고서에서 사라진다.** 이 앱의 원자는 출처가 전부이므로
이건 장식이 아니라 제품 기능이다.

→ v1 의 "호출부를 한 줄도 고치지 않는다" 는 **틀렸다.**

**되찾을 수 있다 — 실측(260916).** 스트림을 뜯어 보면 두 경로가 있다:

```
스트림 구조:  AssistantMessage[ThinkingBlock, ToolUseBlock] → UserMessage[ToolResultBlock]
  ① ToolResultBlock 안의 원시 검색 결과 → URL 20건 회수
  ② 도구 스키마에 sources[] 필드를 더해 모델이 스스로 신고 → 6건
```

그런데 **①이 ②보다 나쁘다.** 원시 20건에는 *아테네*의 하드리아누스 개선문이 섞여 있었다
(질문은 제라시였다). 모델이 신고한 6건은 전부 제라시 것이었다. 검색기는 원래 빗나간
결과를 같이 돌려주고, 서버측 도구를 쓰던 예전 경로에서는 **모델이 실제로 인용한 것만**
`web_search_tool_result` 로 남았다.

**권고**: `research.py` 의 기존 도구 스키마에 `sources[{title,url}]` 를 더해 ②로 받고,
①을 **허용 목록으로 써서 교차 검증**한다(신고한 URL 이 실제 검색 결과에 없으면 버린다).
스트림 수확기를 따로 만드는 것보다 작고, 환각 URL 도 같이 막는다.

값: 검색 1건짜리 리서치 호출 4턴 · 정가환산 $0.0599.

### 4-2. 도구가 2개인 호출

`molecule.py:756` 은 `[web_search 도구, _DOC_TOOL]` 둘을 함께 넘기고 시도마다
`tool_choice` 를 바꾼다(auto → 강제). `output_format` 은 "출력 스키마" 하나라
이 모양을 직접 표현하지 못한다.
→ **분자 이름 붙이기는 1단계 범위에서 뺀다.** (`--tools WebSearch` + 출력 스키마
조합으로 옮길 수 있는지는 §1-5 가 가능성을 보였으나 미검증.)

### 4-3. 프롬프트 캐시를 잃는다

캐시를 쓰는 곳 셋은 전부 가장 비싼 호출이다 —
`analyze.py:319`(언어별 재사용), `correction.py:661`(같은 이미지가 **세 호출**에),
`dialogue.py:338,340`(턴마다 재사용). 블록 단위 `cache_control` 을 얹을 자리가 없다.

**영향**: 돈으로는 0(구독은 청구가 없다). **쿼터로는 실손** — 입력가 0.1배였던 자리가 1.0배.
**완화**: 정정·대화는 원래 "같은 이미지로 이어지는 대화" 이므로 §3-2 의 회수 규칙에서
**예외로 두고 한 세션에 이어 붙이면** Claude Code 자체 캐시가 붙는다. v2 과제.

### 4-4. max_tokens 를 못 준다

`ClaudeAgentOptions` 에 없다. 지금 20곳이 3,000~12,000 으로 잡고 있고,
`translate_max_tokens: 12000` 에는 "Opus 5 는 thinking 이 켜지므로 예전 값이면
답변이 잘린다" 는 주석이 붙어 있다(config.py:130).
상한이 **사라지는** 쪽이라 잘림 위험은 줄지만 폭주 제동도 같이 사라진다.
→ `options.max_budget_usd`(정가 환산)로 대신한다.

---

## 5. 켜면 안 되는 자리 — 약관 ⚠

구독 모드는 **단독 소유자 모드 전용**이다. 문서가 아니라 **코드로** 막는다.

```python
# config.py — 이 조합은 존재할 수 없다
if s.llm_backend == "subscription" and s.multi_user:
    s.llm_backend = "api"      # 되돌리고 시작 배너에 사유를 찍는다
```

- **내 구독으로 남을 먹여 주는 것은 계정 공유다.** `quota.py` 의 "무료 1건" 은
  *운영자의 API 키가 돈을 낸다*는 전제 위에 있다. 구독 모드에서 그 전제는
  "운영자의 **개인 구독**이 낸다" 가 되고, 그건 약관이 금지하는 계정 접근 공유다.
- Railway 배포는 구독 모드를 **쓸 수 없다**. 컨테이너에 OAuth 토큰을 넣어야 하는데
  약관 문제이자 비밀 취급 문제다. **배포는 API 키 경로 그대로 간다.**
- `webapi.py:17` 의 `PROVIDERS` 에 `subscription` 을 **넣지 않는다**.
  회원이 고를 수 있는 것이 되면 안 된다.

즉 이 기능의 이름은 "구독 토글" 이 아니라
**"내 PC 에서 내 구독으로 내 보고서를 만드는 모드"** 다.
상용화 검토(260909)와 이 기능은 **다른 경로**이고 섞이면 안 된다.

### 5-4. 쿼터 계기는 선택이 아니다

보고서 한 건이 15~20 호출이고 5시간 창은 지금 62% 다. 넘치면 밸브가 없다(§1-9).

1. 잡 시작 **전** `utilization` 확인 → 임계(0.8) 넘으면 막고 `resetsAt` 를 사람이 읽는 시각으로
2. 화면 오른쪽 위 잔량 계기 — `five_hour` / `seven_day` 둘 다
3. `status == "rejected"` 면 실패가 아니라 **대기**로 표시(재개 경로가 이미 있다)

---

## 6. 단계

### 1단계 — 블로커 셋 (§2) 을 먼저 고친다 (반나절)

구독 모드와 **무관하게** 지금 고쳐도 되는 것들이고, 안 고치면 나머지가 조용히 틀린다.
`tests/` 에 `Creds("subscription","")` 가 API 키로 떨어지지 않는지, dict usage 가
0 을 내지 않는지 못 박는다.

### 2단계 — 배관 (하루)

- `pyproject.toml`: `subscription = ["claude-agent-sdk>=0.2.150"]`
- `config.py`: `llm_backend: str = "api"` (`GEOHELPER_LLM_BACKEND`) + §5 강제 하향
- `llm.py`: 전용 루프 스레드 + 배타 대여 풀(§3-1) + `_SubscriptionResponse`
- 풀 크기 기본 **2**, `GEOHELPER_SUBSCRIPTION_POOL` 로 조정 (§1-8 메모리)
- **검사**: 같은 프롬프트를 두 백엔드로 → `tool_input()` 결과 비교.
  동시 호출 6건에 답이 섞이지 않는지(§1-6 재현 방지) **반드시** 포함한다.

### 3단계 — 안전장치 (반나절)

- 쿼터 게이트 + 화면 계기 (§5-4)
- 세션 회수 규칙 (§3-2)
- 시작 배너: `LLM 백엔드 : 구독(Max) · 5시간 잔량 38%`

### 4단계 — 화면 토글 (반나절)

- '내 키' 메뉴 라디오: `내 API 키` / `이 PC 의 Claude 구독`
- 구독 선택 시 `claude auth status` 결과(이메일·플랜) 표시
- **다중 사용자 모드에서는 이 라디오가 아예 안 보인다**

### 5단계 — 리서치 출처 복원 (§4-1) (반나절)

`research.py` 도구 스키마에 `sources[{title,url}]` 를 더하고, 스트림의 `ToolResultBlock`
URL 을 허용 목록으로 써서 교차 검증한다. **이게 되기 전까지 구독 모드는 리서치를 켜지 않는다.**

(API 경로에도 그대로 둬도 해롭지 않다 — `_sources_from` 이 이미 주는 것과 겹칠 뿐이다.)

### 6단계 — 캐시 회복 (§4-3), 분자(§4-2) — 선택

---

## 7. 검사

| 무엇 | 어떻게 |
|---|---|
| 구조화 출력 동치 | 같은 프롬프트 두 백엔드 → `tool_input()` dict 비교 |
| 비전 동치 | 같은 캡처 두 백엔드 → 국가 일치 |
| **동시 호출 정합성** | 6건 동시 → 각자 자기 답을 받는가 (§1-6 이 실제로 깨진 자리) |
| 자격증명 폴백 금지 | `Creds("subscription","")` 가 API 키로 떨어지지 않는가 (§2-1) |
| 비용 차분 | 같은 클라이언트 3회 → `spend()` 합이 누적값이 아닌가 (§2-3) |
| 다중 사용자 차단 | `DATABASE_URL` 있을 때 `subscription` → `api` 로 되돌아가는가 |
| 비밀 유출 없음 | 잡 payload·`jobs.jsonl`·로그에 OAuth 토큰이 없는가 |
| 프로세스 회수 | 잡 100건 후 헤드리스 `claude` 수가 풀 크기 이하인가 |
| 쿼터 게이트 | `utilization` 0.9 로 모킹 → 잡이 **시작 전에** 막히는가 |

---

## 8. 열린 질문

1. ~~`should_stop()` 을 `interrupt()` 로 대체할 수 있는가~~ → **해결·구현**(§1-10).
   `aborted_streaming` 을 `LLMCanceled` 로 번역한다(llm.py `_call_subscription`).
2. ~~리서치를 WebSearch 로 갈 것인가~~ → **결정: 간다**(사용자 지시 260916).
   구현: `_translate_tools` 가 `web_search_*` 선언을 보면 `--tools WebSearch` 를 켠다.
   출처는 **모델 신고 + 검색기 결과 교차 검증**이고, 검증에서 떨어진 건수는
   `sources_rejected` 로 결과에 남는다. 실측 — 제라시 12건 통과/0 버림, 바튼스네스
   11건 통과/0 버림, 카라크(API 경로) 7건 통과/0 버림.
3. `total_cost_usd`(정가 환산) 차분을 `ReportRow.cost_usd` 에 계속 넣을 것인가?
   → 현재는 **넣는다**(`llm.spend()` 가 차분을 돌려준다). 어느 백엔드였는지 남기는
   컬럼은 **아직 없다** — 원장에서 구독분과 실지출이 섞여 보인다. 남은 일이다.
4. `refreshTokenExpiresAt` 가 지나면 `claude auth login` 이 필요하다.
   지금은 호출이 실패할 때 비로소 알게 된다. 배너·계기가 "구독 신호 없음" 을 띄우지만
   **선제 경고는 없다.** 남은 일이다.

---

## 9. 검토 이력

- **1차 초안** 260916 · fable — v1
- **2차 자체 검증** 260916 · fable — 실측 7건(§1-4~1-9) + 코드 감사.
  v1 §2-1(영속 클라이언트 공유)을 **반증**하고 블로커 3건(§2)·출처 손실(§4-1)을 찾음. → v2
- **3차 반박** 260916 · codex — **미수령.**
  1차 시도: 컴패니언이 타 세션 스레드에 물려 스레드를 열지 못함(codex 항목 0건 확인) → 중단.
  2차 시도: `task-mu2u9jq9-mcq5rn` 로 등록됨. 그러나 codex-rescue 에이전트는 **전달자**라
  결과를 수거할 수 없고, `/codex:result` 는 사람이 직접 불러야 하는 명령이다.
  → **사용자가 `/codex:result task-mu2u9jq9-mcq5rn` 를 실행해야 회수된다.**
- **4차 판정** — *3차 수령 후*
