"""다중 사용자 저장소 — 회원·세션·API 키·원자.

왜 이 모듈이 생겼나
    이 앱은 `docs/knowledge/*.md` 파일 저장소 하나를 쓰는 **단독 사용자** 도구였다.
    여러 사람이 쓰려면 두 가지가 필요하다: 누가 누구인지(회원), 그리고 남의 원자가
    내 회상에 섞이지 않는 것(격리). 여기서 그 둘을 맡는다.

지식은 하나다 — 회원마다 나누지 않는다 (260913 정정)
    처음에는 회원별로 저장소를 복제했다. 그건 틀렸다. `atom_id()` 는 **내용 주소**라
    같은 사실이면 누가 발견했든 같은 id 가 나온다 — 중복 저장이 구조적으로 불가능하다.
    회원마다 사본을 두면 그 성질을 스스로 깨고, 사용자가 늘수록 같은 사실이 N벌로 불어난다.

    그래서 구조는 이렇다:

        공용 파일 저장소   docs/knowledge/     ← 엔진(recall·molecule·observe)이 읽고 쓰는 곳
        atoms 테이블       원자 1행 = 사실 1개  ← 살아남는 곳(정본). user_id 없음
        atom_refs 테이블   (회원 ↔ 원자) 참조   ← "이 사람이 만든/쓴 원자"

    지식의 총량은 회원 수와 무관하다. 늘어나는 것은 **참조**뿐이고, 그건 행 하나다.
    사용자는 자기 참조와 그 이웃만 본다 — 저장소가 갈라지는 것이 아니라 **보는 창이** 다르다.

    파일 저장소를 DB 로 갈아엎지 않는 이유는 그대로다: recall·분자·관측이 전부 파일을
    읽고, 그 위에 260908 의 그래프 수정이 얹혀 있다. DB 는 영속과 참조를 맡는다.

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
                        String, Text, UniqueConstraint, create_engine, func, or_, select)
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
    """원자 한 개 — **전체에서 하나**. 소유자가 없다.

    같은 사실을 두 회원이 각각 발견해도 atom_id 가 같으므로 행은 하나다. 그래서 회원이
    늘어도 지식의 총량은 그대로다(늘어나는 것은 atom_refs 의 행뿐이다).

    본문은 data(JSON) 에 통째로 — Atom.meta() 와 같은 모양이라 파일 ↔ DB 가 무손실이다.
    컬럼으로 꺼낸 것은 **질의에 실제로 쓰는 것**뿐이다.
    """
    __tablename__ = "atoms"
    __table_args__ = (
        UniqueConstraint("atom_id", name="uq_atom"),
        Index("ix_atoms_cell", "cell"),
        Index("ix_atoms_updated", "updated"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    atom_id = Column(String(32), nullable=False)
    # 누가 **처음** 만들었나. 소유권이 아니라 내력이다 — 생성 비용을 누가 치렀는지.
    first_user_id = Column(String(32), nullable=True, index=True)
    cell = Column(String(16), nullable=True)
    scope = Column(String(24), nullable=True)
    kind = Column(String(24), nullable=True)
    status = Column(String(16), nullable=True)
    updated = Column(Float, nullable=False, default=time.time)
    body_md = Column(Text, nullable=False)      # .md 전문 — 복원이 문자 단위로 같아야 한다
    data = Column(JSON, nullable=False)         # Atom.meta()


class AtomRef(Base):
    """회원 ↔ 원자 참조. 사용자가 보는 **창**을 정의한다.

    relation:
      created  이 회원의 보고서가 그 원자를 처음 만들었다(생성 비용을 치른 쪽)
      used     이 회원의 보고서가 그 원자를 근거로 썼다(회상되어 쓰인 것)

    같은 회원이 같은 원자를 여러 번 써도 행은 relation 당 하나다 — 횟수는 uses 로 센다.
    """
    __tablename__ = "atom_refs"
    __table_args__ = (
        UniqueConstraint("user_id", "atom_id", "relation", name="uq_ref"),
        Index("ix_refs_user", "user_id", "created"),
        Index("ix_refs_atom", "atom_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    atom_id = Column(String(32), nullable=False)
    relation = Column(String(16), nullable=False, default="used")
    report_file = Column(String(300), nullable=True)     # 어느 보고서에서
    uses = Column(Integer, nullable=False, default=1)
    created = Column(Float, nullable=False, default=time.time)


class ReportRow(Base):
    """회원이 만든 보고서 한 건 — 과금의 단위다(무료 1건 → 이후 유료)."""
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_user", "user_id", "created"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    # 비회원 무료 1건을 추적하는 지문. 쿠키와 IP 를 **따로** 남긴다 — 하나만 남기면
    # 쿠키를 지우거나(IP 로 잡아야 한다) 망을 바꾸면(쿠키로 잡아야 한다) 그대로 뚫린다.
    # 둘 다 우회하는 방문자는 막지 못한다. 그건 이 계층이 할 수 있는 일이 아니다.
    anon_key = Column(String(64), nullable=True, index=True)
    anon_ip = Column(String(64), nullable=True, index=True)
    report_file = Column(String(300), nullable=True)
    label = Column(String(200), nullable=True)
    # 이 보고서의 실원가와 재사용 이득 — 가격을 감이 아니라 숫자로 정하기 위해 남긴다.
    cost_usd = Column(Float, nullable=False, default=0.0)
    atoms_new = Column(Integer, nullable=False, default=0)
    atoms_reused = Column(Integer, nullable=False, default=0)
    billed = Column(Boolean, nullable=False, default=False)
    created = Column(Float, nullable=False, default=time.time)


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


# ── 공유 원자 + 회원 참조 ────────────────────────────────────────
def atom_total(s: Session) -> int:
    """지식의 총량 — **회원 수와 무관하다**."""
    return int(s.execute(select(func.count()).select_from(AtomRow)).scalar_one())


def ref_count(s: Session, user_id: str) -> int:
    """이 회원이 보는 창의 크기(참조 수). 원자 수가 아니다."""
    return int(s.execute(
        select(func.count()).select_from(AtomRef).where(AtomRef.user_id == user_id)
    ).scalar_one())


def upsert_atom(s: Session, *, atom_id: str, meta: dict, body_md: str,
                first_user_id: str | None = None) -> str:
    """원자를 공용 저장소에 넣는다. 반환 "inserted" | "updated".

    소유자를 받지 않는다 — 원자는 전체에서 하나다. first_user_id 는 **처음** 만든
    경우에만 기록되고, 이미 있으면 건드리지 않는다(내력은 덮어쓰지 않는다).
    """
    row = s.execute(select(AtomRow).where(AtomRow.atom_id == atom_id)).scalar_one_or_none()
    fields = {
        "cell": meta.get("cell"), "scope": meta.get("scope"), "kind": meta.get("kind"),
        "status": meta.get("status"), "updated": float(meta.get("updated") or time.time()),
        "body_md": body_md, "data": meta,
    }
    if row is None:
        s.add(AtomRow(atom_id=atom_id, first_user_id=first_user_id, **fields))
        return "inserted"
    for k, v in fields.items():
        setattr(row, k, v)
    return "updated"


def add_ref(s: Session, user_id: str, atom_id: str, *, relation: str = "used",
            report_file: str | None = None) -> None:
    """회원 ↔ 원자 참조. 같은 (회원·원자·관계)면 uses 만 올린다."""
    row = s.execute(select(AtomRef).where(
        AtomRef.user_id == user_id, AtomRef.atom_id == atom_id,
        AtomRef.relation == relation,
    )).scalar_one_or_none()
    if row is None:
        s.add(AtomRef(user_id=user_id, atom_id=atom_id, relation=relation,
                      report_file=report_file))
    else:
        row.uses = int(row.uses or 0) + 1
        if report_file:
            row.report_file = report_file


def all_atom_rows(s: Session) -> list[AtomRow]:
    """공용 저장소 전체(파일 복원용)."""
    return list(s.execute(select(AtomRow).order_by(AtomRow.updated)).scalars())


def user_atom_ids(s: Session, user_id: str) -> list[str]:
    return [r.atom_id for r in s.execute(
        select(AtomRef).where(AtomRef.user_id == user_id).order_by(AtomRef.created.desc())
    ).scalars()]


def user_view(s: Session, user_id: str, *, limit: int = 200) -> list[dict]:
    """이 회원이 보는 것 — 자기 참조와 그 원자의 요약.

    저장소가 갈라지는 것이 아니라 **보는 창이** 다르다는 설계를 그대로 옮긴 질의다.
    """
    refs = list(s.execute(
        select(AtomRef).where(AtomRef.user_id == user_id)
        .order_by(AtomRef.created.desc()).limit(limit)
    ).scalars())
    if not refs:
        return []
    by_id = {r.atom_id: r for r in s.execute(
        select(AtomRow).where(AtomRow.atom_id.in_([r.atom_id for r in refs]))
    ).scalars()}
    out = []
    for r in refs:
        a = by_id.get(r.atom_id)
        out.append({
            "id": r.atom_id, "relation": r.relation, "uses": r.uses,
            "report": r.report_file, "created": r.created,
            "title": (a.data or {}).get("title") if a else None,
            "scope": a.scope if a else None, "cell": a.cell if a else None,
        })
    return out


def export_atoms(s: Session, user_id: str) -> list[dict]:
    """내려받기 — 그 회원이 참조하는 원자만(공용 전체가 아니다)."""
    ids = user_atom_ids(s, user_id)
    if not ids:
        return []
    rows = s.execute(select(AtomRow).where(AtomRow.atom_id.in_(ids))).scalars()
    return [{"id": r.atom_id, "meta": r.data, "md": r.body_md} for r in rows]


# ── 보고서(과금 단위) ────────────────────────────────────────────
def report_count(s: Session, *, user_id: str = "", anon_key: str = "",
                 anon_ip: str = "") -> int:
    """몇 건을 썼는가. 비회원은 쿠키 **또는** IP 중 하나라도 걸리면 쓴 것으로 센다."""
    q = select(func.count()).select_from(ReportRow)
    if user_id:
        q = q.where(ReportRow.user_id == user_id)
    else:
        keys = [ReportRow.anon_key == anon_key] if anon_key else []
        if anon_ip:
            keys.append(ReportRow.anon_ip == anon_ip)
        if not keys:
            return 0
        q = q.where(or_(*keys))
    return int(s.execute(q).scalar_one())


def record_report(s: Session, *, user_id: str = "", anon_key: str = "", anon_ip: str = "",
                  report_file: str = "", label: str = "", cost_usd: float = 0.0,
                  atoms_new: int = 0, atoms_reused: int = 0) -> None:
    """보고서 1건을 남긴다 — 과금의 근거이자 원가 계측의 원천."""
    s.add(ReportRow(user_id=user_id or None, anon_key=anon_key or None,
                    anon_ip=anon_ip or None,
                    report_file=report_file[:300] or None, label=label[:200] or None,
                    cost_usd=float(cost_usd or 0.0),
                    atoms_new=int(atoms_new), atoms_reused=int(atoms_reused)))


def healthy(settings: Settings) -> dict:
    """DB 가 실제로 응답하는가 — /api/health 가 쓴다."""
    if not settings.database_url:
        return {"mode": "single-user", "ok": True}
    try:
        with session_for(settings) as s:
            n = int(s.execute(select(func.count()).select_from(User)).scalar_one())
            atoms = atom_total(s)
        return {"mode": "multi-user", "ok": True, "users": n, "atoms": atoms,
                "driver": settings.database_url.split("://", 1)[0]}
    except Exception as exc:  # noqa: BLE001 — 헬스체크가 예외로 죽으면 안 된다
        return {"mode": "multi-user", "ok": False, "error": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "Base", "User", "UserSession", "UserKey", "AtomRow", "AtomRef", "ReportRow",
    "engine_for", "session_for", "reset_engine",
    "atom_total", "ref_count", "upsert_atom", "add_ref", "all_atom_rows",
    "user_atom_ids", "user_view", "export_atoms",
    "report_count", "record_report", "healthy",
]
