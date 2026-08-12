"""로드뷰 실증 스위트 — 지금까지 신고된 모든 링크를 두 조건에서 실제로 띄워 본다.

왜 이 파일이 저장소에 있는가
    이 프로젝트는 "검은 로드뷰"를 여섯 번 고쳤다고 선언하고 여섯 번 다 틀렸다.
    매번 한두 개 링크만 확인하고 나머지로 일반화했기 때문이다.
    이제는 **신고된 모든 링크**를 한 번에 돌리고, 표로 결과를 남긴다.

무엇을 재는가 — 그리고 무엇을 재지 **못하는가**
    사용자의 실제 고장은 "타일이 429 로 거절되는 상태"다. 그런데 그 상태를 Playwright 로
    만들 수 없다는 것을 실측으로 확인했다: 요청을 가로채는 순간(page.route 든 context.route 든)
    타일 파이프라인이 통째로 죽어 `canvases: 0`, gpms 요청 0건이 된다. 화면도 사용자가 본
    **검정**이 아니라 **회색 빈 화면**이 된다 — 다른 고장이다.

    그래서 재현할 수 없는 조건을 흉내 내는 대신, **검증할 수 있는 성질**을 잰다:
      ① 렌더  제3자 파노가 실제로 그려지는가 (밝기)
      ② 출처  그 픽셀이 **같은 출처(/api/pano-image)** 를 통해 왔는가.
              그렇다면 클라이언트가 lh3 에 못 닿아도 화면은 산다 — 논리로 보장된다.
      ③ 조작  진짜 마우스 드래그로 화면이 바뀌는가 (그림이 아니라 뷰어인가)

그리고 실제 GPU 로 돈다
    헤드리스(SwiftShader)와 실제 GPU 는 캔버스 읽기 동작이 다르다 — 실측:
    같은 화면이 헤드리스에선 mean 117, 실제 GPU 에선 mean 0 으로 읽힌다.
    헤드리스만 보면 사용자 환경을 대표하지 못한다. 그래서 기본이 headed 다.

사용법
    python tests/viewer_matrix.py <포트> [--headless] [--only <키워드>]
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

OUT = Path("E:/GeoguessHelper/.testout")

# 신고된 링크 전부. 새 신고가 오면 여기에 **먼저** 추가하고 돌린다.
CASES = [
    ("공식 KKMP (회귀)", "official",
     "https://www.google.com/maps/place/x/@45.1002454,14.6367304,3a,75y,355.18h,80.56t/"
     "data=!3m10!1e1!3m8!1sKKMPlm4XaYHeOHZOvkFG2A!2e0!7i16384!8i8192!5m1!1e4?entry=ttu"),
    ("사진구체 CIHM-A", "photosphere",
     "https://www.google.com/maps/place/x/@45.0999,14.6296348,3a,75y,36.84h,103.37t/"
     "data=!3m11!1e1!3m9!1sCIHM0ogKEICAgIDqvbDfZw!2e10!3e11!6shttps:%2F%2Flh3.googleusercontent.com"
     "%2Fgpms-cs-s%2FAFP8RcNwCznh1djIlRleIJ6SQGkuyZ6KzrOLjRMBXf6sCGCMJJwXsv2_216UMBdYZ51uzp1yirbEOL"
     "Yqs8HgHAB31zzesTbM-3x3QWBXoUjnqJPK26tLVj6cTXetrFmibISlTJPLQ_al%3Dw900-h600-k-no-"
     "pi-13.365798953196943-ya18.840502585634653-ro0-fo100!7i8704!8i4352!5m1!1e4?entry=ttu"),
    ("사진구체 CIHM-B", "photosphere",
     "https://www.google.com/maps/place/x/@44.2989931,15.0277781,3a,90y,326.81h,52.78t/"
     "data=!3m11!1e1!3m9!1sCIHM0ogKEICAgIDq7a7OswE!2e10!3e11!6shttps:%2F%2Flh3.googleusercontent.com"
     "%2Fgpms-cs-s%2FAFP8RcPGRvTApnmCic6rEfwByD82MDcVeqlE2PY1Efl3tKr9fZPVScTKFi-MWcI0mLqMh5MroAXX-"
     "_6H91qymAkXyhXzX8kaq-Mhlci-QF1D6RLu5SMh1YAx7m5y1SWHM_pMKo24CxMdBw%3Dw900-h600-k-no-"
     "pi37.22060526831795-ya326.8120340355348-ro0-fo100!7i10203!8i5102!5m1!1e4?entry=ttu"),
    ("사진구체 CIAB (!6s 없음)", "photosphere-no6s",
     "https://www.google.com/maps/@44.2790173,15.2841005,0a,73.7y,264.58h,83.9t/"
     "data=!3m4!1e1!3m2!1sCIABIhC3KIRGTQtfjhRG2LU3AfZw!2e10?source=apiv3"),
    ("사진구체 CIHM-C (파슈키 모스트)", "photosphere",
     "https://www.google.com/maps/place/x/@44.3258869,15.2571344,3a,75y,181.95h,87.79t/"
     "data=!3m11!1e1!3m9!1sCIHM0ogKEICAgICk0J2fLg!2e10!3e11!6shttps:%2F%2Flh3.googleusercontent.com"
     "%2Fgpms-cs-s%2FAFP8RcN89y29_QkCFXHDNQdXGG4-h6zhknVhMPLIPTVhUHZ8VaxgM5Z_EOidJHOTBOoGPbkE5n9HyV"
     "8fxc6IsyBORj87AgJr29N4KaswCG1AE_L6d9RSVb4Eb-9EZuorQ7QI8taJOG75%3Dw900-h600-k-no-"
     "pi2.214662455377635-ya49.950557494009814-ro0-fo100!7i8704!8i4352!5m1!1e4?entry=ttu"),
]

BLACK = 12.0        # 이 밝기 아래는 사실상 검은 화면
SETTLE = 30         # 파노 해석(2왕복) + 타일 감시(8s) + 서버 토큰(≈8s) + 이미지까지


def fingerprint(png: bytes) -> tuple:
    """화면 지문 — 밝기가 아니라 **변화**를 재기 위한 것."""
    im = Image.open(io.BytesIO(png)).convert("L").resize((8, 8))
    return tuple(im.getdata())


def mean_luma(png: bytes) -> float:
    im = Image.open(io.BytesIO(png)).convert("L")
    d = list(im.getdata())
    return sum(d) / len(d)


def run_one(pw, port: int, url: str, headed: bool) -> dict:
    args = ["--no-sandbox"] + ([] if headed else ["--use-gl=swiftshader"])
    b = pw.chromium.launch(headless=not headed, args=args)
    try:
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        direct, proxied = [], []
        ctx.on("response", lambda r: (
            proxied.append(r.status) if "/api/pano-image" in r.url
            else (direct.append(r.status) if "googleusercontent.com" in r.url else None)))
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{port}/", wait_until="load", timeout=40000)
        pg.fill("#url", url)
        pg.click("#btn-extract")
        time.sleep(SETTLE)

        def shot():
            box = pg.evaluate("""() => { const r = document.querySelector('#pano').getBoundingClientRect();
                return {x: r.x, y: r.y, width: r.width, height: r.height}; }""")
            return pg.screenshot(clip=box), box

        png0, box = shot()

        # ③ 진짜 마우스 드래그 — 프로그램적 setPov 는 쓰지 않는다.
        #    예전에 setPov 로 "회전 확인"했다가 사용자가 못 끄는 그림을 배포했다.
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        pg.mouse.move(cx, cy)
        pg.mouse.down()
        for i in range(1, 16):
            pg.mouse.move(cx - i * 20, cy)
            time.sleep(0.02)
        pg.mouse.up()
        time.sleep(3)
        png1, _ = shot()

        st = pg.evaluate("""() => {
            const c = document.querySelector('.sphere-canvas');
            const i = document.querySelector('#pano-fallback');
            return { shown: (window.pano && pano.getPano) ? String(pano.getPano() || '').slice(0, 12) : '',
                     sphere: !!c, fb: !!(i && i.classList.contains('on')),
                     cp: (typeof panoCopyright !== 'undefined' && window.pano)
                         ? (panoCopyright[pano.getPano()] || '') : '' };
        }""")
        return {"luma": mean_luma(png0), "luma2": mean_luma(png1),
                "moved": fingerprint(png0) != fingerprint(png1),
                "shot": png0, "shot2": png1,
                "direct": direct, "proxied": proxied, **st}
    finally:
        b.close()


def main() -> int:
    port = int(sys.argv[1])
    headed = "--headless" not in sys.argv
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    OUT.mkdir(parents=True, exist_ok=True)

    cases = [c for c in CASES if not only or only in c[0] or only in c[1]]
    print(f"  환경: {'실제 GPU' if headed else '헤드리스'} · 링크 {len(cases)}개")
    print()
    print(f"  {'링크':30} {'밝기':>7} {'경로':>7} {'출처':>18} {'드래그':>7}  판정")
    print("  " + "-" * 92)

    fails = 0
    for idx, (name, kind, url) in enumerate(cases, 1):
        with sync_playwright() as pw:
            try:
                r = run_one(pw, port, url, headed)
            except Exception as exc:  # noqa: BLE001
                print(f"  {name[:29]:30} {'—':>7} {'—':>7} {'—':>18} {'—':>7}  ✘ 실행오류 {type(exc).__name__}")
                fails += 1
                continue
        # 파일명은 **결정적**이어야 한다 — 해시는 실행마다 달라져 증거로 못 쓴다.
        base = OUT / f"{idx:02d}_{kind}"
        base.with_suffix(".png").write_bytes(r["shot"])
        base.with_name(base.name + "_drag").with_suffix(".png").write_bytes(r["shot2"])

        third = bool(r["cp"]) and "Google" not in r["cp"]
        route_kind = "sphere" if r["sphere"] else ("img" if r["fb"] else "google")
        src = f"프록시{len(r['proxied'])}·직접{len(r['direct'])}"

        bad = []
        if r["luma"] < BLACK:
            bad.append("검은 화면")
        if third and not r["sphere"]:
            bad.append("제3자인데 자체 뷰어 아님")
        if third and not r["proxied"]:
            bad.append("프록시를 안 씀")
        if not r["moved"]:
            bad.append("드래그 무반응")
        if bad:
            fails += 1
        mark = "✔" if not bad else "✘ " + " · ".join(bad)
        print(f"  {name[:29]:30} {r['luma']:7.1f} {route_kind:>7} {src:>18} "
              f"{('예' if r['moved'] else '아니오'):>7}  {mark}")

    print()
    print(f"  실패 {fails}건 · 스크린샷 {OUT}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
