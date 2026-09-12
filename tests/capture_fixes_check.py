"""260912 수정분의 경계 조건을 고정한다 — I-2 · C-2 · C-20 · C-22 · C-32.

이 파일이 있는 이유: 이 넷은 전부 "고쳤다고 적었는데 조건부였던" 계열이다
(docs/report/obstacles-report-map-capture_260911.html §8). 그래서 말이 아니라
코드가 지키게 한다.

실행:  uv run python tests/capture_fixes_check.py
       네트워크가 없으면 TLS 프로브 항목만 skip 하고 나머지는 그대로 돈다.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FAIL: list[str] = []
SKIP: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg, flush=True)
    if not cond:
        FAIL.append(msg)


def skip(msg: str) -> None:
    print("  SKIP  " + msg, flush=True)
    SKIP.append(msg)


# ── I-2  TLS: 검증을 끄기 전에 규격 완화를 거치는가 ──────────────────────
def t_tls() -> None:
    print("① TLS — verify=False 로 가기 전에 한 칸(VERIFY_X509_STRICT)만 푼다")
    from geoguesshelper import tls

    strict = tls.system_ssl_context(strict=True)
    relaxed = tls.system_ssl_context(strict=False)
    flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    check(bool(strict.verify_flags & flag) or not flag, "strict=True 는 X509_STRICT 를 남긴다")
    check(not (relaxed.verify_flags & flag), "strict=False 는 X509_STRICT 만 끈다")
    # 완화해도 '검증'의 본체는 그대로여야 한다 — 이게 verify=False 와 갈리는 지점이다.
    for name, c in (("strict", strict), ("relaxed", relaxed)):
        check(c.verify_mode == ssl.CERT_REQUIRED, f"{name}: verify_mode == CERT_REQUIRED")
        check(c.check_hostname is True, f"{name}: 호스트명 검증 유지")

    mode = tls.decide_tls()
    check(mode in ("secure", "relaxed", "insecure"), f"모드가 세 값 중 하나 (={mode})")
    if mode == "insecure":
        skip("이 환경은 완화로도 검증 실패 — verify=False 경로 확인은 건너뜀")
    else:
        v = tls.httpx_verify("api.anthropic.com")
        check(v is not False,
              f"모드 {mode} 에서 anthropic 연결이 verify=False 가 아니다(키가 오가는 연결)")


# ── C-2  Static 거부 기억이 프로세스 재시작을 넘기는가 ───────────────────
def t_static_deny() -> None:
    print("\n② Static 거부 기억 — 디스크에 남고, 키 값은 남기지 않는다")
    from geoguesshelper import capture as C
    from geoguesshelper.config import Settings

    with tempfile.TemporaryDirectory(prefix="ggh_deny_") as td:
        s = Settings()
        s.captures_dir = Path(td)
        s.static_api_key = "AIzaFAKE_KEY_FOR_TEST_ONLY_000000000000"

        C._static_denied.clear()
        C._deny_loaded.clear()
        C._note_static_status(s, "REQUEST_DENIED")

        p = C._deny_path(s)
        check(p.exists(), "거부를 static_deny.json 에 남긴다")
        body = p.read_text(encoding="utf-8")
        check(s.static_api_key not in body, "파일에 키 값이 없다(지문만 남긴다)")
        check(C._static_key_id(s) in body, "키 지문으로 적는다")

        C._static_denied.clear()          # ← 재시작 흉내
        C._deny_loaded.clear()
        check(C._static_denied_reason(s) == "REQUEST_DENIED", "재시작 뒤에도 기억한다")

        h = C.static_health(s)
        check(h["denied"] == "REQUEST_DENIED" and h["usable"] is False,
              "static_health 가 막힌 상태를 드러낸다")
        check(h["dedicated_key"] is True, "전용 static 키 여부를 구분한다")

        C._note_static_status(s, "OK")
        check(C._static_denied_reason(s) is None, "한 번 통하면 기억을 지운다")
        C._static_denied.clear()
        C._deny_loaded.clear()
        check(C._static_denied_reason(s) is None, "지운 기억은 재시작 뒤에도 없다")

        # TTL 이 지난 기억은 되살리지 않는다 — 사용자가 콘솔에서 켰을 수 있다.
        C._note_static_status(s, "REQUEST_DENIED")
        raw = p.read_text(encoding="utf-8").replace('"at": ', '"at": 1.0 * ')
        import json as _json
        d = _json.loads(p.read_text(encoding="utf-8"))
        for ent in d["denied"].values():
            ent["at"] = time.time() - (C._STATIC_DENY_TTL_S + 60)
        p.write_text(_json.dumps(d), encoding="utf-8")
        C._static_denied.clear()
        C._deny_loaded.clear()
        check(C._static_denied_reason(s) is None, f"TTL({C._STATIC_DENY_TTL_S:.0f}초) 지난 기억은 버린다")
        assert raw  # 위 치환은 쓰지 않는다(형식만 확인)

        # 깨진 파일 하나가 서버 기동을 죽이면 안 된다 — 스키마 해석까지 보호 안에 있는가.
        for bad in ("[]", '{"denied": [1]}', '{"denied": {"x": 3}}', "not json"):
            p.write_text(bad, encoding="utf-8")
            C._static_denied.clear()
            C._deny_loaded.clear()
            try:
                C._static_denied_reason(s)
                C.static_health(s)
                ok = True
            except Exception:  # noqa: BLE001
                ok = False
            check(ok, f"깨진 deny 파일을 견딘다: {bad[:22]}")
        p.unlink(missing_ok=True)

        # 한 프로세스에서 captures_dir 가 바뀌면 **두 번째 파일도 읽어야** 한다.
        # 예전에는 모듈 전역 불리언이라 첫 경로의 캐시를 그대로 썼다.
        with tempfile.TemporaryDirectory(prefix="ggh_deny2_") as td2:
            s2 = Settings()
            s2.captures_dir = Path(td2)
            s2.static_api_key = "AIzaOTHER_KEY_FOR_TEST_00000000000000"
            C._static_denied.clear()
            C._deny_loaded.clear()
            C._note_static_status(s, "REQUEST_DENIED")      # 첫 경로에만 기록
            check(C._static_denied_reason(s) == "REQUEST_DENIED", "첫 경로의 기억이 살아 있다")
            # 두 번째 경로에는 아직 파일이 없다. 여기서 첫 경로의 캐시를 물려받으면 안 된다.
            check(C._static_denied_reason(s2) is None,
                  "다른 captures_dir 는 첫 경로의 캐시를 물려받지 않는다")
            (Path(td2) / C._DENY_FILE).write_text(
                json.dumps({"denied": {C._static_key_id(s2): {"at": time.time(), "why": "NO_KEY"}}}),
                encoding="utf-8")
            C._static_denied.clear()
            C._deny_loaded.clear()
            check(C._static_denied_reason(s2) == "NO_KEY", "두 번째 경로의 파일도 실제로 읽는다")
        p.unlink(missing_ok=True)


# ── C-20  막힌 렌더가 뒤를 막지 않는가 ──────────────────────────────────
def t_worker_stall() -> None:
    print("\n③ 렌더 워커 — 상한에 걸리면 워커를 은퇴시키고 다음이 바로 돈다")
    try:
        import playwright  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        skip("playwright 미설치 — 워커 상한 검사 건너뜀")
        return
    from geoguesshelper import render_google as RG
    from geoguesshelper.config import Settings

    s = Settings()
    s.render_worker_timeout_s = 1.5
    s.render_reuse_browser = True

    pool = RG._BrowserPool()
    before_worker = pool._worker

    t0 = time.time()
    try:
        pool.run(s, lambda _b: time.sleep(20))     # 브라우저 안에서 멎은 상황
        check(False, "상한을 넘기면 RenderStalled 가 나야 한다")
    except RG.RenderStalled:
        dt = time.time() - t0
        check(dt < 5.0, f"상한 1.5초에서 {dt:.2f}초 만에 끊는다")
    check(pool.stalls == 1, "stall 을 계수한다")
    # 요지 — 막힌 일이 끝나기를 기다리면 상한을 둔 의미가 없다(실측 31.69초였다).
    check(pool._worker is not before_worker, "막힌 워커를 새 워커로 갈아 끼운다")

    t1 = time.time()
    got = pool.run(s, lambda _b: "ok")
    dt1 = time.time() - t1
    check(got == "ok", "은퇴 직후에도 다음 작업이 실행된다")
    check(dt1 < 15.0, f"앞의 20초 작업을 기다리지 않는다 ({dt1:.2f}초)")
    pool.shutdown()


# ── C-22 / C-32  정리 대상의 경계 ────────────────────────────────────────
def t_cleanup_bounds() -> None:
    print("\n④ 정리 — 설치 트리 밖은 건드리지 않고, 임시 프로필은 실제 설치에서만")
    from geoguesshelper import cleanup as CL
    from geoguesshelper.config import Settings, project_root

    root = project_root().resolve()

    # ⓐ 캡처 디렉터리 하나만 엉뚱한 곳을 가리키면 허용목록에서 빠져야 한다.
    with tempfile.TemporaryDirectory(prefix="ggh_out_") as td:
        s = Settings()                      # knowledge_dir 등은 기본(프로젝트 안)
        s.captures_dir = Path(td)
        victim = Path(td) / "capture_zzzzzzzzzzzz.jpg"
        victim.write_bytes(b"\xff\xd8" + b"x" * 4000)
        os.utime(victim, (time.time() - 99999,) * 2)
        rep = CL.scan(s)
        rep.pop("_cats", None)
        paths = [i["path"] for c in rep["categories"] for i in c["sample"]]
        check(not any("capture_zzzzzzzzzzzz" in p for p in paths),
              "설치 트리 밖 캡처 디렉터리의 파일은 후보가 아니다")
        check(str(Path(td).resolve()) in (rep.get("outsideRoot") or []),
              "제외한 경로를 outsideRoot 로 밝힌다(침묵하지 않는다)")
        check(not any(c["key"] == "playwright-temp" and c["files"] for c in rep["categories"]),
              "실제 설치가 아니면 머신 전역 %TEMP% 는 건드리지 않는다")

    # ⓑ 통째로 옮긴 설치(지식 저장소와 같은 트리)는 막히지 않아야 한다.
    with tempfile.TemporaryDirectory(prefix="ggh_moved_") as td:
        base = Path(td)
        s = Settings()
        s.captures_dir = base / "captures"
        s.reports_dir = base / "reports"
        s.jobs_dir = base / "jobs"
        s.knowledge_dir = base / "knowledge"
        for d in (s.captures_dir, s.reports_dir, s.jobs_dir, s.knowledge_dir):
            d.mkdir(parents=True, exist_ok=True)
        orphan = s.captures_dir / "capture_yyyyyyyyyyyy.jpg"
        orphan.write_bytes(b"\xff\xd8" + b"y" * 4000)
        os.utime(orphan, (time.time() - 99999,) * 2)
        rep = CL.scan(s)
        rep.pop("_cats", None)
        paths = [i["path"] for c in rep["categories"] for i in c["sample"]]
        check(any("capture_yyyyyyyyyyyy" in p for p in paths),
              "지식 저장소와 같은 트리면 옮긴 설치도 정상 동작한다")
        check(not (rep.get("outsideRoot") or []), "그 경우 outsideRoot 는 비어 있다")

    # ⓒ 지식 저장소는 어떤 경우에도 후보가 아니다(기존 보장의 회귀 확인).
    s = Settings()
    rep = CL.scan(s)
    rep.pop("_cats", None)
    kd = str(s.knowledge_dir.resolve())
    paths = [i["path"] for c in rep["categories"] for i in c["sample"]]
    check(not any(p.startswith(kd) for p in paths), "지식 저장소는 언제나 보호된다")
    check(root.exists(), "project_root() 가 실제 경로를 돌려준다")


def main() -> int:
    for fn in (t_tls, t_static_deny, t_worker_stall, t_cleanup_bounds):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FAIL.append(f"{fn.__name__}: {type(exc).__name__}: {exc}")
            print(f"  FAIL  {fn.__name__} 실행 중 예외 — {type(exc).__name__}: {exc}", flush=True)
    total = len(FAIL)
    print(f"\n{'PASS' if not total else 'FAIL'} — 실패 {total}건"
          + (f" · skip {len(SKIP)}" if SKIP else ""))
    for f in FAIL:
        print("   ·", f)
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
