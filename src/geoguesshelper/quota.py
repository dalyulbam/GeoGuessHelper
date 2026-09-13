"""무료 1건과 그 다음 — 누가 무엇을 몇 건까지 받는가.

과금 단위는 **보고서 1건**이다. 원자가 아니다. 원자는 공용 지식이라(tenancy.py 머리말)
회원마다 세어서 막을 대상이 아니고, 애초에 새 원자를 만드는 비용은 그 보고서를 만드는
비용의 일부다. 그래서 세는 곳은 한 군데, 보고서다.

규칙은 셋뿐이다.

  ① 자기 키를 넣은 사람은 한도가 없다 — 그 사람의 돈으로 그 사람의 호출을 한다.
     한도는 **운영자 키를 지키는 장치**이지 기능 제한이 아니다.
  ② 가입 전 방문자는 운영자 키로 1건. 그 1건이 이 제품이 무엇인지 보여 주는 몫이다.
  ③ 그 다음은 가입, 가입 후 무료분을 다 쓰면 유료.

방문자를 어떻게 알아보나 — 쿠키와 IP 를 **둘 다** 남기고 둘 중 하나라도 걸리면 쓴 것으로
센다. 쿠키만 보면 시크릿 창으로, IP 만 보면 모바일 데이터 전환으로 그냥 뚫린다. 둘 다
우회하는 사람은 이 계층이 막지 못한다 — 그건 사실이고, 그래서 진짜 방어선은 여기가 아니라
운영자 키의 **일일 지출 상한**이다. 이 문단을 지우지 말 것. 지우면 다음 사람이 이 장치를
실제보다 튼튼한 것으로 오해한다.

IP 는 그대로 두지 않고 비밀로 소금 친 해시로 바꿔서 저장한다. IPv4 는 전수 대입이 가능해
소금 없는 해시는 원문이나 마찬가지다.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from .config import Settings

VISITOR_COOKIE = "ggh_v"
_COOKIE_MAX_AGE = 400 * 24 * 3600       # 브라우저가 받아 주는 상한(약 400일)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""        # "" | "signup" | "pay" | "nokey"
    message: str = ""
    used: int = 0
    free: int = 0
    plan: str = "anon"

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason, "message": self.message,
                "used": self.used, "free": self.free, "plan": self.plan,
                "remaining": max(0, self.free - self.used)}


def new_visitor() -> str:
    return secrets.token_hex(16)


def visitor_of(request) -> str:
    v = (request.cookies.get(VISITOR_COOKIE) or "").strip()
    return v if v.isalnum() and 8 <= len(v) <= 64 else ""


def client_ip(request) -> str:
    """프록시 뒤에서의 진짜 주소. Railway·대부분의 PaaS 가 X-Forwarded-For 를 붙인다.

    **첫 번째** 홉을 쓴다 — 뒤쪽은 중간 프록시들이고, 앞쪽은 클라이언트가 위조할 수
    있지만 그건 이 장치가 애초에 막지 못하는 부류(위 머리말)라 더 복잡하게 만들지 않는다.
    """
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    return getattr(getattr(request, "client", None), "host", "") or ""


def ip_key(settings: Settings, request) -> str:
    ip = client_ip(request)
    if not ip:
        return ""
    salt = settings.key_enc_secret or settings.database_url or "geoguesshelper"
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:32]


def has_own_key(settings: Settings, user) -> bool:
    """이 요청이 자기 키로 도는가 — 방문자가 헤더로 넣었거나, 회원이 저장해 뒀거나."""
    from . import llm

    c = llm.current_credentials_or_none()
    if c is not None and c.api_key:
        return True
    if user is None or not settings.multi_user:
        return False
    from . import db

    with db.session_for(settings) as s:
        return s.query(db.UserKey).filter(db.UserKey.user_id == user.id).first() is not None


def decide(settings: Settings, request, user) -> Decision:
    """이 보고서 요청을 받아 줄 것인가."""
    if not settings.multi_user:
        return Decision(True, plan="owner")          # 단독 소유자 모드 — 한도 없음
    if has_own_key(settings, user):
        return Decision(True, plan="byo")            # 자기 돈으로 돈다
    plan = getattr(user, "plan", "") or "anon"
    if plan == "pro":
        return Decision(True, plan="pro")

    free = int(settings.free_reports)
    from . import db

    with db.session_for(settings) as s:
        if user is not None:
            used = db.report_count(s, user_id=user.id)
        else:
            used = db.report_count(s, anon_key=visitor_of(request),
                                   anon_ip=ip_key(settings, request))
    if used < free:
        if user is None and not (settings.anthropic_api_key or settings.openai_api_key):
            return Decision(False, "nokey", "지금은 서버에 준비된 키가 없습니다. "
                            "본인 API 키를 넣으면 바로 쓸 수 있습니다.",
                            used, free, plan)
        return Decision(True, plan=plan, used=used, free=free)

    if user is None:
        msg = (f"무료 체험 {free}건을 다 쓰셨습니다. 가입하시면 이어서 쓸 수 있고, "
               "본인 API 키를 넣으면 한도 없이 쓸 수 있습니다.")
        return Decision(False, "signup", msg, used, free, plan)
    msg = (f"무료 {free}건을 다 쓰셨습니다. 유료 전환 또는 본인 API 키 등록이 필요합니다.")
    return Decision(False, "pay", msg, used, free, plan)


__all__ = ["Decision", "VISITOR_COOKIE", "new_visitor", "visitor_of", "client_ip",
           "ip_key", "has_own_key", "decide", "_COOKIE_MAX_AGE"]
