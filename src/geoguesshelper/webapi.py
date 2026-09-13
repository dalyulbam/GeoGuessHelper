"""다중 사용자 라우트 — 인증·내 키·사용량·관리.

server.py 에 직접 넣지 않고 모듈을 나눈 이유: server.py 는 이미 1,200줄이고 이 기능은
**켜지지 않을 수도** 있다(DATABASE_URL 이 없으면 통째로 잠든다). 경계를 파일로 그어야
단독 소유자 모드가 이 코드를 한 줄도 지나지 않는다는 것이 눈으로 확인된다.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import auth, db, llm, secretbox, tenancy
from .config import Settings

PROVIDERS = ("anthropic", "openai")
_PROVIDER_LABEL = {"anthropic": "Claude (Anthropic)", "openai": "ChatGPT (OpenAI)"}


def current_user(settings: Settings, request: Request):
    """요청의 회원. 단독 소유자 모드거나 미로그인이면 None."""
    if not settings.multi_user:
        return None
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    if not token:
        return None
    try:
        with db.session_for(settings) as s:
            u = auth.user_for_token(s, token)
            return u
    except Exception:  # noqa: BLE001 — 인증 조회 실패가 페이지 자체를 막으면 안 된다
        return None


def require_user(settings: Settings, request: Request):
    u = current_user(settings, request)
    if u is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return u


def require_admin(settings: Settings, request: Request):
    u = require_user(settings, request)
    if u.email.lower() not in (settings.admin_emails or []):
        raise HTTPException(status_code=403, detail="관리자만 가능합니다.")
    return u


def credentials_for(settings: Settings, user) -> llm.Creds | None:
    """그 회원이 넣어 둔 키 → Creds. 없으면 None.

    두 제공자를 다 넣었으면 anthropic 을 먼저 쓴다 — 이 앱의 프롬프트·도구 스키마가
    거기에 맞춰져 있고, 서버측 web_search 가 리서치에 필요하기 때문이다.
    """
    if user is None:
        return None
    with db.session_for(settings) as s:
        rows = {r.provider: r for r in s.query(db.UserKey).filter(
            db.UserKey.user_id == user.id).all()}
        for p in PROVIDERS:
            r = rows.get(p)
            if r is None:
                continue
            try:
                plain = secretbox.open_(settings, r.ciphertext, user_id=user.id, provider=p)
            except Exception:  # noqa: BLE001 — 비밀이 바뀌면 복호화가 안 된다
                continue
            r.last_used = time.time()
            return llm.Creds(p, plain, r.hint)
    return None


def build_router(settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api")

    def _base(request: Request) -> str:
        return settings.public_base_url or str(request.base_url).rstrip("/")

    # ── 상태 ─────────────────────────────────────────────────────
    @r.get("/health")
    async def health():
        """배포 헬스체크. DB 가 붙었는지까지 본다 — 뜬 것과 동작하는 것은 다르다."""
        info = db.healthy(settings)
        code = 200 if info.get("ok") else 503
        return JSONResponse({"status": "ok" if info.get("ok") else "degraded", **info},
                            status_code=code)

    @r.get("/me")
    async def me(request: Request):
        u = current_user(settings, request)
        if u is None:
            return JSONResponse({
                "authenticated": False,
                "multiUser": settings.multi_user,
                "googleEnabled": auth.google_enabled(settings),
                "keyStorage": bool(settings.key_enc_secret),
            })
        with db.session_for(settings) as s:
            keys = [{"provider": k.provider, "label": _PROVIDER_LABEL.get(k.provider, k.provider),
                     "hint": k.hint, "created": k.created}
                    for k in s.query(db.UserKey).filter(db.UserKey.user_id == u.id).all()]
        return JSONResponse({
            "authenticated": True, "multiUser": True,
            "googleEnabled": auth.google_enabled(settings),
            "keyStorage": bool(settings.key_enc_secret),
            "user": u.public(settings),
            "keys": keys,
            "usage": tenancy.usage(settings, u.id, plan=u.plan),
        })

    # ── 가입·로그인 ───────────────────────────────────────────────
    @r.post("/auth/signup")
    async def signup(payload: dict, request: Request):
        try:
            with db.session_for(settings) as s:
                u = auth.signup(s, settings, email=payload.get("email", ""),
                                password=payload.get("password", ""),
                                name=payload.get("name", ""))
                token = auth.open_session(s, u, user_agent=request.headers.get("user-agent", ""))
                body = u.public(settings)
        except auth.AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        resp = JSONResponse({"ok": True, "user": body})
        resp.set_cookie(auth.SESSION_COOKIE, token, **auth.cookie_kwargs(settings, request))
        return resp

    @r.post("/auth/login")
    async def login(payload: dict, request: Request):
        try:
            with db.session_for(settings) as s:
                u = auth.login(s, email=payload.get("email", ""),
                               password=payload.get("password", ""))
                token = auth.open_session(s, u, user_agent=request.headers.get("user-agent", ""))
                body = u.public(settings)
        except auth.AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        resp = JSONResponse({"ok": True, "user": body})
        resp.set_cookie(auth.SESSION_COOKIE, token, **auth.cookie_kwargs(settings, request))
        return resp

    @r.post("/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(auth.SESSION_COOKIE, "")
        if token:
            with db.session_for(settings) as s:
                auth.close_session(s, token)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    # ── 구글 ─────────────────────────────────────────────────────
    @r.get("/auth/google/start")
    async def google_start(request: Request):
        if not auth.google_enabled(settings):
            raise HTTPException(status_code=404, detail="구글 로그인이 설정되지 않았습니다.")
        url = auth.google_authorize_url(settings, state=auth.new_state(),
                                        request_base=_base(request))
        return RedirectResponse(url, status_code=302)

    @r.get("/auth/google/callback")
    async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error:
            return RedirectResponse(f"/?auth_error={error}", status_code=302)
        if not auth.take_state(state):
            return RedirectResponse("/?auth_error=state", status_code=302)
        try:
            claims = await auth.google_exchange(settings, code=code, request_base=_base(request))
            with db.session_for(settings) as s:
                u = auth.upsert_google_user(s, settings, claims)
                token = auth.open_session(s, u, user_agent=request.headers.get("user-agent", ""))
        except auth.AuthError as exc:
            return RedirectResponse(f"/?auth_error={exc}", status_code=302)
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(auth.SESSION_COOKIE, token, **auth.cookie_kwargs(settings, request))
        return resp

    # ── 내 키 ────────────────────────────────────────────────────
    @r.post("/keys")
    async def put_key(payload: dict, request: Request):
        """API 키 저장. 제공자는 키 모양으로 알아본다(사용자가 고르지 않아도 되게)."""
        u = require_user(settings, request)
        raw = str(payload.get("key") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="키가 비어 있습니다.")
        provider = str(payload.get("provider") or "").strip() or secretbox.provider_of(raw)
        if provider not in PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail="키 종류를 알 수 없습니다. Claude 키는 sk-ant-…, ChatGPT 키는 sk-… 입니다.")
        ok, why = llm.verify_key(settings, provider, raw)
        if not ok:
            raise HTTPException(status_code=400, detail=why)
        try:
            sealed = secretbox.seal(settings, raw, user_id=u.id, provider=provider)
        except secretbox.SecretUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        hint = secretbox.mask(raw)
        with db.session_for(settings) as s:
            row = s.query(db.UserKey).filter(db.UserKey.user_id == u.id,
                                             db.UserKey.provider == provider).first()
            if row is None:
                s.add(db.UserKey(user_id=u.id, provider=provider, ciphertext=sealed, hint=hint))
            else:
                row.ciphertext, row.hint, row.created = sealed, hint, time.time()
        return JSONResponse({"ok": True, "provider": provider,
                             "label": _PROVIDER_LABEL[provider], "hint": hint})

    @r.delete("/keys/{provider}")
    async def delete_key(provider: str, request: Request):
        u = require_user(settings, request)
        with db.session_for(settings) as s:
            row = s.query(db.UserKey).filter(db.UserKey.user_id == u.id,
                                             db.UserKey.provider == provider).first()
            if row is not None:
                s.delete(row)
        return JSONResponse({"ok": True})

    # ── 내 지식 ──────────────────────────────────────────────────
    @r.get("/my/knowledge")
    async def my_knowledge(request: Request):
        u = require_user(settings, request)
        tenancy.hydrate(settings, u.id)
        return JSONResponse(tenancy.usage(settings, u.id, plan=u.plan))

    @r.get("/my/knowledge/export")
    async def my_export(request: Request):
        """내려받기 — 저장 한도에 걸린 회원도 자기 것을 가져갈 수 있어야 한다."""
        u = require_user(settings, request)
        with db.session_for(settings) as s:
            rows = db.export_atoms(s, u.id)
        return JSONResponse({"user": u.email, "count": len(rows), "atoms": rows},
                            headers={"Content-Disposition":
                                     'attachment; filename="geoguesshelper-atoms.json"'})

    # ── 관리 ─────────────────────────────────────────────────────
    @r.get("/admin/users")
    async def admin_users(request: Request):
        require_admin(settings, request)
        with db.session_for(settings) as s:
            users = s.query(db.User).order_by(db.User.created.desc()).limit(500).all()
            out = [{**u.public(settings), "atoms": db.atom_count(s, u.id)} for u in users]
        return JSONResponse({"users": out, "count": len(out)})

    @r.post("/admin/users/{user_id}/plan")
    async def admin_set_plan(user_id: str, payload: dict, request: Request):
        """등급 부여 — 결제 연동 전까지 운영자가 수동으로 준다."""
        require_admin(settings, request)
        plan = str(payload.get("plan") or "").strip().lower()
        if plan not in ("free", "pro"):
            raise HTTPException(status_code=400, detail="plan 은 free 또는 pro 여야 합니다.")
        with db.session_for(settings) as s:
            u = s.get(db.User, user_id)
            if u is None:
                raise HTTPException(status_code=404, detail="없는 회원입니다.")
            u.plan = plan
            body = u.public(settings)
        return JSONResponse({"ok": True, "user": body})

    return r


__all__ = ["build_router", "current_user", "require_user", "require_admin",
           "credentials_for", "PROVIDERS"]
