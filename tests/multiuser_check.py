"""다중 사용자 계층 검사 — 가입·세션·키 보관·작업공간 분리·공유 지식·무료 1건.

이 파일이 있는 이유: 여기서 조용히 틀리면 **남의 키로 남의 돈을 쓴다**. 이 앱에서 가장
나쁜 실패이고, 화면으로는 티가 안 난다.

④⑤ 는 260913 에 뒤집혔다. 앞선 판은 "회원마다 지식 저장소가 다르다"를 검사했는데, 그건
원자 id 가 내용 주소라는 성질(knowledge.py:164)을 깨는 설계였다. 지금은 반대를 검사한다 —
**지식은 하나고, 회원마다 다른 것은 그것을 가리키는 참조뿐이다.**

실행:  uv run --extra server python tests/multiuser_check.py
       sqlalchemy 가 없으면 전체를 skip 한다(단독 소유자 모드에서는 필요 없다).
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg, flush=True)
    if not cond:
        FAIL.append(msg)


def main() -> int:
    try:
        import sqlalchemy  # noqa: F401
    except ModuleNotFoundError:
        print("SKIP — sqlalchemy 미설치 (uv sync --extra server)")
        return 0

    from geoguesshelper import auth, db, llm, secretbox, tenancy
    from geoguesshelper.config import Settings

    tmp = Path(tempfile.mkdtemp(prefix="ggh_mu_"))
    s = Settings()
    s.database_url = f"sqlite:///{(tmp / 'app.db').as_posix()}"
    s.data_dir = tmp / "data"
    s.key_enc_secret = "test-secret-that-is-long-enough"
    s.admin_emails = ["boss@example.com"]
    s.knowledge_dir = tmp / "knowledge"
    s.free_reports = 1
    db.reset_engine()
    tenancy.forget_hydration()

    print("① 가입·로그인·세션")
    with db.session_for(s) as ses:
        a = auth.signup(ses, s, email="A@Example.com ", password="password123", name="에이")
        b = auth.signup(ses, s, email="b@example.com", password="password456")
        boss = auth.signup(ses, s, email="boss@example.com", password="password789")
        a_id, b_id = a.id, b.id
        check(a.email == "a@example.com", "이메일을 소문자·공백제거로 정규화한다")
        check(a.plan == "free" and boss.plan == "pro", "관리자 이메일은 처음부터 pro")
        tok_a = auth.open_session(ses, a)
    with db.session_for(s) as ses:
        check(auth.user_for_token(ses, tok_a).id == a_id, "세션 토큰으로 사용자를 찾는다")
        check(auth.user_for_token(ses, "없는토큰") is None, "없는 토큰은 None")
    with db.session_for(s) as ses:
        try:
            auth.signup(ses, s, email="a@example.com", password="password123")
            check(False, "중복 가입은 거부돼야 한다")
        except auth.AuthError:
            check(True, "같은 이메일 중복 가입 거부")
    with db.session_for(s) as ses:
        try:
            auth.login(ses, email="a@example.com", password="틀린비밀번호")
            check(False, "틀린 비밀번호는 거부돼야 한다")
        except auth.AuthError as exc:
            check("이메일 또는 비밀번호" in str(exc), "계정 존재 여부를 흘리지 않는다")
        check(auth.login(ses, email="a@example.com", password="password123").id == a_id,
              "올바른 비밀번호로 로그인")
    with db.session_for(s) as ses:
        auth.close_session(ses, tok_a)
    with db.session_for(s) as ses:
        check(auth.user_for_token(ses, tok_a) is None, "로그아웃한 세션은 즉시 무효")

    print("\n② 만료·정지")
    with db.session_for(s) as ses:
        u = ses.get(db.User, b_id)
        t = auth.open_session(ses, u)
        ses.get(db.UserSession, t).expires = time.time() - 1
    with db.session_for(s) as ses:
        check(auth.user_for_token(ses, t) is None, "만료된 세션은 거부")
    with db.session_for(s) as ses:
        u = ses.get(db.User, b_id)
        t2 = auth.open_session(ses, u)
        u.disabled = True
    with db.session_for(s) as ses:
        check(auth.user_for_token(ses, t2) is None, "정지된 계정은 세션이 살아 있어도 거부")
        ses.get(db.User, b_id).disabled = False

    print("\n③ API 키 — 암호문만, 소유자에 묶인다")
    plain = "sk-ant-api03-" + "x" * 60
    sealed = secretbox.seal(s, plain, user_id=a_id, provider="anthropic")
    check(plain not in sealed, "암호문에 평문이 들어 있지 않다")
    check(secretbox.open_(s, sealed, user_id=a_id, provider="anthropic") == plain,
          "같은 소유자·제공자로는 복호화된다")
    try:
        secretbox.open_(s, sealed, user_id=b_id, provider="anthropic")
        check(False, "다른 회원이 복호화하면 실패해야 한다")
    except Exception:
        check(True, "다른 회원의 암호문은 복호화되지 않는다(연관 데이터로 묶임)")
    try:
        secretbox.open_(s, sealed, user_id=a_id, provider="openai")
        check(False, "다른 제공자로 복호화하면 실패해야 한다")
    except Exception:
        check(True, "제공자가 다르면 복호화되지 않는다")
    check(secretbox.mask(plain).endswith(plain[-4:]) and plain[:20] not in secretbox.mask(plain),
          f"마스킹은 꼬리만 남긴다 ({secretbox.mask(plain)})")
    check(secretbox.provider_of("sk-ant-abc") == "anthropic", "Claude 키 모양을 알아본다")
    check(secretbox.provider_of("sk-proj-" + "y" * 40) == "openai", "ChatGPT 키 모양을 알아본다")
    s2 = Settings()
    s2.key_enc_secret = ""
    try:
        secretbox.seal(s2, plain, user_id=a_id, provider="anthropic")
        check(False, "비밀이 없으면 보관을 거부해야 한다")
    except secretbox.SecretUnavailable:
        check(True, "암호화 비밀이 없으면 키를 아예 받지 않는다")

    print("\n④ 작업공간은 나누고 지식은 나누지 않는다")
    sa = tenancy.settings_for_user(s, a_id)
    sb = tenancy.settings_for_user(s, b_id)
    check(sa.knowledge_dir == sb.knowledge_dir == s.knowledge_dir,
          "지식 저장소는 하나다 — 회원마다 나누지 않는다")
    check(sa.captures_dir != sb.captures_dir and sa.reports_dir != sb.reports_dir,
          "캡처·보고서·작업기록은 회원마다 나뉜다")
    check(str(a_id) in str(sa.captures_dir), "작업공간 경로에는 그 회원의 id 가 들어간다")
    check(s.captures_dir != sa.captures_dir, "원본 Settings 는 바뀌지 않는다")
    try:
        tenancy.settings_for_user(s, "../../etc")
        check(False, "경로 조작 id 는 거부돼야 한다")
    except ValueError:
        check(True, "경로가 될 수 없는 id 를 거부한다")

    print("\n⑤ 공유 원자 + 개별 참조 — 회원이 늘어도 지식 총량은 그대로")
    from geoguesshelper.knowledge import Atom

    def put(n: int, who: str) -> None:
        d = s.knowledge_dir / "atoms"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            a = Atom(id=f"atm_{who}{i:08x}", layer="history", scope="country",
                     title=f"{who} 원자 {i}", body=f"{who} 본문 {i}",
                     entities=[who], tags=["t"], cell="wydm9qq")
            (d / f"{a.id}.md").write_text(a.to_md(), encoding="utf-8")

    before = tenancy.atom_ids_now(s)
    put(5, "aa")
    r = tenancy.sync_atoms(s, a_id, before=before)
    check(r["created"] == 5 and r["reused"] == 0, f"A 가 새 원자 5개를 만들었다 {r}")
    with db.session_for(s) as ses:
        check(db.atom_total(ses) == 5, "공용 저장소에 5개")
        check(db.ref_count(ses, a_id) == 5, "A 의 참조 5개")
        check(db.ref_count(ses, b_id) == 0, "B 는 아직 아무것도 참조하지 않는다")

    # B 가 **같은 원자**에 닿는다 — 같은 사실이면 id 가 같으므로 새로 만들어지지 않는다.
    r_b = tenancy.sync_atoms(s, b_id, before=tenancy.atom_ids_now(s))
    with db.session_for(s) as ses:
        total_after = db.atom_total(ses)
        refs_b = db.ref_count(ses, b_id)
    check(total_after == 5, f"B 가 와도 지식 총량은 그대로 (={total_after})")
    check(refs_b == 5 and r_b["created"] == 0 and r_b["reused"] == 5,
          f"B 는 참조만 늘었다 (refs={refs_b}, {r_b})")

    # 한 원자를 두 회원이 가리킨다 — 그게 '창'이 다르다는 것의 전부다.
    with db.session_for(s) as ses:
        ids_a = set(db.user_atom_ids(ses, a_id))
        ids_b = set(db.user_atom_ids(ses, b_id))
        rows = db.all_atom_rows(ses)
        firsts = {r.first_user_id for r in rows}
        view = db.user_view(ses, b_id)
        exported = db.export_atoms(ses, b_id)
    check(ids_a == ids_b, "둘이 같은 원자를 본다(사본이 아니다)")
    check(firsts == {a_id}, "처음 만든 사람은 A 로 남고 B 가 덮어쓰지 않는다")
    check(len(view) == 5 and view[0]["relation"] == "used", "B 의 창은 'used' 관계로 보인다")
    check(len(exported) == 5, "내려받기는 내가 참조하는 원자만 준다")

    print("\n⑥ 복원(hydrate) — 파일이 날아가도 원자는 산다")
    import shutil

    shutil.rmtree(s.knowledge_dir, ignore_errors=True)
    tenancy.forget_hydration()
    n = tenancy.hydrate(s)
    check(n == 5, f"DB 에서 .md 5개를 복원했다 (={n})")
    files = sorted(p.name for p in (s.knowledge_dir / "atoms").glob("atm_*.md"))
    check(len(files) == 5, "파일이 실제로 돌아왔다")
    check(tenancy.hydrate(s) == 0, "두 번째 호출은 다시 쓰지 않는다(프로세스당 1회)")

    print("\n⑥-2 과금 단위는 보고서 1건")
    from geoguesshelper import quota

    class Req:                       # 최소한의 가짜 요청 — 쿠키와 헤더만 본다
        def __init__(self, cookie="", ip="1.2.3.4"):
            self.cookies = {quota.VISITOR_COOKIE: cookie} if cookie else {}
            self.headers = {"x-forwarded-for": ip}
            self.client = type("C", (), {"host": ip})()

    s.free_reports = 1
    s.anthropic_api_key = "sk-ant-server-for-free-tier"
    anon = Req(cookie="a" * 32)
    d = quota.decide(s, anon, None)
    check(d.allowed and d.plan == "anon", f"가입 전 첫 1건은 무료 {d.as_dict()}")
    with db.session_for(s) as ses:
        db.record_report(ses, anon_key="a" * 32, anon_ip=quota.ip_key(s, anon),
                         report_file="r1.html", cost_usd=0.12, atoms_new=5, atoms_reused=0)
    d = quota.decide(s, anon, None)
    check(not d.allowed and d.reason == "signup", f"그 다음은 가입 요구 {d.reason}")
    d = quota.decide(s, Req(cookie="b" * 32), None)     # 쿠키를 지운 같은 IP
    check(not d.allowed, "쿠키를 갈아도 같은 IP 면 이미 쓴 것으로 센다")
    d = quota.decide(s, Req(cookie="a" * 32, ip="9.9.9.9"), None)   # IP 만 바꿈
    check(not d.allowed, "IP 를 갈아도 같은 쿠키면 이미 쓴 것으로 센다")
    check(quota.decide(s, Req(cookie="c" * 32, ip="5.5.5.5"), None).allowed,
          "둘 다 처음인 방문자는 무료 1건을 받는다")

    with db.session_for(s) as ses:
        u_a = ses.get(db.User, a_id)
        check(quota.decide(s, anon, u_a).allowed, "회원의 첫 1건도 무료")
        db.record_report(ses, user_id=a_id, report_file="r2.html")
    with db.session_for(s) as ses:
        u_a = ses.get(db.User, a_id)
        u_a.plan = "free"
        d = quota.decide(s, anon, u_a)
        check(not d.allowed and d.reason == "pay", f"다 쓴 회원은 유료 안내 {d.reason}")
        u_a.plan = "pro"
        check(quota.decide(s, anon, ses.get(db.User, a_id)).allowed, "pro 는 한도가 없다")
        u_a.plan = "free"
    tok_byo = llm.set_credentials(llm.Creds("anthropic", "sk-ant-visitor", "byo"))
    try:
        with db.session_for(s) as ses:
            d = quota.decide(s, anon, ses.get(db.User, a_id))
        check(d.allowed and d.plan == "byo", "자기 키를 넣으면 한도가 없다")
    finally:
        llm.reset_credentials(tok_byo)
    s_solo = Settings()
    check(quota.decide(s_solo, anon, None).allowed, "단독 소유자 모드는 한도 자체가 없다")
    s.anthropic_api_key = ""

    print("\n⑦ 자격증명 컨텍스트")
    s3 = Settings()
    s3.anthropic_api_key = ""
    try:
        llm.current_credentials(s3)
        check(False, "키가 없으면 예외여야 한다")
    except llm.LLMUnavailable as exc:
        check("키" in str(exc), "키가 없으면 사용자에게 무엇을 하라고 말한다")
    tok = llm.set_credentials(llm.Creds("openai", "sk-test-123"))
    try:
        c = llm.current_credentials(s3)
        check(c.provider == "openai" and c.api_key == "sk-test-123",
              "컨텍스트의 자격증명이 우선한다")
    finally:
        llm.reset_credentials(tok)
    s4 = Settings()
    s4.anthropic_api_key = "sk-ant-server"
    check(llm.current_credentials(s4).label == "server",
          "컨텍스트가 비면 서버 키로 물러선다(단독 소유자 모드)")

    print("\n⑧ 구글 연결")
    with db.session_for(s) as ses:
        g = auth.upsert_google_user(ses, s, {"sub": "g-1", "email": "a@example.com",
                                             "email_verified": True, "name": "에이"})
        check(g.id == a_id, "같은 이메일의 기존 계정에 구글을 연결한다(새로 만들지 않는다)")
        check(g.google_sub == "g-1", "google_sub 를 기록한다")
    with db.session_for(ses2 := s) as ses:
        g2 = auth.upsert_google_user(ses, s, {"sub": "g-2", "email": "new@example.com",
                                              "email_verified": True})
        check(g2.id != a_id, "새 sub 는 새 계정을 만든다")
    with db.session_for(s) as ses:
        try:
            auth.upsert_google_user(ses, s, {"sub": "g-3", "email": "x@example.com",
                                             "email_verified": False})
            check(False, "미확인 이메일은 거부해야 한다")
        except auth.AuthError:
            check(True, "이메일 미확인 구글 계정은 거부한다")
    check(auth.take_state("없는상태") is False, "쓰지 않은 OAuth state 는 거부")
    st = auth.new_state()
    check(auth.take_state(st) is True and auth.take_state(st) is False,
          "state 는 한 번만 쓰인다(재사용 거부)")

    db.reset_engine()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'PASS' if not FAIL else 'FAIL'} — 실패 {len(FAIL)}건")
    for f in FAIL:
        print("   ·", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
