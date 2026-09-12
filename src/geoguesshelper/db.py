"""다중 사용자 저장소 — 회원·세션·API 키·원자.

왜 이 모듈이 생겼나
    이 앱은 `docs/knowledge/*.md` 파일 저장소 하나를 쓰는 **단독 사용자** 도구였다.
    여러 사람이 쓰려면 두 가지가 필요하다: 누가 누구인지(회원), 그리고 남의 원자가
    내 회상에 섞이지 않는 것(격리). 여기서 그 둘을 맡는다.

경계 — 파일 저장소를 대체하지 않는다
    recall·molecule·observe 는 전부 파일을 읽는 기존 코드다. 그것을 다시 쓰는 것은
    이 변경의 범위가 아니고, 다시 썼다면 260908 에 고친 그래프 수정들을 통째로 위험에
    빠뜨렸을 것이다. 그래서 구조는 이렇다:

        회원별 파일 저장소   data/users/<uid>/knowledge/   ← 엔진이 읽고 쓰는 곳
        DB                  atoms 테이블                   ← 살아남는 곳(정본)

    저장은 **양쪽에 쓴다**(write-through). 프로세스가 그 회원의 저장소를 처음 만질 때
    DB 에서 .md 를 복원한다(hydrate). Railway 처럼 파일시스템이 휘발성이어도 원자는 산다.

스키마를 JSON 한 칸으로 두는 이유
    Atom 은 필드가 28개이고 앞으로도 는다(category 는 260830 에, hits/misses 는 260908 에
    생겼다). 컬럼으로 펴면 필드가 늘 때마다 마이그레이션이 필요하고, 파일 형식과 DB 형식이
    갈라진다. 그래서 본문은 `data` JSON 에 통째로 넣고, **질의에 실제로 쓰는 것만** 컬럼으로
    꺼낸다(cell·scope·kind·status·updated). 파일 ↔ DB 왕복이 무손실이다.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, create_engine, func, select)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


def _uid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=_uid)
    email = Column(String(320), unique=True, nullable=False, index=True)
    # 비밀번호 가입이 아니면 비어 있다(구글로만 가입한 회원).
    password_hash = Column(Text, nullable=True)
    # 구글 계정의 안정 식별자. 이메일은 바뀔 수 있으므로 이쪽이 정본이다.
    google_sub = Column(String(64), unique=True, nullable=True, index=True)
    display_name = Column(String(200), nullable=True)
    plan = Column(String(16), nullable=False, default="free")     # free | pro
    created = Column(Float, nullable=False, default=time.time)
    last_login = Column(Float, nullable=True)
    disabled = Column(Boolean, nullable=False, default=False)

    keys = relationship("UserKey", back_populates="user", cascade="all, delete-orphan")

    @property
    def is_pro(self) -> bool:
        return self.plan == "pro"

    def public(self, settings: Settings | None = None) -> dict:
        """브라우저로 내려보낼 요약. 키는 **절대** 포함하지 않는다."""
        admin = bool(settings and self.email.lower() in (settings.admin_emails or []))
        return {
            "id": self.id, "email": self.email, "name": self.display_name or self.email.split("@")[0],
            "plan": self.plan, "isPro": self.is_pro, "isAdmin": admin,
            "google": bool(self.google_sub), "created": self.created,
        }


class UserSession(Base):
    """서버측 세션. JWT 가 아니라 DB 세션인 이유 — **즉시 무효화**가 가능해야 한다.

    사용자의 LLM 키를 대신 들고 있는 서비스에서, 로그아웃과 계정 정지가 다음 요청부터
    확실히 듣지 않으면 곤란하다.
    """
    __tablename__ = "sessions"

    token = Column(String(64), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created = Column(Float, nullable=False, default=time.time)
    expires = Column(Float, nullable=False)
    user_agent = Column(String(300), nullable=True)


class UserKey(Base):
    """회원이 넣은 LLM API 키 — **암호문만** 저장한다.

    provider 는 "anthropic" | "openai". 평문은 어디에도 남기지 않고, 로그·응답에는
    마스킹된 꼬리 네 글자만 나간다(secretbox.mask).
    """
    __tablename__ = "user_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_provider"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(32), nullable=False)
    ciphertext = Column(Text, nullable=False)
    hint = Column(String(16), nullable=False, default="")     # 마스킹 표시용 꼬리
    created = Column(Float, nullable=False, default=time.time)
    last_used = Column(Float, nullable=True)

    user = relationship("User", back_populates="keys")


class AtomRow(Base):
    """회원의 원자 한 개.

    본문은 data(JSON) 에 통째로 — Atom.meta() 와 같은 모양이라 파일 ↔ DB 가 무손실이다.
    컬럼으로 꺼낸 것은 **질의에 실제로 쓰는 것**뿐이다.
    """
    __tablename__ = "atoms"
    __table_args__ = (
        UniqueConstraint("user_id", "atom_id", name="uq_user_atom"),
        Index("ix_atoms_user_cell", "user_id", "cell"),
        Index("ix_atoms_user_updated", "user_id", "updated"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    atom_id = Column(String(32), nullable=False)
    cell = Column(String(16), nullable=True)
    scope = Column(String(24), nullable=True)
    kind = Column(String(24), nullable=True)
    status = Column(String(16), nullable=True)
    updated = Column(Float, nullable=False, default=time.time)
    body_md = Column(Text, nullable=False)      # .md 전문 — 복원이 문자 단위로 같아야 한다
    data = Column(JSON, nullable=False)         # Atom.meta()


_engine = None
_Session: sessionmaker | None = None


def engine_for(settings: Settings):
    """엔진 1개를 프로세스 수명 동안 재사용한다. database_url 이 없으면 None."""
    global _engine, _Session
    if not settings.database_url:
        return None
    if _engine is None:
        kw: dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if settings.database_url.startswith("sqlite"):
            # SQLite 는 기본이 스레드 귀속이다. uvicorn 의 워커 스레드에서 같은 연결을
            # 쓰므로 그 검사를 끈다(쓰기는 세션 단위로 직렬화된다).
            kw["connect_args"] = {"check_same_thread": False}
        else:
            kw["pool_size"] = 5
            kw["max_overflow"] = 5
        _engine = create_engine(settings.database_url, **kw)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(_engine)
    return _engine


@contextmanager
def session_for(settings: Settings) -> Iterator[Session]:
    """트랜잭션 한 벌. 예외가 나면 롤백한다."""
    if engine_for(settings) is None or _Session is None:
        raise RuntimeError("DATABASE_URL 이 설정되지 않았다 — 단독 소유자 모드다.")
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def reset_engine() -> None:
    """테스트가 다른 DB 로 갈아탈 때. 운영 경로에서는 쓰지 않는다."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


