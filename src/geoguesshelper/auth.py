"""회원가입·로그인·구글 인증 — 그리고 "지금 누구인가".

설계 원칙 셋
  ① 단독 소유자 모드를 깨지 않는다. DATABASE_URL 이 없으면 이 모듈은 사실상 잠들어
     있고, current_user() 가 None 을 돌려주며 서버는 예전과 똑같이 동작한다.
     로컬에서 매일 쓰는 흐름이 이 변경 때문에 달라지면 안 된다.
  ② 세션은 서버에 있다(db.UserSession). 로그아웃과 정지가 **다음 요청부터** 들어야 한다 —
     남의 LLM 키를 들고 있는 서비스에서 이건 타협 대상이 아니다.
  ③ 구글은 있으면 쓰고 없으면 없는 대로. 클라이언트 ID 가 없으면 이메일 가입만 열린다.
"""
from __future__ import annotations

import hmac
import os
import secrets
import time
from typing import Any

from .config import Settings

SESSION_COOKIE = "ggh_session"
_SESSION_TTL_S = 60 * 60 * 24 * 30          # 30일
_OAUTH_STATE_TTL_S = 600.0


class AuthError(RuntimeError):
    """호출부가 4xx 로 바꿔 쓰는 사용자 오류."""


# ── 비밀번호 ──────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    """argon2 로 해싱한다. 없으면 표준 라이브러리 PBKDF2 로 물러선다.

    물러서는 경로를 남기는 이유 — 이 앱은 `--extra server` 없이도 임포트된다(테스트·CLI).
    거기서 ImportError 로 죽는 대신, 약하지만 표준적인 방식으로 계속 돈다.
    """
    if len(pw or "") < 8:
        raise AuthError("비밀번호는 8자 이상이어야 합니다.")
    try:
        from argon2 import PasswordHasher

        return PasswordHasher().hash(pw)
    except ModuleNotFoundError:
        import hashlib

        salt = secrets.token_bytes(16)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 240_000)
        return "pbkdf2$" + salt.hex() + "$" + dk.hex()


def verify_password(pw: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith("pbkdf2$"):
        import hashlib

        _, salt_hex, dk_hex = stored.split("$", 2)
        dk = hashlib.pbkdf2_hmac("sha256", (pw or "").encode("utf-8"),
                                 bytes.fromhex(salt_hex), 240_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    try:
        from argon2 import PasswordHasher
        from argon2.exceptions import VerifyMismatchError

        try:
            PasswordHasher().verify(stored, pw or "")
            return True
        except VerifyMismatchError:
            return False
        except Exception:  # noqa: BLE001 — 손상된 해시는 실패로 본다
            return False
    except ModuleNotFoundError:
        return False


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e or e.startswith("@") or e.endswith("@") or len(e) > 320:
        raise AuthError("이메일 형식이 올바르지 않습니다.")
    return e


# ── 세션 ──────────────────────────────────────────────────────────
def open_session(s, user, *, user_agent: str = "") -> str:
    from .db import UserSession

    token = secrets.token_urlsafe(32)
    s.add(UserSession(token=token, user_id=user.id, created=time.time(),
                      expires=time.time() + _SESSION_TTL_S, user_agent=(user_agent or "")[:300]))
    user.last_login = time.time()
    return token


def close_session(s, token: str) -> None:
    from .db import UserSession

    row = s.get(UserSession, token)
    if row is not None:
        s.delete(row)


def user_for_token(s, token: str):
    """토큰 → User. 만료·정지·없음은 전부 None."""
    from .db import User, UserSession

    if not token:
        return None
    row = s.get(UserSession, token)
    if row is None:
        return None
    if row.expires < time.time():
        s.delete(row)
        return None
    user = s.get(User, row.user_id)
    if user is None or user.disabled:
        return None
    return user


# ── 가입·로그인 ───────────────────────────────────────────────────
def signup(s, settings: Settings, *, email: str, password: str, name: str = ""):
    from .db import User

    e = normalize_email(email)
    if s.query(User).filter(User.email == e).first() is not None:
        raise AuthError("이미 가입된 이메일입니다. 로그인해 주세요.")
    user = User(email=e, password_hash=hash_password(password),
                display_name=(name or "").strip()[:200] or None,
                plan=_initial_plan(settings, e))
    s.add(user)
    s.flush()
    return user


def login(s, *, email: str, password: str):
    from .db import User

    e = normalize_email(email)
    user = s.query(User).filter(User.email == e).first()
    # 없는 계정과 틀린 비밀번호를 구분해 알려 주지 않는다(계정 열거 방지).
    if user is None or not verify_password(password, user.password_hash or ""):
        raise AuthError("이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.disabled:
        raise AuthError("정지된 계정입니다.")
    return user


def _initial_plan(settings: Settings, email: str) -> str:
    """관리자 이메일은 처음부터 pro — 운영자가 자기 도구를 못 쓰는 일이 없게."""
    return "pro" if email in (settings.admin_emails or []) else "free"


def upsert_google_user(s, settings: Settings, claims: dict):
    """구글 ID 토큰의 클레임 → User(없으면 생성, 있으면 연결).

    google_sub 가 정본이다 — 이메일은 바뀔 수 있고, 같은 사람이 이메일 가입 뒤 구글로
    들어오는 경우도 있으므로 그때는 **기존 계정에 붙인다**.
    """
    from .db import User

    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise AuthError("구글 응답에 계정 식별자(sub)가 없습니다.")
    if claims.get("email_verified") is False:
        raise AuthError("구글 계정의 이메일이 확인되지 않았습니다.")
    e = normalize_email(str(claims.get("email") or ""))

    user = s.query(User).filter(User.google_sub == sub).first()
    if user is None:
        user = s.query(User).filter(User.email == e).first()
        if user is not None:
            user.google_sub = sub                    # 같은 사람 — 기존 계정에 연결
        else:
            user = User(email=e, google_sub=sub, plan=_initial_plan(settings, e))
            s.add(user)
            s.flush()
    if not user.display_name and claims.get("name"):
        user.display_name = str(claims["name"])[:200]
    if user.disabled:
        raise AuthError("정지된 계정입니다.")
    return user


# ── 구글 OAuth ────────────────────────────────────────────────────
def google_enabled(settings: Settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri(settings: Settings, request_base: str = "") -> str:
    base = settings.public_base_url or request_base.rstrip("/")
    return f"{base}/api/auth/google/callback"


_states: dict[str, float] = {}


def new_state() -> str:
    """CSRF 상태값. 메모리에 두되 만료시킨다 — 프로세스가 하나인 배포를 전제한다."""
    now = time.time()
    for k, ts in list(_states.items()):
        if now - ts > _OAUTH_STATE_TTL_S:
            _states.pop(k, None)
    st = secrets.token_urlsafe(24)
    _states[st] = now
    return st


def take_state(state: str) -> bool:
    """한 번만 쓴다. 재사용은 거부."""
    ts = _states.pop(state or "", None)
    return ts is not None and (time.time() - ts) <= _OAUTH_STATE_TTL_S


def google_authorize_url(settings: Settings, *, state: str, request_base: str = "") -> str:
    from urllib.parse import urlencode

    q = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(settings, request_base),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(q)


async def google_exchange(settings: Settings, *, code: str, request_base: str = "") -> dict:
    """authorization code → id_token 클레임.

    id_token 의 서명을 직접 검증하지 않는 이유 — 토큰 엔드포인트는 **우리 클라이언트
    시크릿으로 인증한 TLS 연결**이고 그 응답을 그대로 쓴다. 서명 검증은 토큰을 제3자에게서
    받았을 때 필요하다. (그래도 aud/iss/exp 는 확인한다.)
    """
    from . import tls

    body = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri(settings, request_base),
        "grant_type": "authorization_code",
    }
    import httpx

    async with httpx.AsyncClient(verify=tls.httpx_verify("oauth2.googleapis.com"),
                                 timeout=20.0) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data=body)
    if r.status_code != 200:
        raise AuthError(f"구글 토큰 교환 실패({r.status_code}). 리디렉션 URI 등록을 확인하세요.")
    tok = r.json()
    claims = _decode_id_token(str(tok.get("id_token") or ""))
    if claims.get("aud") != settings.google_client_id:
        raise AuthError("구글 토큰의 대상(aud)이 이 앱이 아닙니다.")
    if str(claims.get("iss", "")).rstrip("/") not in ("https://accounts.google.com", "accounts.google.com"):
        raise AuthError("구글 토큰의 발급자(iss)가 올바르지 않습니다.")
    if float(claims.get("exp") or 0) < time.time():
        raise AuthError("구글 토큰이 만료되었습니다. 다시 시도해 주세요.")
    return claims


def _decode_id_token(jwt: str) -> dict[str, Any]:
    import base64
    import json

    parts = jwt.split(".")
    if len(parts) != 3:
        raise AuthError("구글 id_token 형식이 올바르지 않습니다.")
    pad = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(pad).decode("utf-8"))


def cookie_kwargs(settings: Settings) -> dict:
    """세션 쿠키 옵션. 로컬 http 에서도 동작해야 하므로 secure 는 공개 주소일 때만."""
    https = (settings.public_base_url or "").startswith("https://")
    return {"httponly": True, "samesite": "lax", "secure": https,
            "max_age": _SESSION_TTL_S, "path": "/"}


__all__ = [
    "SESSION_COOKIE", "AuthError", "hash_password", "verify_password", "normalize_email",
    "open_session", "close_session", "user_for_token", "signup", "login",
    "upsert_google_user", "google_enabled", "google_authorize_url", "google_exchange",
    "new_state", "take_state", "redirect_uri", "cookie_kwargs",
]
