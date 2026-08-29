"""저장소 정리 — 캡처·임시 렌더 파일·보고서·큐 로그가 무한히 쌓이는 것을 막는다.

문제: 이 앱은 만들기만 하고 지우지 않았다. 실측(v0.4.2 시점, 보고서 13건):

    captures/          379개  196.6 MB   ← 이 중 306개(162.7 MB)가 어떤 보고서도 참조 안 함
    docs/report/        13개  126.0 MB   ← HTML 에 이미지가 base64 로 박혀 1건당 ~10 MB
    docs/jobs/*.jsonl    1개    0.2 MB   ← append 전용, 회전 없음

캡처가 고아가 되는 이유: 보고서는 이미지를 **base64 로 임베드**해서 서버 없이도 열리게
만든다(`report.py`). 그래서 보고서가 나온 순간 원본 .jpg 는 더 이상 필요 없는데,
지우는 코드가 아무 데도 없었다.

`.rv_*.html` 은 더 급하다 — `render_google.py:471` 이 Static Maps **API 키를 평문으로**
박아 captures_dir 에 쓰고 finally 에서 지우는데, 그 경로가 깨지면 키가 든 파일이 디스크에
남는다(실제로 2개가 남아 있었다). 그래서 이 범주는 `--older-than` 보존 기간을 무시하고
언제나 후보가 된다. 단 아래 `_GRACE_S` 만은 지킨다 — 방금 만들어진 `.rv_` 는 지금 렌더
중인 브라우저가 읽고 있는 파일이라, 지우면 진행 중인 캡처가 깨진다.

안전 규칙 — 지우면 안 되는 것을 코드로 막는다:
  · docs/knowledge/ 는 **절대** 건드리지 않는다. .gitignore 가 명시하듯 의도적으로 커밋하는
    자산이고, 보고서를 만들수록 쌓이는 유일한 비복구 데이터다.
  · 프로젝트 루트 밖은 건드리지 않는다(경로 탈출 방어).
  · git 이 추적 중인 파일은 기본적으로 건너뛴다.
  · `_GRACE_S` 안에 생성된 파일은 진행 중인 작업이 쓰는 중일 수 있으므로 건너뛴다.
  · 큐에 살아 있는(비종료) 작업이 참조하는 캡처는 건너뛴다.
  · 기본은 **모의 실행**이다. 실제 삭제는 호출자가 명시해야 한다.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, project_root

# 진행 중인 작업이 쓰는 중일 수 있는 구간 — 이보다 최근 파일은 건드리지 않는다.
_GRACE_S = 900.0

_CAPTURE_RE = re.compile(r"capture_[0-9a-f]+\.(?:jpg|jpeg|png)", re.I)
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class Item:
    path: Path
    size: int
    mtime: float
    category: str
    reason: str = ""

    def public(self, root: Path) -> dict:
        try:
            rel = str(self.path.relative_to(root))
        except ValueError:
            rel = str(self.path)
        return {
            "path": rel,
            "size": self.size,
            "ageDays": round(max(0.0, time.time() - self.mtime) / 86400.0, 2),
            "category": self.category,
            "reason": self.reason,
        }


@dataclass
class Category:
    key: str
    label: str
    detail: str
    default: bool           # 옵션 없이 부르는 기본 정리에 포함되는가
    items: list[Item] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(i.size for i in self.items)

    def public(self, root: Path, *, sample: int = 5) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "detail": self.detail,
            "default": self.default,
            "files": len(self.items),
            "size": self.size,
            "sizeHuman": human(self.size),
            "sample": [i.public(root) for i in self.items[:sample]],
        }


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ── 참조 추적 ────────────────────────────────────────────────────
def referenced_captures(settings: Settings) -> set[str]:
    """보고서(HTML/JSON)가 파일명으로 언급하는 캡처들.

    보고서는 이미지를 base64 로 임베드하지만 원본 파일명도 함께 남긴다. 그 이름이
    고아 판정의 유일한 근거이므로, 보고서를 못 읽으면 **보수적으로** 전부 참조된
    것으로 친다(읽기 실패 때문에 지워버리는 사고를 막는다).
    """
    ref: set[str] = set()
    d = settings.reports_dir
    if not d.exists():
        return ref
    for p in d.rglob("*"):          # 국가 하위 폴더(docs/report/{iso}/)까지
        if not p.is_file() or p.suffix.lower() not in (".html", ".json", ".bak"):
            continue
        try:
            ref |= {m.lower() for m in _CAPTURE_RE.findall(p.read_text(encoding="utf-8", errors="ignore"))}
        except OSError:
            continue
    return ref


def _live_job_captures(queue) -> set[str]:
    """큐에 살아 있는(대기/실행 중) 작업이 쓰기로 한 캡처 파일명."""
    names: set[str] = set()
    if queue is None:
        return names
    try:
        from . import jobs as _jobs

        for job in getattr(queue, "_jobs", {}).values():
            if job.status in _jobs.TERMINAL:
                continue
            for f in (job.payload or {}).get("files") or []:
                names.add(Path(str(f)).name.lower())
    except Exception:  # noqa: BLE001 — 정리 기능이 큐 내부 구조에 죽으면 안 된다
        return set()
    return names


def _git_tracked(probe: Path) -> set[Path]:
    """git 이 추적 중인 파일 — 기본적으로 보호 대상.

    저장소 최상위를 `probe` 에서 되짚어 찾는다. 설치 위치(project_root)를 쓰면 캡처
    디렉터리를 다른 곳으로 옮겼을 때(GEOHELPER_CAPTURES) 엉뚱한 저장소를 보게 된다.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            capture_output=True, timeout=20, check=False,
        )
        if top.returncode != 0:
            return set()
        root = Path(top.stdout.decode("utf-8", "ignore").strip())
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, timeout=20, check=False,
        )
        if out.returncode != 0:
            return set()
        return {(root / p).resolve() for p in out.stdout.decode("utf-8", "ignore").split("\0") if p}
    except (OSError, subprocess.SubprocessError):
        return set()