# ── 회원 원자 ────────────────────────────────────────────────────
def atom_count(s: Session, user_id: str) -> int:
    return int(s.execute(
        select(func.count()).select_from(AtomRow).where(AtomRow.user_id == user_id)
    ).scalar_one())


def upsert_atom(s: Session, user_id: str, *, atom_id: str, meta: dict, body_md: str) -> str:
    """원자 하나를 회원 소유로 저장한다. 반환 "inserted" | "updated"."""
    row = s.execute(
        select(AtomRow).where(AtomRow.user_id == user_id, AtomRow.atom_id == atom_id)
    ).scalar_one_or_none()
    fields = {
        "cell": meta.get("cell"), "scope": meta.get("scope"), "kind": meta.get("kind"),
        "status": meta.get("status"), "updated": float(meta.get("updated") or time.time()),
        "body_md": body_md, "data": meta,
    }
    if row is None:
        s.add(AtomRow(user_id=user_id, atom_id=atom_id, **fields))
        return "inserted"
    for k, v in fields.items():
        setattr(row, k, v)
    return "updated"


def all_atom_rows(s: Session, user_id: str) -> list[AtomRow]:
    return list(s.execute(
        select(AtomRow).where(AtomRow.user_id == user_id).order_by(AtomRow.updated)
    ).scalars())


def export_atoms(s: Session, user_id: str) -> list[dict]:
    """내려받기용 — 무료 회원이 자기 원자를 가져갈 수 있어야 한다."""
    return [{"id": r.atom_id, "meta": r.data, "md": r.body_md} for r in all_atom_rows(s, user_id)]


def healthy(settings: Settings) -> dict:
    """DB 가 실제로 응답하는가 — /api/health 가 쓴다."""
    if not settings.database_url:
        return {"mode": "single-user", "ok": True}
    try:
        with session_for(settings) as s:
            n = int(s.execute(select(func.count()).select_from(User)).scalar_one())
        return {"mode": "multi-user", "ok": True, "users": n,
                "driver": settings.database_url.split("://", 1)[0]}
    except Exception as exc:  # noqa: BLE001 — 헬스체크가 예외로 죽으면 안 된다
        return {"mode": "multi-user", "ok": False, "error": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "Base", "User", "UserSession", "UserKey", "AtomRow",
    "engine_for", "session_for", "reset_engine",
    "atom_count", "upsert_atom", "all_atom_rows", "export_atoms", "healthy",
]
