"""무료 1건이 **HTTP 로** 실제로 동작하는가 — 단위 검사가 잡지 못하는 것들.

quota.decide 가 맞아도 서버는 틀릴 수 있다. 쿠키를 안 심어 주면 매 요청이 새 방문자가
되고, 미들웨어가 request.state.user 를 안 채우면 회원이 비회원으로 취급되며, 잡이
끝난 뒤 기록이 안 남으면 무료 1건이 무한히 반복된다. 그 셋은 전부 HTTP 를 지나야만
드러난다.

실행:  uv run --extra server python tests/quota_http_check.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ggh_q_"))
os.environ["GEOHELPER_CAPTURES"] = str(TMP / "captures")
os.environ["GEOHELPER_KNOWLEDGE"] = str(TMP / "knowledge")
os.environ["GEOHELPER_DATA_DIR"] = str(TMP / "data")
os.environ["GEOHELPER_NO_BROWSER"] = "1"
os.environ["GEOHELPER_KEY_SECRET"] = "test-secret-that-is-long-enough"
os.environ["GEOHELPER_FREE_REPORTS"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'app.db').as_posix()}"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg, flush=True)
    if not cond:
        FAIL.append(msg)


def main() -> int:
    try:
        import sqlalchemy  # noqa: F401
        from fastapi.testclient import TestClient
    except ModuleNotFoundError:
        print("SKIP - sqlalchemy/fastapi 미설치 (uv sync --extra server)")
        return 0

    from geoguesshelper import db, quota, server, tenancy
    from geoguesshelper.config import load_settings
    from geoguesshelper.knowledge import Atom

    s = load_settings()
    s.anthropic_api_key = "sk-ant-server-key-for-free-tier"   # 무료 1건이 쓸 운영자 키
    db.reset_engine()
    tenancy.forget_hydration()

    made: list[str] = []

    async def fake_report(job):
        """진짜 LLM 대신 — 원자 2개를 만들고 보고서 하나를 냈다고 답한다."""
        d = s.knowledge_dir / "atoms"
        d.mkdir(parents=True, exist_ok=True)
        n = len(made)
        for i in range(2):
            a = Atom(id=f"atm_{n:04x}{i:08x}", layer="history", scope="country",
                     title=f"원자 {n}-{i}", body="본문", entities=["x"], tags=["t"],
                     cell="wydm9qq")
            (d / f"{a.id}.md").write_text(a.to_md(), encoding="utf-8")
        made.append("r")
        return {"status": "OK", "reports": [f"report_{n}.html"], "cost_usd": 0.11}

    server._make_handlers = lambda _s: {"report": fake_report}
    app = server.build_app(s)

    def wait(n: int) -> None:
        for _ in range(100):
            if len(made) >= n:
                time.sleep(0.2)     # 기록(_record_work)이 끝날 틈
                return
            time.sleep(0.1)

    print("① 가입 전 방문자 — 무료 1건")
    with TestClient(app) as c:
        q = c.get("/api/quota").json()
        check(q["allowed"] and q["remaining"] == 1, f"첫 방문자는 1건 남아 있다 {q}")
        check(quota.VISITOR_COOKIE in c.cookies, "방문자 쿠키를 심었다")
        vid = c.cookies.get(quota.VISITOR_COOKIE)

        r = c.post("/api/jobs/report", json={"files": ["a.jpg"], "label": "무료"})
        check(r.status_code == 200, f"무료 1건은 받아 준다 ({r.status_code})")
        wait(1)

        check(c.cookies.get(quota.VISITOR_COOKIE) == vid,
              "쿠키를 매 요청 새로 심지 않는다(그러면 한도가 계속 되살아난다)")

        print("\n② 두 번째부터는 막는다")
        r2 = c.post("/api/jobs/report", json={"files": ["b.jpg"], "label": "두번째"})
        check(r2.status_code == 402, f"402 로 막는다 ({r2.status_code})")
        check(r2.headers.get("x-quota-reason") == "signup",
              f"이유는 '가입' ({r2.headers.get('x-quota-reason')})")
        check("가입" in (r2.json().get("detail") or ""), "무엇을 하라고 말한다")
        check(len(made) == 1, "막힌 요청은 실제로 돌지 않았다(돈이 안 나갔다)")

        print("\n③ 비회원의 1건도 지식은 남는다")
        with db.session_for(s) as ses:
            total = db.atom_total(ses)
            reports = db.report_count(ses, anon_key=vid)
        check(total == 2, f"만든 원자 2개가 공용 저장소에 남았다 (={total})")
        check(reports == 1, f"보고서 1건이 그 방문자 앞으로 기록됐다 (={reports})")

        print("\n④ 가입하면 이어서 쓴다")
        r = c.post("/api/auth/signup", json={"email": "u@example.com",
                                             "password": "password123"})
        check(r.status_code == 200, f"가입 OK ({r.status_code})")
        q = c.get("/api/quota").json()
        check(q["allowed"] and q["plan"] == "free", f"회원의 무료 1건은 별도로 센다 {q}")
        r = c.post("/api/jobs/report", json={"files": ["c.jpg"], "label": "회원1"})
        check(r.status_code == 200, f"회원 첫 건 OK ({r.status_code})")
        wait(2)
        r = c.post("/api/jobs/report", json={"files": ["d.jpg"], "label": "회원2"})
        check(r.status_code == 402 and r.headers.get("x-quota-reason") == "pay",
              f"다 쓴 회원은 결제 안내 ({r.status_code}/{r.headers.get('x-quota-reason')})")

        print("\n⑤ 회원의 참조가 생겼는가 — 저장소가 아니라 창")
        me = c.get("/api/me").json()
        uid = me["user"]["id"]
        with db.session_for(s) as ses:
            refs = db.ref_count(ses, uid)
            total2 = db.atom_total(ses)
            rows = db.all_atom_rows(ses)
        check(refs == 4, f"자기 보고서가 만진 원자 4개를 참조한다 (={refs})")
        check(total2 == 4, f"공용 원자는 4개 — 사본이 생기지 않았다 (={total2})")
        check(sum(1 for r_ in rows if r_.first_user_id is None) == 2,
              "비회원이 만든 2개는 주인 없이 공용으로 남는다")
        v = c.get("/api/my/knowledge").json()
        check(v["atoms"] == 4 and v["refs"] == 4 and v["reports"] == 1,
              f"화면이 전체와 내 것을 나란히 받는다 {[v.get(k) for k in ('atoms','refs','reports')]}")

        print("\n⑥ 자기 키를 넣으면 한도가 없다")
        r = c.post("/api/jobs/report", json={"files": ["e.jpg"], "label": "내키"},
                   headers={"X-Llm-Key": "sk-ant-api03-" + "Z" * 60})
        check(r.status_code == 200, f"BYO 키 요청은 한도를 지나간다 ({r.status_code})")
        q = c.get("/api/quota", headers={"X-Llm-Key": "sk-ant-api03-" + "Z" * 60}).json()
        check(q["remaining"] is None and q["unlimited"],
              f"한도가 없으면 remaining 은 0 이 아니라 null 이다 {q}")

    print("\n⑦ 헬스체크는 어떤 설정에서도 응답한다")
    with TestClient(app) as c:
        r = c.get("/api/health")
        check(r.status_code == 200 and r.json()["mode"] == "multi-user",
              f"DB 가 붙었으면 실제로 질의해 본다 {r.status_code} {r.json().get('mode')}")

    import shutil

    db.reset_engine()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'PASS' if not FAIL else 'FAIL'} — 실패 {len(FAIL)}건")
    for f in FAIL:
        print("   ·", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
