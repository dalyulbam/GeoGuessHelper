"""정리 기능 실증 — 샌드박스에 가짜 저장소를 짓고 **실제로 지워 본다**.

왜 이 파일이 저장소에 있는가
    정리 코드는 잘못 짜면 조용히 남의 데이터를 지운다. 그리고 그 실패는 되돌릴 수 없다.
    이 프로젝트에서 지우면 안 되는 것은 명확하다 — docs/knowledge/ 는 보고서를 만들수록
    쌓이는 유일한 비복구 자산이고 .gitignore 가 "의도적으로 커밋한다"고 못박아 뒀다.

    그래서 이 스위트는 "안 지워지더라" 를 믿지 않는다. 실제 파일을 만들고, apply=True 로
    진짜 삭제를 돌린 뒤, **살아남아야 할 파일이 살아 있는지 stat 으로 확인한다.**

검증 항목
    ① 지식 저장소는 어떤 범주로도 지워지지 않는다
    ② git 이 추적 중인 파일은 지워지지 않는다
    ③ _GRACE_S 안의 최신 파일은 작업 중일 수 있으므로 지워지지 않는다
    ④ 큐에 살아 있는 작업이 참조하는 캡처는 지워지지 않는다
    ⑤ 보고서가 참조하는 캡처와 고아 캡처가 올바로 갈린다
    ⑥ apply=False 는 한 개도 지우지 않는다(바이트 수는 보고하되)
    ⑦ apply=True 는 보고한 만큼 정확히 지운다
    ⑧ 경로 탈출(..)로 프로젝트 밖을 지울 수 없다
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from geoguesshelper import cleanup            # noqa: E402
from geoguesshelper.config import Settings    # noqa: E402

OLD = time.time() - 30 * 86400                # 30일 전 — 유예구간 밖


def _write(p: Path, text: str = "x", *, mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def build(root: Path) -> Settings:
    """가짜 프로젝트 — 실제 디렉터리 배치를 그대로 흉내낸다."""
    (root / "pyproject.toml").write_text("[project]\nname='fake'\n", encoding="utf-8")
    s = Settings()
    s.captures_dir = root / "captures"
    s.reports_dir = root / "docs" / "report"
    s.knowledge_dir = root / "docs" / "knowledge"
    s.jobs_dir = root / "docs" / "jobs"
    for d in (s.captures_dir, s.reports_dir, s.knowledge_dir, s.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 보고서 1건이 캡처 2개를 참조한다(base64 임베드 + 파일명 언급).
    _write(s.reports_dir / "report_x.html",
           "<img src='data:image/jpeg;base64,AAA' alt='capture_aaaaaaaaaaaa.jpg'>"
           "<img alt='capture_bbbbbbbbbbbb.jpg'>", mtime=OLD)
    _write(s.reports_dir / "report_x.script.json", "{}", mtime=OLD)
    _write(s.reports_dir / "report_x.html.bak", "old", mtime=OLD)

    _write(s.captures_dir / "capture_aaaaaaaaaaaa.jpg", "A" * 100, mtime=OLD)   # 참조됨
    _write(s.captures_dir / "capture_bbbbbbbbbbbb.jpg", "B" * 100, mtime=OLD)   # 참조됨
    _write(s.captures_dir / "capture_cccccccccccc.jpg", "C" * 100, mtime=OLD)   # 고아
    _write(s.captures_dir / "capture_dddddddddddd.jpg", "D" * 100, mtime=OLD)   # 고아
    _write(s.captures_dir / "capture_eeeeeeeeeeee.jpg", "E" * 100)             # 고아지만 방금 생성
    _write(s.captures_dir / "capture_ffffffffffff.jpg", "F" * 100, mtime=OLD)   # 고아지만 큐가 사용중
    _write(s.captures_dir / ".rv_12345678.html", "key=SECRET", mtime=OLD)       # 방치된 키 노출
    _write(s.captures_dir / ".rv_87654321.html", "key=SECRET")                  # 렌더 진행 중
    _write(s.jobs_dir / "queue.jsonl", "{}\n", mtime=OLD)

    # 화이트리스트 밖 — 프로젝트 안이지만 정리 대상 디렉터리가 아니다.
    _write(root / ".env", "ANTHROPIC_API_KEY=sk-real", mtime=OLD)
    _write(root / "src" / "geoguesshelper" / "server.py", "print(1)", mtime=OLD)

    # 지식 저장소 — 절대 지워지면 안 된다.
    _write(s.knowledge_dir / "index.json", "{}", mtime=OLD)
    _write(s.knowledge_dir / "atoms" / "atm_deadbeef0000.md", "---\n{}\n---\n", mtime=OLD)
    _write(s.knowledge_dir / "atoms" / "stray.tmp", "tmp", mtime=OLD)
    return s


class FakeJob:
    def __init__(self, status: str, files: list[str]) -> None:
        self.status = status
        self.payload = {"files": files}


class FakeQueue:
    def __init__(self, jobs: dict) -> None:
        self._jobs = jobs


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    return cond


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ggh_clean_"))
    root = tmp / "proj"
    root.mkdir()
    ok = True
    try:
        # project_root() 는 pyproject.toml 을 위로 찾아 올라간다 → cwd 를 옮겨 가짜 루트를 잡는다.
        prev = Path.cwd()
        os.chdir(root)
        s = build(root)

        print("① 스캔 — 고아/참조 분류")
        rep = cleanup.scan(s)
        cats = {c["key"]: c for c in rep["categories"]}
        rep.pop("_cats")
        orphan = cats["capture-orphan"]["files"]
        linked = cats["capture-linked"]["files"]
        ok &= check("고아 캡처 3개 (c, d, f — e 는 유예구간이라 제외)",
                    orphan == 3, f"실제 {orphan}")
        ok &= check("참조된 캡처 2개 (a, b)", linked == 2, f"실제 {linked}")
        ok &= check("방치된 렌더 임시파일 1개만 (진행 중인 것은 제외)",
                    cats["render-temp"]["files"] == 1, f"실제 {cats['render-temp']['files']}")
        ok &= check("보고서 부산물 2개 (.bak, .script.json)",
                    cats["report-aux"]["files"] == 2, f"실제 {cats['report-aux']['files']}")

        print("\n② 유예구간 — 방금 만든 파일은 후보가 아니다")
        recent = [i for c in rep["categories"] for i in c["sample"]
                  if "capture_eeee" in i["path"]]
        ok &= check("최근 캡처 e 는 어떤 범주에도 없다", not recent)

        print("\n③ 큐가 참조 중인 캡처는 제외")
        q = FakeQueue({"j1": FakeJob("RUNNING", ["capture_ffffffffffff.jpg"])})
        rep2 = cleanup.scan(s, queue=q)
        rep2.pop("_cats")
        cats2 = {c["key"]: c for c in rep2["categories"]}
        ok &= check("큐가 쓰는 f 가 빠져 고아 2개", cats2["capture-orphan"]["files"] == 2,
                    f"실제 {cats2['capture-orphan']['files']}")

        print("\n④ 모의 실행은 아무것도 지우지 않는다")
        before = {p for p in root.rglob("*") if p.is_file()}
        dry = cleanup.sweep(s, apply=False)
        after = {p for p in root.rglob("*") if p.is_file()}
        ok &= check("파일 집합 불변", before == after, f"{len(before)} → {len(after)}")
        ok &= check("그래도 회수량은 보고한다", dry["removed"] > 0 and dry["freed"] > 0)
        ok &= check("applied=False 로 표시", dry["applied"] is False)

        print("\n⑤ 실제 삭제 — 보고한 만큼 정확히 지운다")
        planned = dry["removed"]
        run = cleanup.sweep(s, apply=True)
        gone = len(before) - len({p for p in root.rglob("*") if p.is_file()})
        ok &= check("모의 실행 계획과 실제 삭제 수 일치",
                    planned == run["removed"] == gone, f"계획 {planned} 보고 {run['removed']} 실제 {gone}")
        ok &= check("삭제 실패 0건", not run["failed"], str(run["failed"])[:120])

        print("\n⑥ 지식 저장소는 살아남는다")
        ok &= check("index.json 보존", (s.knowledge_dir / "index.json").is_file())
        ok &= check("원자 .md 보존", (s.knowledge_dir / "atoms" / "atm_deadbeef0000.md").is_file())
        ok &= check("지식 하위의 .tmp 조차 보존(atomic-temp 범주에서도 제외)",
                    (s.knowledge_dir / "atoms" / "stray.tmp").is_file())

        print("\n⑦ 남아야 할 것 / 사라져야 할 것")
        ok &= check("참조된 캡처 a 는 기본 정리에서 살아남는다",
                    (s.captures_dir / "capture_aaaaaaaaaaaa.jpg").is_file())
        ok &= check("최근 캡처 e 살아남음", (s.captures_dir / "capture_eeeeeeeeeeee.jpg").is_file())
        ok &= check("고아 c 삭제됨", not (s.captures_dir / "capture_cccccccccccc.jpg").exists())
        ok &= check("방치된 키 노출 .rv_ 삭제됨", not (s.captures_dir / ".rv_12345678.html").exists())
        ok &= check("렌더 진행 중인 .rv_ 는 보존", (s.captures_dir / ".rv_87654321.html").is_file())
        ok &= check("보고서 HTML 은 기본 정리 대상이 아니다",
                    (s.reports_dir / "report_x.html").is_file())

        print("\n⑧ git 추적 파일 보호")
        subprocess.run(["git", "init", "-q"], cwd=root, check=False, capture_output=True)
        tracked = _write(s.captures_dir / "capture_999999999999.jpg", "Z" * 100, mtime=OLD)
        subprocess.run(["git", "add", "-f", str(tracked.relative_to(root))],
                       cwd=root, check=False, capture_output=True)
        has_git = subprocess.run(["git", "-C", str(root), "ls-files"],
                                 capture_output=True, check=False).stdout.strip()
        if has_git:
            cleanup.sweep(s, apply=True)
            ok &= check("git 추적 중인 캡처는 지워지지 않는다", tracked.is_file())
        else:
            print("  SKIP  git 사용 불가")

        print("\n⑨ 화이트리스트 밖은 손대지 않는다")
        outside = _write(tmp / "outside.jpg", "X" * 100, mtime=OLD)
        cleanup.sweep(s, apply=True)
        ok &= check("프로젝트 밖 파일 보존", outside.is_file())
        ok &= check(".env 보존 (프로젝트 안이지만 정리 대상 아님)", (root / ".env").is_file())
        ok &= check("src/ 소스 보존 (pycache 범주는 *.pyc 만 본다)",
                    (root / "src" / "geoguesshelper" / "server.py").is_file())

        print("\n⑩ 알 수 없는 범주는 거부한다")
        try:
            cleanup.sweep(s, categories=["everything"], apply=True)
            ok &= check("ValueError 발생", False, "예외가 안 났다")
        except ValueError as exc:
            ok &= check("ValueError 발생", "everything" in str(exc))

        os.chdir(prev)
    finally:
        os.chdir(tempfile.gettempdir())
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("전체 통과" if ok else "실패 있음"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
