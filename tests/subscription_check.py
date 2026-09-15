"""구독 백엔드 — 조용히 틀리는 자리들을 못 박는다.

이 파일이 있는 이유. 구독 모드의 결함은 **전부 조용했다**. 예외가 나지 않고, 로그도 깨끗하고,
보고서도 나온다. 틀린 것은 값뿐이다:

  ① 구독 Creds 는 키가 없다. 그런데 판정이 `c.api_key` 하나여서 falsy 로 걸렸고,
     **서버의 API 키로 떨어졌다** — 사용자는 구독으로 도는 줄 알고 요금을 냈다.
  ② SDK 의 usage 는 dict 인데 cost_of() 는 getattr 로 읽는다 → 전부 빗나가 0.0.
     원장(ReportRow.cost_usd)이 0 으로 채워진다.
  ③ total_cost_usd 는 **세션 누적값**이다(실측 0.0020 → 0.0040 → 0.0063).
     그대로 합산하면 중복 계상된다. dialogue.py:443 은 재시도마다 **더하도록** 일부러
     고쳐진 자리라(codex 감사 260908) 특히 위험하다.
  ④ 다중 사용자 모드에서 구독이 켜지면 개인 구독으로 남의 요청을 처리하게 된다(계정 공유).
  ⑤ 출처 교차 검증 — 모델이 신고했지만 검색기가 돌려준 적 없는 URL 은 지어낸 것이다.

⑤ 말고는 전부 네트워크 없이 검사된다. 실제 호출 검사는 GEOHELPER_SUBSCRIPTION_LIVE=1 일 때만.

실행:  uv run --no-sync python tests/subscription_check.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ggh_sub_"))
os.environ["GEOHELPER_CAPTURES"] = str(TMP / "captures")
os.environ["GEOHELPER_KNOWLEDGE"] = str(TMP / "knowledge")
os.environ["GEOHELPER_DATA_DIR"] = str(TMP / "data")
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg, flush=True)
    if not cond:
        FAIL.append(msg)


def main() -> int:  # noqa: PLR0915
    from geoguesshelper import llm, research, subscription
    from geoguesshelper.config import load_settings

    print("① 구독 자격증명이 API 키로 떨어지지 않는가")
    s = load_settings()
    s.anthropic_api_key = "sk-ant-THIS-MUST-NOT-BE-USED"
    sub = llm.Creds("subscription", "", "이 PC")
    check(sub.usable, "키가 없어도 usable 이다 — 그게 구독의 요점이다")
    tok = llm.set_credentials(sub)
    try:
        got = llm.current_credentials(s)
        check(got.provider == "subscription",
              f"구독으로 남는다 (실제 {got.provider!r})")
        check(got.api_key == "", "API 키를 끌어오지 않는다")
    finally:
        llm.reset_credentials(tok)

    # 반대 방향도 지킨다 — 빈 anthropic Creds 는 예전처럼 폴백해야 한다.
    tok = llm.set_credentials(llm.Creds("anthropic", ""))
    try:
        got = llm.current_credentials(s)
        check(got.provider == "anthropic" and got.api_key == s.anthropic_api_key,
              "빈 anthropic 키는 예전처럼 서버 키로 폴백한다")
    finally:
        llm.reset_credentials(tok)

    print("\n② dict usage 에서 비용이 0 이 되지 않는가")
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    c = llm.cost_of(usage, "claude-opus-5")
    check(abs(c - 30.0) < 0.01, f"1M in + 1M out = $30.00 (실제 ${c:.4f})")
    nested = {"input_tokens": 0, "output_tokens": 0,
              "server_tool_use": {"web_search_requests": 3}}
    c2 = llm.cost_of(nested, "claude-opus-5")
    check(abs(c2 - 0.03) < 1e-6, f"중첩 dict 의 web_search 3건 = $0.03 (실제 ${c2:.4f})")
    check(llm.cost_of(None) == 0.0, "None 은 0.0")

    print("\n③ 누적값이 아니라 차분을 돌려주는가")
    r1 = subscription.Response(structured={"a": 1}, tool_name="t", text="", usage={},
                               model="m", cost_usd=0.002)
    r2 = subscription.Response(structured={"a": 2}, tool_name="t", text="", usage={},
                               model="m", cost_usd=0.002)
    total = llm.spend(r1) + llm.spend(r2)
    check(abs(total - 0.004) < 1e-9,
          f"재시도 2회 합이 각 호출의 합이다 (실제 ${total:.6f})")
    check(llm.tool_input(r1, "t") == {"a": 1}, "tool_input 이 구조화 출력을 낸다")
    check(llm.tool_input(r1, "다른이름") is None, "이름이 다르면 None — 준 적 없는 것이다")

    print("\n④ 다중 사용자에서 구독이 강제로 꺼지는가")
    os.environ["GEOHELPER_LLM_BACKEND"] = "subscription"
    try:
        s2 = load_settings()
        check(s2.llm_backend == "subscription", "단독 소유자 모드에서는 켜진다")
        check(s2.has_anthropic, "구독이면 키가 없어도 분석이 켜진다")
        os.environ["DATABASE_URL"] = "postgresql://x/y"
        s3 = load_settings()
        check(s3.llm_backend == "api",
              f"다중 사용자면 api 로 되돌린다 (실제 {s3.llm_backend!r})")
        check(bool(s3.llm_backend_forced), "되돌린 사유를 남긴다")
    finally:
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("GEOHELPER_LLM_BACKEND", None)

    print("\n⑤ 출처 교차 검증 — 지어낸 URL 을 버리는가")

    class FakeResp:
        """구독 응답 흉내 — 모델 신고 + 검색기가 실제로 돌려준 URL."""

        is_subscription = True

        def __init__(self, claimed, harvested):
            self._claimed = claimed
            self.harvested_urls = harvested
            self.content = []
            self.tool_name = "location_profile"
            self.structured = {"summary": "x", "sources": claimed}
            self.text = ""
            self.usage = {}
            self.model = "m"
            self.cost_usd = 0.0

    real = "https://www.lonelyplanet.com/jordan/jerash-and-the-north/jerash/attractions/"
    fake = "https://example.org/totally-made-up-page"
    resp = FakeResp(
        claimed=[{"title": "Lonely Planet", "url": real},
                 {"title": "지어낸 것", "url": fake}],
        harvested=[real, "https://universes.art/en/art-destinations/jordan/jerash/hadrians-arch"],
    )
    kept, dropped = research._sources_from(resp)
    urls = [k["url"] for k in kept]
    check(real in urls, "검색기가 실제로 돌려준 URL 은 남는다")
    check(fake not in urls, "검색 결과에 없던 URL 은 버린다 (환각 차단)")
    check(dropped == 1, f"버린 건수를 보고한다 (실제 {dropped})")

    # 추적 파라미터·www·끝 슬래시가 달라도 같은 글로 본다.
    resp2 = FakeResp(
        claimed=[{"title": "같은 글", "url": "https://lonelyplanet.com/jordan/jerash-and-the-north/jerash/attractions?utm_source=x"}],
        harvested=[real],
    )
    kept2, dropped2 = research._sources_from(resp2)
    check(len(kept2) == 1 and dropped2 == 0,
          f"www·끝슬래시·추적파라미터 차이는 같은 글로 본다 (남김 {len(kept2)}, 버림 {dropped2})")

    # 검색을 아예 안 돌린 호출은 거를 기준이 없다 — 거른 척하면 안 된다.
    resp3 = FakeResp(claimed=[{"title": "t", "url": "https://a.example/x"}], harvested=[])
    kept3, dropped3 = research._sources_from(resp3)
    check(len(kept3) == 1 and dropped3 == 0,
          "검색 흔적이 없으면 거를 기준이 없으므로 통과시킨다")

    print("\n⑥ 스키마에 sources 가 실제로 들어갔는가")
    props = research._PROFILE_TOOL["input_schema"]["properties"]
    check("sources" in props, "location_profile 에 sources 가 있다")
    check("sources" not in (research._PROFILE_TOOL["input_schema"].get("required") or []),
          "required 에는 넣지 않는다 — 검색 안 한 호출까지 강요하지 않는다")
    sh = research._shard_tool(research._SHARDS[0])
    check("sources" in sh["input_schema"]["properties"], "partial_profile 에도 있다")

    print("\n⑦ 도구 번역 — 강제 tool_choice → 출력 스키마")
    tool = {"name": "report_location", "input_schema": {"type": "object", "properties": {}}}
    schema, name, web = subscription._translate_tools(
        [tool], {"type": "tool", "name": "report_location"})
    check(schema is tool["input_schema"] and name == "report_location" and not web,
          "단일 도구 → 스키마·이름, 웹검색 아님")
    schema, name, web = subscription._translate_tools(
        [{"type": "web_search_20260209", "name": "web_search"}, tool], None)
    check(web and name == "report_location", "web_search 가 섞이면 웹검색을 켜고 커스텀 도구를 쓴다")
    schema, name, web = subscription._translate_tools(None, None)
    check(schema is None and not web, "도구 없음 → 스키마 없음(자유 텍스트)")

    if os.environ.get("GEOHELPER_SUBSCRIPTION_LIVE", "").strip() in ("1", "true", "yes"):
        print("\n⑧ 실제 호출 (LIVE)")
        s4 = load_settings()
        s4.llm_backend = "subscription"
        s4.subscription_pool = 2
        ok, why = subscription.available(s4)
        check(ok, f"구독 백엔드를 쓸 수 있다 ({why})")
        # .env 의 ANTHROPIC_API_KEY 가 자식 프로세스에 상속되면 CLI 가 구독 대신 그 키를 쓴다.
        # 쿼터 이벤트는 구독 사용자에게만 오므로, 이게 "정말 구독인가" 의 증거다.
        info = subscription.rate_limit(s4, max_age_s=0.0)
        check(bool(info.get("sawRateLimit")),
              f"API 키가 환경에 있어도 구독으로 돈다 (5시간 {info.get('five_hour')})")
        if ok:
            _live(s4, llm, check)

    import shutil

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'PASS' if not FAIL else 'FAIL'} — 실패 {len(FAIL)}건")
    for f in FAIL:
        print("   ·", f)
    return 1 if FAIL else 0


def _live(s, llm, check) -> None:
    """동시에 물려도 **각 호출이 자기 답을 받는가** — 이게 실제로 깨졌던 자리다.

    예전 설계(영속 클라이언트 공유)에서는 워커3이 워커4의 답을, 워커4가 워커1의 답을
    받았다. 그래서 여기서 재는 것은 모델의 지식이 아니라 **배선**이다. 지리 상식으로
    재면 haiku 가 틀릴 때(Dannevirke → Denmark) 배선이 멀쩡해도 빨갛게 된다.
    그래서 워커마다 고유 표식을 주고 그게 그대로 돌아오는지 본다 — 결정적이다.
    """
    import concurrent.futures as cf

    tool = {"name": "echo", "description": "Return the token you were given, verbatim.",
            "input_schema": {"type": "object", "required": ["token"],
                             "properties": {"token": {"type": "string"}}}}
    tokens = ["GGH-ALPHA-11", "GGH-BRAVO-22", "GGH-CHARLIE-33", "GGH-DELTA-44"]

    def one(tk: str):
        tok = llm.set_credentials(llm.Creds("subscription", ""))
        try:
            r = llm.call(s, system="You echo tokens exactly as given.",
                         messages=[{"role": "user",
                                    "content": f"Your token is {tk}. Call echo with it."}],
                         tools=[tool], tool_choice={"type": "tool", "name": "echo"},
                         max_tokens=200, role="draft")
            return (llm.tool_input(r, "echo") or {}).get("token", ""), llm.spend(r)
        finally:
            llm.reset_credentials(tok)

    with cf.ThreadPoolExecutor(max_workers=len(tokens)) as ex:
        got = list(ex.map(one, tokens))
    for tk, (have, cost) in zip(tokens, got):
        check(tk == (have or "").strip(),
              f"{tk} 를 보낸 호출이 {tk} 를 받는다 (실제 {have!r}, 정가환산 ${cost:.5f})")
    others = {t for t in tokens}
    crossed = [f"{tk}→{have}" for tk, (have, _) in zip(tokens, got)
               if have and have.strip() != tk and have.strip() in others]
    check(not crossed, f"다른 호출의 답을 받은 것이 없다 {crossed}")
    check(all(c > 0 for _, c in got), "정가 환산 비용이 0 이 아니다(차분이 살아 있다)")


if __name__ == "__main__":
    raise SystemExit(main())
