"""회원 작업공간과 공유 지식 — 무엇을 나누고 무엇을 나누지 않는가.

**지식은 나누지 않는다 (260913 정정).** 앞선 판은 회원마다 knowledge/ 를 따로 두었는데,
그건 이 저장소가 이미 보장하는 성질을 깨는 설계였다. 원자 id 는 내용 주소다
(knowledge.py:164 — "같은 사실이면 같은 id"). 같은 사실을 두 회원이 각각 만들어도
id 가 같으므로 **중복 저장이 구조적으로 불가능한데**, 회원별 사본을 만들면 같은 원자가
회원 수만큼 생기고 그 성질이 무너진다.

그래서 이렇게 나눈다.

    knowledge/                     공유 — 원자·분자의 그래프. 하나뿐이다.
    data/users/<uid>/captures/     회원 것 — 그 사람이 올린 사진
    data/users/<uid>/reports/      회원 것 — 그 사람의 보고서
    data/users/<uid>/jobs/         회원 것 — 그 사람의 작업 기록
    atom_refs 표                    회원 ↔ 원자. **그 사람이 보는 창**

회원이 늘어도 지식의 총량은 그대로다. 늘어나는 것은 참조(창)뿐이다. 새 원자를 만드는
비용은 운영자가 내고, 이미 있는 원자는 다시 만들지 않고 가져다 쓴다 — 그게 이 구조가
그대로 사업 모형이 되는 지점이다.

파일과 DB 를 둘 다 쓰는 이유는 그대로다. 배포 파일시스템은 휘발성이라(Railway 는 배포마다
초기화된다) DB 가 정본이고, recall·분자·관측이 전부 파일을 읽는 기존 코드라 파일이 작업면이다.

  · hydrate():  DB → .md 복원. 공유 저장소이므로 **프로세스당 한 번**이다.
  · sync_atoms(): 저장이 일어나면 .md → DB(공용) + 그 회원의 참조를 남긴다.
"""
from __future__ import annotations

import re
import shutil
import threading
from dataclasses import replace
from pathlib import Path

from .config import Settings

_SAFE_ID = re.compile(r"^[0-9a-f]{8,64}$")
_hydrated = False
_hydrate_lock = threading.Lock()


class QuotaExceeded(RuntimeError):
    """무료 한도. 호출부가 사용자 안내로 바꾼다."""


def user_root(settings: Settings, user_id: str) -> Path:
    """그 회원의 작업공간 뿌리. id 는 우리가 만든 hex 라 경로 조작이 불가능하지만,
    **그래도 검사한다** — 경로를 만드는 값은 출처를 믿지 않는다."""
    uid = (user_id or "").strip().lower()
    if not _SAFE_ID.match(uid):
        raise ValueError(f"잘못된 사용자 id: {user_id!r}")
    return settings.data_dir / "users" / uid