# ── 스캔 ─────────────────────────────────────────────────────────
def scan(
    settings: Settings,
    *,
    older_than_days: float = 0.0,
    keep_last: int = 0,
    queue=None,
) -> dict:
    """지울 수 있는 것을 범주별로 모은다. 아무것도 지우지 않는다."""
    root = project_root().resolve()
    now = time.time()
    cutoff = now - older_than_days * 86400.0 if older_than_days > 0 else now
    protected_dir = settings.knowledge_dir.resolve()

    # 손댈 수 있는 디렉터리를 **화이트리스트로** 고정한다. "프로젝트 안이면 OK" 로 두면
    # src/ 나 .env 까지 사정권에 들어온다 — 지울 수 있는 곳만 명시적으로 연다.
    allowed: list[Path] = []
    for d in (settings.captures_dir, settings.reports_dir, settings.jobs_dir,
              root / "src", root / "tests"):
        try:
            rd = Path(d).resolve()
        except OSError:
            continue
        if rd != protected_dir and protected_dir not in rd.parents:
            allowed.append(rd)

    probe = next((d for d in (settings.captures_dir, settings.reports_dir) if d.exists()), root)
    tracked = _git_tracked(probe)
    ref = referenced_captures(settings)
    live = _live_job_captures(queue)

    def usable(p: Path, *, ignore_age: bool = False, allow_tracked: bool = False) -> tuple[bool, str]:
        """이 파일을 삭제 후보로 올려도 되는가."""
        try:
            rp = p.resolve()
        except OSError:
            return False, "해석 불가"
        if not p.is_file():
            return False, "파일 아님"
        if rp == protected_dir or protected_dir in rp.parents:
            return False, "지식 저장소 보호"
        if not any(a in rp.parents for a in allowed):
            return False, "허용 디렉터리 밖"
        if not allow_tracked and rp in tracked:
            return False, "git 추적 중"
        try:
            st = p.stat()
        except OSError:
            return False, "stat 실패"
        if now - st.st_mtime < _GRACE_S:
            return False, "작업 중일 수 있음"
        if not ignore_age and older_than_days > 0 and st.st_mtime > cutoff:
            return False, "보존 기간 내"
        return True, ""

    def mk(p: Path, cat: str, reason: str) -> Item:
        st = p.stat()
        return Item(path=p, size=st.st_size, mtime=st.st_mtime, category=cat, reason=reason)

    cats: dict[str, Category] = {}

    def add(key: str, label: str, detail: str, default: bool) -> Category:
        c = Category(key=key, label=label, detail=detail, default=default)
        cats[key] = c
        return c

    # 1) 렌더 임시 HTML — API 키가 평문으로 들어 있다. 나이 무관, 항상 후보.
    c = add("render-temp", "렌더 임시 파일",
            "captures/.rv_*.html · .t8_*.html — Static Maps API 키가 평문으로 포함된다", True)
    if settings.captures_dir.exists():
        for p in settings.captures_dir.iterdir():
            if p.name.startswith((".rv_", ".t8_")) and p.suffix.lower() == ".html":
                ok, _ = usable(p, ignore_age=True)
                if ok:
                    c.items.append(mk(p, c.key, "API 키 노출"))

    # 2) 고아 캡처 — 보고서가 base64 로 임베드했으므로 원본은 더 이상 필요 없다.
    c = add("capture-orphan", "고아 캡처",
            "어떤 보고서도 참조하지 않는 captures/ 이미지", True)
    # 3) 참조된 캡처 — 보고서 안에 base64 사본이 있으므로 지워도 보고서는 열린다.
    c2 = add("capture-linked", "참조된 캡처",
             "보고서가 언급하는 원본 이미지(보고서에 base64 사본이 있어 삭제해도 열린다)", False)
    if settings.captures_dir.exists():
        for p in settings.captures_dir.iterdir():
            if p.suffix.lower() not in _IMAGE_EXT:
                continue
            name = p.name.lower()
            if name in live:
                continue
            ok, _ = usable(p)
            if not ok:
                continue
            if name in ref:
                c2.items.append(mk(p, c2.key, "보고서가 참조"))
            else:
                c.items.append(mk(p, c.key, "참조 없음"))

    # 4) 보고서 부산물 — 재번역 백업과 스크립트 중간산물.
    c = add("report-aux", "보고서 부산물",
            "docs/report/*.bak · *.script.json — 재생성 가능한 중간산물", True)
    if settings.reports_dir.exists():
        for p in settings.reports_dir.rglob("*"):
            if p.suffix.lower() == ".bak" or p.name.endswith(".script.json"):
                ok, _ = usable(p)
                if ok:
                    c.items.append(mk(p, c.key, "중간산물"))

    # 5) 오래된 보고서 HTML — keep_last 를 줬을 때만 후보가 된다(기본은 건드리지 않는다).
    c = add("report-html", "오래된 보고서",
            "docs/report/*.html — keep_last 로 최신 N건만 남긴다", False)
    if keep_last > 0 and settings.reports_dir.exists():
        htmls = sorted(
            # report_*.html 만 — civilzation/ 같은 문서 폴더의 HTML 은 보고서가 아니다
            (p for p in settings.reports_dir.rglob("report_*.html") if p.is_file()),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for p in htmls[keep_last:]:
            ok, _ = usable(p, ignore_age=True)
            if ok:
                c.items.append(mk(p, c.key, f"최신 {keep_last}건 밖"))

    # 6) 큐 실행 로그 — append 전용이라 회전이 없다.
    c = add("jobs-log", "작업 큐 로그",
            "docs/jobs/*.jsonl — append 전용, 회전 없음", True)
    if settings.jobs_dir.exists():
        for p in settings.jobs_dir.glob("*.jsonl"):
            ok, _ = usable(p)
            if ok:
                c.items.append(mk(p, c.key, "실행 로그"))

    # 7) 원자적 쓰기 잔여물 — tmp.replace() 가 중간에 죽으면 남는다.
    c = add("atomic-temp", "원자적 쓰기 잔여물",
            "*.tmp — 저장 도중 중단되어 남은 파일", True)
    for base in (settings.reports_dir, settings.jobs_dir, settings.captures_dir):
        if not base.exists():
            continue
        for p in base.rglob("*.tmp"):
            ok, _ = usable(p, ignore_age=True)
            if ok:
                c.items.append(mk(p, c.key, "미완 저장"))

    # 8) 파이썬 바이트코드 캐시.
    c = add("pycache", "파이썬 캐시", "__pycache__/*.pyc — 언제든 재생성된다", False)
    for d in (root / "src", root / "tests"):
        if not d.exists():
            continue
        for p in d.rglob("*.pyc"):
            ok, _ = usable(p, ignore_age=True)
            if ok:
                c.items.append(mk(p, c.key, "바이트코드"))

    total = sum(x.size for x in cats.values())
    default_size = sum(x.size for x in cats.values() if x.default)
    return {
        "root": str(root),
        "categories": [c.public(root) for c in cats.values()],
        "_cats": cats,                     # 내부용(sweep 이 재사용)
        "totalFiles": sum(len(x.items) for x in cats.values()),
        "totalSize": total,
        "totalSizeHuman": human(total),
        "defaultSize": default_size,
        "defaultSizeHuman": human(default_size),
        "graceSeconds": _GRACE_S,
        "olderThanDays": older_than_days,
        "keepLast": keep_last,
    }


# ── 실행 ─────────────────────────────────────────────────────────
def sweep(
    settings: Settings,
    *,
    categories: list[str] | None = None,
    older_than_days: float = 0.0,
    keep_last: int = 0,
    apply: bool = False,
    queue=None,
) -> dict:
    """스캔 결과를 실제로 지운다. `apply=False`(기본)면 모의 실행이다."""
    report = scan(settings, older_than_days=older_than_days, keep_last=keep_last, queue=queue)
    cats: dict[str, Category] = report.pop("_cats")

    if categories:
        want = {c.strip() for c in categories if c and c.strip()}
        unknown = want - set(cats)
        if unknown:
            raise ValueError(f"알 수 없는 범주: {', '.join(sorted(unknown))}")
        chosen = [cats[k] for k in want]
    else:
        chosen = [c for c in cats.values() if c.default]

    removed: list[dict] = []
    failed: list[dict] = []
    freed = 0
    root = Path(report["root"])
    for c in chosen:
        for item in c.items:
            if not apply:
                removed.append(item.public(root))
                freed += item.size
                continue
            try:
                item.path.unlink()
            except OSError as exc:
                failed.append({**item.public(root), "error": str(exc)})
                continue
            removed.append(item.public(root))
            freed += item.size

    if apply:
        _prune_empty_dirs(settings)

    return {
        "applied": apply,
        "categories": sorted(c.key for c in chosen),
        "removed": len(removed),
        "failed": failed,
        "freed": freed,
        "freedHuman": human(freed),
        "olderThanDays": older_than_days,
        "keepLast": keep_last,
        "files": removed[:200],
        "truncated": max(0, len(removed) - 200),
    }


def scan_pos(settings: Settings, older_than_days: float, keep_last: int, queue) -> dict:
    """`_in_pool(fn, *args)` 이 위치인자만 넘기므로 쓰는 얇은 래퍼."""
    return scan(settings, older_than_days=older_than_days, keep_last=keep_last, queue=queue)


def sweep_pos(
    settings: Settings, categories: list[str], older_than_days: float,
    keep_last: int, apply_: bool, queue,
) -> dict:
    """`scan_pos` 와 같은 이유의 위치인자 래퍼."""
    return sweep(
        settings, categories=categories or None, older_than_days=older_than_days,
        keep_last=keep_last, apply=apply_, queue=queue,
    )


def _prune_empty_dirs(settings: Settings) -> None:
    """캡처 하위에 빈 디렉터리가 남으면 정리한다(최상위는 유지)."""
    base = settings.captures_dir
    if not base.exists():
        return
    for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass


# ── 메모리 ───────────────────────────────────────────────────────
def prune_memory(queue, *, max_age_s: float = 3600.0) -> dict:
    """끝난 지 오래된 작업의 `result` 본문을 메모리에서 떨군다.

    `JobQueue._jobs` 는 history(기본 200)건을 통째로 들고 있고, 각 result 에는 보고서
    분석 전문(primaryAnalysis)이 들어간다. 목록·상태 표시에는 result 가 필요 없으므로
    오래된 것부터 본문만 비운다 — job id 로 조회하면 여전히 상태는 나온다.
    """
    if queue is None:
        return {"pruned": 0, "jobs": 0}
    try:
        from . import jobs as _jobs

        now = time.time()
        pruned = 0
        allj = getattr(queue, "_jobs", {})
        for job in allj.values():
            if job.status not in _jobs.TERMINAL or job.result is None:
                continue
            if now - (job.finished_at or job.created_at) < max_age_s:
                continue
            if job._subs:                       # 아직 보고 있는 구독자가 있으면 유지
                continue
            job.result = None
            job.progress = job.progress[-5:]
            pruned += 1
        return {"pruned": pruned, "jobs": len(allj)}
    except Exception as exc:  # noqa: BLE001
        return {"pruned": 0, "jobs": 0, "error": str(exc)}


# ── 사람이 읽는 요약 ─────────────────────────────────────────────
def format_report(report: dict) -> str:
    lines = [
        f"프로젝트: {report['root']}",
        f"정리 가능: {report['totalFiles']}개 · {report['totalSizeHuman']}"
        f"  (기본 범주만: {report['defaultSizeHuman']})",
        "",
        f"{'범주':<16}{'기본':<6}{'파일':>7}{'크기':>12}   설명",
        "─" * 96,
    ]
    for c in report["categories"]:
        mark = "✓" if c["default"] else "·"
        lines.append(
            f"{c['key']:<16}{mark:<6}{c['files']:>7}{c['sizeHuman']:>12}   {c['detail']}"
        )
    return "\n".join(lines)
