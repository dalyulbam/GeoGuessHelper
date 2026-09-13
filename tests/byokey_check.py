"""방문자가 넣은 API 키 검사 — 요청·잡까지 전달되고, 어디에도 남지 않는가.

이 파일이 있는 이유: 키를 payload 에 넣으면 jobs.jsonl 에 **평문으로** 남는다.
그래서 난수 핸들만 싣고 키는 메모리에만 두는데, 그 구조가 조용히 깨지면 아무도 모른다.
⑥ 이 그것을 실제 디스크 전수 검색으로 잡는다.

실행:  uv run --no-sync python tests/byokey_check.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ggh_byo_"))
os.environ["GEOHELPER_CAPTURES"] = str(TMP / "captures")
os.environ["GEOHELPER_KNOWLEDGE"] = str(TMP / "knowledge")
os.environ["GEOHELPER_DATA_DIR"] = str(TMP / "data")
os.environ["GEOHELPER_NO_BROWSER"] = "1"
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, r"D:\26y\GeoGuessHelper\src")

from fastapi.testclient import TestClient        # noqa: E402
from geoguesshelper import llm, server           # noqa: E402
from geoguesshelper.config import load_settings  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg, flush=True)
    if not cond:
        FAIL.append(msg)


FAKE_ANT = "sk-ant-api03-" + "Z" * 60
FAKE_OAI = "sk-proj-" + "Q" * 50

s = load_settings()
s.jobs_dir = TMP / "jobs"
s.jobs_dir.mkdir(parents=True, exist_ok=True)
s.anthropic_api_key = ""            # 서버 키 없음 — 헤더 키만으로 동작해야 한다
seen: list = []


async def spy_async(job):
    """핸들러 대신 — 이 잡이 어떤 자격증명을 보는지 기록한다.

    **build_app 전에** 갈아 끼워야 한다. 뒤에 register 로 덮으면 서버가 씌운
    자격증명 래퍼(_byo_job)까지 같이 벗겨져, 정작 검사하려던 경로를 안 지난다.
    """
    c = llm.current_credentials_or_none()
    seen.append((c.provider, c.api_key, c.label) if c else None)
    return {"status": "OK", "reports": []}


server._make_handlers = lambda _s: {"report": spy_async}
app = server.build_app(s)

with TestClient(app) as client:
    print("① 헤더 키가 그 요청의 자격증명이 되는가")
    r = client.get("/api/config", headers={"X-Llm-Key": FAKE_ANT})
    check(r.status_code == 200, "헤더가 있어도 일반 요청은 정상 동작")

    print("\n② 헤더 키가 **잡**까지 가는가 (요청 컨텍스트 밖)")
    r = client.post("/api/jobs/report",
                    json={"files": ["capture_x.jpg"], "label": "t"},
                    headers={"X-Llm-Key": FAKE_ANT})
    check(r.status_code == 200, f"잡 등록 OK ({r.status_code})")
    job_id = r.json()["job"]["id"]
    for _ in range(60):
        if seen:
            break
        time.sleep(0.1)
    check(bool(seen), "잡이 실행됐다")
    if seen:
        prov, key, label = seen[0]
        check(prov == "anthropic", f"제공자를 키 모양으로 알아본다 (={prov})")
        check(key == FAKE_ANT, "잡이 헤더로 온 그 키를 쓴다")
        check(label == "byo", "출처가 'byo' 로 표시된다")

    print("\n③ ChatGPT 키도 알아보는가")
    seen.clear()
    client.post("/api/jobs/report", json={"files": ["y.jpg"], "label": "t2"},
                headers={"X-Llm-Key": FAKE_OAI})
    for _ in range(60):
        if seen:
            break
        time.sleep(0.1)
    check(bool(seen) and seen[0][0] == "openai",
          f"sk-proj- 는 openai 로 (={seen[0][0] if seen else '없음'})")

    print("\n④ 키 없이 부르면 — 조용히 실패하지 않는가")
    seen.clear()
    client.post("/api/jobs/report", json={"files": ["z.jpg"], "label": "t3"})
    for _ in range(60):
        if seen:
            break
        time.sleep(0.1)
    check(bool(seen) and seen[0] is None, "키가 없으면 자격증명도 없다(서버 키로 새지 않는다)")

    print("\n⑤ 요청이 끝나면 컨텍스트에 남지 않는가")
    check(llm.current_credentials_or_none() is None, "요청 밖에서는 자격증명이 없다")
    check(not server._JOB_CREDS, f"끝난 잡의 키를 메모리에서 지웠다 (남은 {len(server._JOB_CREDS)}건)")

print("\n⑥ 평문 키가 디스크에 남지 않는가 — 가장 중요한 검사")
leaked = []
for p in TMP.rglob("*"):
    if not p.is_file():
        continue
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if FAKE_ANT in txt or FAKE_OAI in txt:
        leaked.append(str(p))
check(not leaked, f"jobs.jsonl 등 어디에도 평문 키가 없다 {leaked}")
jl = s.jobs_dir / "jobs.jsonl"
if jl.exists():
    body = jl.read_text(encoding="utf-8", errors="ignore")
    check("_owner" not in body or "sk-" not in body, "잡 로그에 키 흔적 없음")
    print(f"     (jobs.jsonl {len(body)}자 확인)")

import shutil                                    # noqa: E402

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'PASS' if not FAIL else 'FAIL'} — 실패 {len(FAIL)}건")
for f in FAIL:
    print("   ·", f)
sys.exit(1 if FAIL else 0)