def settings_for_user(settings: Settings, user_id: str) -> Settings:
    """그 회원 전용 경로로 갈아 끼운 Settings 사본.

    **knowledge_dir 은 갈아 끼우지 않는다** — 지식은 공유다. 나누는 것은 그 사람이
    올린 것과 받아 본 것(captures·reports·jobs·atlas)뿐이다.

    원본을 수정하지 않는다 — 요청마다 다른 사용자가 오고, 전역을 건드리면 동시 요청이
    서로의 작업공간을 본다(이 앱에서 가장 나쁜 실패는 조용히 남의 것을 쓰는 것이다).
    """
    root = user_root(settings, user_id)
    s = replace(
        settings,
        captures_dir=root / "captures",
        reports_dir=root / "reports",
        jobs_dir=root / "jobs",
        atlas_dir=root / "atlas",
    )
    for d in (s.knowledge_dir, s.captures_dir, s.reports_dir, s.jobs_dir, s.atlas_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


# ── DB ↔ 파일 ────────────────────────────────────────────────────
def hydrate(settings: Settings, user_id: str = "") -> int:
    """DB 의 원자를 공유 저장소에 .md 로 복원한다. **프로세스당 한 번.**

    user_id 는 받아도 쓰지 않는다 — 호출부(요청 경로)가 회원 단위로 부르기 때문에
    자리는 남기되, 복원 대상은 공용 저장소 하나다. 반환은 새로 쓴 파일 수.
    """
    global _hydrated
    if not settings.multi_user:
        return 0
    with _hydrate_lock:
        if _hydrated:
            return 0
        _hydrated = True
    from . import db

    atoms_dir = settings.knowledge_dir / "atoms"
    atoms_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    with db.session_for(settings) as s:
        for row in db.all_atom_rows(s):
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

        knowledge.Store(settings.knowledge_dir).rebuild()
    return n


def forget_hydration(user_id: str = "") -> None:
    """테스트용 — 다음 hydrate 가 다시 돌게 한다."""
    global _hydrated
    _hydrated = False


def sync_atoms(settings: Settings, user_id: str, *, plan: str = "free",
               report_file: str = "", before: set[str] | None = None) -> dict:
    """공유 저장소의 .md 를 DB 로 올리고, 이 회원의 참조를 남긴다.

    before 는 이 작업 **전에** 있던 원자 id 들이다. 그걸 주면 이번에 새로 생긴 것과
    그냥 가져다 쓴 것을 가릴 수 있다 — 새 원자는 운영자가 돈을 들여 만든 것이고,
    나머지는 이미 있던 지식이다. 이 구분이 원가 계측의 전부이므로 세어서 돌려준다.

    한계 하나를 적어 둔다: 저장소가 공유이므로 **두 잡이 겹쳐 돌면** 남의 잡이 만든
    원자가 내 "새 원자"로 세어질 수 있다. 그래프는 멀쩡하고(내용 주소라 중복이 없다)
    틀리는 것은 건별 귀속뿐이며, 총합은 맞는다. 원가 평균을 보는 용도에는 충분해서
    잠금을 걸지 않았다 — 건별 정확도가 필요해지면 그때 잡별 산출 목록을 받아야 한다.
    """
    if not settings.multi_user:
        return {"created": 0, "reused": 0, "total": 0}
    from . import db
    from .knowledge import Atom

    atoms_dir = settings.knowledge_dir / "atoms"
    if not atoms_dir.exists():
        return {"created": 0, "reused": 0, "total": 0}

    seen_before = before if before is not None else set()
    created = reused = 0
    with db.session_for(settings) as s:
        for p in sorted(atoms_dir.glob("atm_*.md")):
            md = p.read_text(encoding="utf-8")
            a = Atom.from_md(md)
            if a is None:
                continue
            fresh = a.id not in seen_before
            if before is None and not fresh:
                continue
            db.upsert_atom(s, atom_id=a.id, meta=a.meta(), body_md=md,
                           first_user_id=user_id if (fresh and user_id) else None)
            rel = "created" if fresh else "used"
            if fresh:
                created += 1
            else:
                reused += 1
            # 비회원(user_id 없음)의 1건도 원자는 남는다 — 지식은 공용이니까. 남지
            # 않는 것은 참조뿐이다(가리킬 사람이 없다).
            if user_id:
                db.add_ref(s, user_id, a.id, relation=rel, report_file=report_file)
        total = db.atom_total(s)
    return {"created": created, "reused": reused, "total": total}


def atom_ids_now(settings: Settings) -> set[str]:
    """지금 공유 저장소에 있는 원자 id — sync_atoms 의 before 로 넘길 값."""
    atoms_dir = settings.knowledge_dir / "atoms"
    if not atoms_dir.exists():
        return set()
    return {p.stem for p in atoms_dir.glob("atm_*.md")}


def usage(settings: Settings, user_id: str, *, plan: str = "free") -> dict:
    """이 회원이 보는 창의 크기와, 지식 전체의 크기.

    둘을 나란히 보여 주는 것이 설계 그 자체다 — 내 참조는 늘지만 지식은 하나다.
    """
    base = {"refs": 0, "atoms": 0, "reports": 0,
            "free": int(settings.free_reports), "plan": plan}
    if not settings.multi_user:
        return base
    from . import db

    with db.session_for(settings) as s:
        base["refs"] = db.ref_count(s, user_id)
        base["atoms"] = db.atom_total(s)
        base["reports"] = db.report_count(s, user_id=user_id)
    base["remaining"] = (None if plan == "pro"
                         else max(0, base["free"] - base["reports"]))
    return base


def purge(settings: Settings, user_id: str) -> None:
    """회원 탈퇴 — 그 사람의 작업공간과 참조를 지운다.

    **원자는 지우지 않는다.** 그 사람이 처음 만든 원자라도 이미 공용 지식이고, 다른
    회원의 회상이 그것에 기대고 있다. 지우는 것은 창이지 지식이 아니다.
    """
    root = user_root(settings, user_id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


__all__ = ["QuotaExceeded", "user_root", "settings_for_user", "hydrate",
           "sync_atoms", "atom_ids_now", "usage", "purge", "forget_hydration"]
