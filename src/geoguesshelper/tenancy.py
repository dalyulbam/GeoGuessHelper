"""회원별 지식 저장소 — 격리와 영속.

풀어야 하는 문제 둘
  ① **격리**: 남의 원자가 내 회상에 섞이면 안 된다. recall 은 저장소 루트 아래를 훑으므로,
     루트를 회원마다 나누면 격리는 그것으로 끝난다.
  ② **영속**: 배포 파일시스템은 휘발성이다(Railway 는 배포마다 초기화된다). 파일만 믿으면
     회원 원자가 배포 때마다 사라진다.

그래서 파일과 DB 를 둘 다 쓴다 — 대체가 아니라 역할 분담이다.

    data/users/<uid>/knowledge/    엔진이 읽고 쓰는 곳(recall·molecule·observe 가 그대로 돈다)
    atoms 테이블                    살아남는 곳(정본)

  · hydrate(): 이 프로세스가 그 회원의 저장소를 처음 만질 때 DB → .md 복원.
  · sync_atom(): 저장이 일어날 때 .md 와 DB 에 함께 쓴다(write-through).

왜 파일 엔진을 DB 로 갈아엎지 않았나 — recall·분자·관측이 전부 파일을 읽는 기존 코드이고,
그 위에 260908 의 그래프 수정(헝가리안 매칭·union-find·float64 통일)이 얹혀 있다. 저장 계층을
통째로 바꾸면 그 검증을 전부 다시 해야 한다. 격리와 영속은 루트 분리 + write-through 로
충분히 얻어지므로, 더 싼 길을 택했다.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path

from .config import Settings

_SAFE_ID = re.compile(r"^[0-9a-f]{8,64}$")
_hydrated: set[str] = set()


class QuotaExceeded(RuntimeError):
    """무료 회원의 원자 상한. 호출부가 사용자 안내로 바꾼다."""


def user_root(settings: Settings, user_id: str) -> Path:
    """그 회원의 데이터 뿌리. id 는 우리가 만든 hex 라 경로 조작이 불가능하지만,
    **그래도 검사한다** — 경로를 만드는 값은 출처를 믿지 않는다."""
    uid = (user_id or "").strip().lower()
    if not _SAFE_ID.match(uid):
        raise ValueError(f"잘못된 사용자 id: {user_id!r}")
    return settings.data_dir / "users" / uid


def settings_for_user(settings: Settings, user_id: str) -> Settings:
    """그 회원 전용 경로로 갈아 끼운 Settings 사본.

    원본을 수정하지 않는다 — 요청마다 다른 사용자가 오고, 전역을 건드리면 동시 요청이
    서로의 저장소를 본다(이 앱에서 가장 나쁜 실패는 조용히 남의 것을 쓰는 것이다).
    """
    root = user_root(settings, user_id)
    s = replace(
        settings,
        knowledge_dir=root / "knowledge",
        captures_dir=root / "captures",
        reports_dir=root / "reports",
        jobs_dir=root / "jobs",
        atlas_dir=root / "atlas",
    )
    for d in (s.knowledge_dir, s.captures_dir, s.reports_dir, s.jobs_dir, s.atlas_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


# ── DB ↔ 파일 ────────────────────────────────────────────────────
def hydrate(settings: Settings, user_id: str) -> int:
    """DB 의 원자를 회원 저장소에 .md 로 복원한다. 프로세스당 회원마다 한 번.

    반환은 새로 쓴 파일 수. DB 가 없으면(단독 모드) 0.
    """
    if not settings.multi_user or user_id in _hydrated:
        return 0
    _hydrated.add(user_id)
    from . import db

    us = settings_for_user(settings, user_id)
    atoms_dir = us.knowledge_dir / "atoms"
    atoms_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    with db.session_for(settings) as s:
        for row in db.all_atom_rows(s, user_id):
            p = atoms_dir / f"{row.atom_id}.md"
            # 이미 있고 내용이 같으면 건드리지 않는다 — mtime 을 흔들면 관측 사이드카가
            # 바뀐 것으로 오해한다.
            if p.exists() and p.read_text(encoding="utf-8") == row.body_md:
                continue
            p.write_text(row.body_md, encoding="utf-8")
            n += 1
    if n:
        # 색인은 파생물이다 — 파일이 바뀌었으면 다시 만든다(knowledge.py:258).
        from . import knowledge

        knowledge.Store(us.knowledge_dir).rebuild()
    return n


def forget_hydration(user_id: str = "") -> None:
    """테스트용 — 다음 hydrate 가 다시 돌게 한다."""
    if user_id:
        _hydrated.discard(user_id)
    else:
        _hydrated.clear()


def sync_atoms(settings: Settings, user_id: str, *, plan: str = "free") -> dict:
    """회원 저장소의 .md 를 DB 로 올린다(write-through).

    무료 회원은 free_atom_limit 에서 멈춘다 — 넘긴 만큼은 파일에 남아 그 세션에서는
    쓰이지만 DB 에 들어가지 않으므로 다음 배포에서 사라진다. 그 사실을 반환값으로
    알린다(조용히 버리지 않는다).
    """
    if not settings.multi_user:
        return {"synced": 0, "skipped": 0, "limit": None}
    from . import db
    from .knowledge import Atom

    us = settings_for_user(settings, user_id)
    atoms_dir = us.knowledge_dir / "atoms"
    if not atoms_dir.exists():
        return {"synced": 0, "skipped": 0, "limit": None}

    limit = None if plan == "pro" else int(settings.free_atom_limit)
    synced = skipped = 0
    with db.session_for(settings) as s:
        have = db.atom_count(s, user_id)
        for p in sorted(atoms_dir.glob("atm_*.md")):
            md = p.read_text(encoding="utf-8")
            a = Atom.from_md(md)
            if a is None:
                continue
            exists = s.query(db.AtomRow).filter(
                db.AtomRow.user_id == user_id, db.AtomRow.atom_id == a.id
            ).first() is not None
            if limit is not None and not exists and have >= limit:
                skipped += 1
                continue
            if db.upsert_atom(s, user_id, atom_id=a.id, meta=a.meta(), body_md=md) == "inserted":
                have += 1
            synced += 1
    return {"synced": synced, "skipped": skipped, "limit": limit, "total": have}


def usage(settings: Settings, user_id: str, *, plan: str = "free") -> dict:
    """지금 몇 개를 쓰고 있는가 — 화면이 상한을 보여 줄 수 있게."""
    if not settings.multi_user:
        return {"atoms": 0, "limit": None, "plan": plan}
    from . import db

    with db.session_for(settings) as s:
        n = db.atom_count(s, user_id)
    return {"atoms": n, "limit": None if plan == "pro" else int(settings.free_atom_limit),
            "plan": plan}


def purge(settings: Settings, user_id: str) -> None:
    """회원 탈퇴 — 파일과 DB 양쪽에서 지운다. DB 쪽은 cascade 가 맡는다."""
    root = user_root(settings, user_id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    forget_hydration(user_id)


__all__ = ["QuotaExceeded", "user_root", "settings_for_user", "hydrate",
           "sync_atoms", "usage", "purge", "forget_hydration"]
