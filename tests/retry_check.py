"""실패한 작업을 화면에서 되살릴 수 있는가.

이 파일이 있는 이유: 지금까지 실패한 줄은 화면에서 **영영 실패로만 남았다**. 서버가
원래 payload 를 그대로 들고 있는데도 다시 시도할 경로가 없어서, 사용자는 그 장면을
처음부터 다시 잡아야 했다. 실제로 정정 잡 10건이 같은 사유로 실패한 채 큐에 쌓였고
"되살릴 수 없느냐"는 질문이 나왔다(260914).

검사하는 것
  ① 실패한 작업을 다시 시도하면 **정말로 다시 돈다**(핸들러가 한 번 더 불린다)
  ② 끝나지 않은 작업은 다시 시도할 수 없다(진행 중인 걸 복제하면 돈이 두 번 나간다)
  ③ 원래 payload 가 그대로 간다 — 사용자가 다시 입력할 것이 없어야 한다
  ④ client_key 를 새로 만든다. 원래 키를 그대로 쓰면 submit 의 중복 병합이 옛 작업을
     돌려줘(jobs.py:177) '다시 시도' 가 아무 일도 안 한 것처럼 보인다
  ⑤ 없는 작업은 404 — 이력에서 밀려난 경우를 조용히 성공으로 만들지 않는다

실행:  uv run --extra server python tests/retry_check.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ggh_retry_"))
os.environ["GEOHELPER_CAPTURES"] = str(TMP / "captures")
os.environ["GEOHELPER_KNOWLEDGE"] = str(TMP / "knowledge")
os.environ["GEOHELPER_DATA_DIR"] = str(TMP / "data")
os.environ["GEOHELPER_NO_BROWSER"] = "1"
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg, flush=True)
    if not cond:
        FAIL.append(msg)


def main() -> int:
    try:
        from fastapi.testclient import TestClient
    except ModuleNotFoundError:
        print("SKIP - fastapi 미설치")
        return 0

    from geoguesshelper import server
    from geoguesshelper.config import load_settings

    seen: list[dict] = []
    boom = {"on": True}

    async def flaky(job):
        """처음엔 실패, 다시 시도하면 성공 — 되살리기가 실제로 도는지 보려고."""
        seen.append(dict(job.payload or {}))
        if boom["on"]:
            raise RuntimeError("일부러 낸 실패")
        return {"status": "OK", "reports": []}

    s = load_settings()
    s.jobs_dir = TMP / "jobs"          # 진짜 작업 로그를 건드리지 않는다
    s.jobs_dir.mkdir(parents=True, exist_ok=True)
    server._make_handlers = lambda _s: {"report": flaky}
    app = server.build_app(s)

    with TestClient(app) as c:
        print("① 실패한 작업을 다시 시도하면 정말로 다시 도는가")
        r = c.post("/api/jobs/report",
                   json={"files": ["x.jpg"], "label": "원본", "lang": "ko"})
        check(r.status_code == 200, f"등록 OK ({r.status_code})")
        jid = r.json()["job"]["id"]
        for _ in range(80):
            j = c.get(f"/api/jobs/{jid}").json()["job"]
            if j["status"] in ("FAILED", "DONE"):
                break
            time.sleep(0.1)
        check(j["status"] == "FAILED", f"의도대로 실패했다 ({j['status']})")
        check(len(seen) == 1, f"핸들러가 한 번 돌았다 ({len(seen)})")

        boom["on"] = False             # 이번엔 성공하게
        rr = c.post(f"/api/jobs/{jid}/retry")
        check(rr.status_code == 200, f"다시 시도 등록 OK ({rr.status_code})")
        new_id = rr.json()["job"]["id"]
        check(new_id != jid, "새 작업 id 를 준다(옛 줄을 덮어쓰지 않는다)")
        check(rr.json().get("retriedFrom") == jid, "어느 작업에서 되살렸는지 알려 준다")
        for _ in range(80):
            j2 = c.get(f"/api/jobs/{new_id}").json()["job"]
            if j2["status"] in ("FAILED", "DONE"):
                break
            time.sleep(0.1)
        check(j2["status"] == "DONE", f"다시 시도는 성공했다 ({j2['status']})")
        check(len(seen) == 2, f"핸들러가 한 번 더 돌았다 ({len(seen)})")

        print("\n③ 원래 payload 가 그대로 가는가")
        check(seen[1].get("files") == ["x.jpg"] and seen[1].get("lang") == "ko",
              f"사용자가 다시 입력할 것이 없다 {seen[1].get('files')} {seen[1].get('lang')}")

        print("\n④ client_key 를 새로 만드는가")
        # 원래 키를 그대로 쓰면 submit 이 옛 작업을 돌려줘 '다시 시도' 가 무동작이 된다.
        check("clientKey" not in seen[1] or seen[1]["clientKey"] != seen[0].get("clientKey"),
              "되살린 작업은 옛 client_key 를 물려받지 않는다")

        print("\n② 끝나지 않은 작업은 다시 시도할 수 없는가")
        boom["on"] = True
        r3 = c.post("/api/jobs/report", json={"files": ["y.jpg"], "label": "두번째"})
        jid3 = r3.json()["job"]["id"]
        again = c.post(f"/api/jobs/{jid3}/retry")
        # 이미 끝났으면 200, 아직 도는 중이면 409 — 둘 다 '조용히 복제' 는 아니어야 한다
        if again.status_code == 409:
            check(True, "진행 중인 작업은 409 로 거절한다")
        else:
            done = c.get(f"/api/jobs/{jid3}").json()["job"]["status"]
            check(done in ("FAILED", "CANCELED"),
                  f"200 이었다면 그 작업은 이미 끝나 있어야 한다 ({done})")

        print("\n⑤ 없는 작업")
        r4 = c.post("/api/jobs/job_없는거/retry")
        check(r4.status_code == 404, f"404 로 분명히 말한다 ({r4.status_code})")

    print("\n⑥ 진짜 작업 로그를 건드리지 않았는가")
    real = Path(__file__).resolve().parents[1] / "docs" / "jobs" / "jobs.jsonl"
    check(s.jobs_dir == TMP / "jobs", f"작업 로그가 임시 폴더에 있다 ({s.jobs_dir})")
    if real.exists():
        body = real.read_text(encoding="utf-8", errors="ignore")
        check("원본" not in body and "두번째" not in body,
              "실제 docs/jobs/jobs.jsonl 에 이 검사의 흔적이 없다")

    import shutil

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'PASS' if not FAIL else 'FAIL'} — 실패 {len(FAIL)}건")
    for f in FAIL:
        print("   ·", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
