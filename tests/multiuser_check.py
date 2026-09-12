"""다중 사용자 계층 검사 — 가입·세션·키 보관·저장소 격리·무료 상한.

이 파일이 있는 이유: 여기서 조용히 틀리면 **남의 키로 남의 돈을 쓰거나 남의 원자를
읽는다**. 이 앱에서 가장 나쁜 실패이고, 화면으로는 티가 안 난다.

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
    s.free_atom_limit = 3
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

    print("\n④ 저장소 격리")
    sa = tenancy.settings_for_user(s, a_id)
    sb = tenancy.settings_for_user(s, b_id)
    check(sa.knowledge_dir != sb.knowledge_dir, "회원마다 지식 저장소가 다르다")
    check(str(a_id) in str(sa.knowledge_dir), "경로에 그 회원의 id 가 들어간다")
    check(sa.captures_dir != sb.captures_dir and sa.reports_dir != sb.reports_dir,
          "캡처·보고서도 분리된다")
    check(s.knowledge_dir != sa.knowledge_dir, "원본 Settings 는 바뀌지 않는다")
    try:
        tenancy.settings_for_user(s, "../../etc")
        check(False, "경로 조작 id 는 거부돼야 한다")
    except ValueError:
        check(True, "경로가 될 수 없는 id 를 거부한다")

    print("\n⑤ write-through 와 무료 상한")
    from geoguesshelper.knowledge import Atom

    def put(us, n: int, who: str) -> None:
        d = us.knowledge_dir / "atoms"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            a = Atom(id=f"atm_{who}{i:08x}", layer="history", scope="country",
                     title=f"{who} 원자 {i}", body=f"{who} 본문 {i}",
                     entities=[who], tags=["t"], cell="wydm9qq")
            (d / f"{a.id}.md").write_text(a.to_md(), encoding="utf-8")

    put(sa, 5, "aa")            # 무료 상한 3 < 5
    r = tenancy.sync_atoms(s, a_id, plan="free")
    check(r["synced"] == 3 and r["skipped"] == 2,
          f"무료는 상한 3 에서 멈추고 넘긴 수를 알린다 {r}")
    with db.session_for(ses_s := s) as ses:
        check(db.atom_count(ses, a_id) == 3, "DB 에 3개만 들어갔다")
        check(db.atom_count(ses, b_id) == 0, "B 의 저장소는 비어 있다(격리)")
    put(sb, 2, "bb")
    tenancy.sync_atoms(s, b_id, plan="free")
    with db.session_for(s) as ses:
        ids_a = {r.atom_id for r in db.all_atom_rows(ses, a_id)}
        ids_b = {r.atom_id for r in db.all_atom_rows(ses, b_id)}
    check(ids_a.isdisjoint(ids_b), "두 회원의 원자가 섞이지 않는다")
    check(all(i.startswith("atm_bb") for i in ids_b), "B 는 자기 원자만 갖는다")

    with db.session_for(s) as ses:
        ses.get(db.User, a_id).plan = "pro"
    r2 = tenancy.sync_atoms(s, a_id, plan="pro")
    check(r2["skipped"] == 0, f"pro 는 상한이 없다 {r2}")
    with db.session_for(s) as ses:
        check(db.atom_count(ses, a_id) == 5, "pro 로 올리면 나머지도 올라간다")

    print("\n⑥ 복원(hydrate) — 파일이 날아가도 원자는 산다")
    import shutil

    shutil.rmtree(sa.knowledge_dir, ignore_errors=True)
    tenancy.forget_hydration(a_id)
    n = tenancy.hydrate(s, a_id)
    check(n == 5, f"DB 에서 .md 5개를 복원했다 (={n})")
    files = sorted(p.name for p in (sa.knowledge_dir / "atoms").glob("atm_*.md"))
    check(len(files) == 5, "파일이 실제로 돌아왔다")
    check(tenancy.hydrate(s, a_id) == 0, "두 번째 호출은 다시 쓰지 않는다")

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
