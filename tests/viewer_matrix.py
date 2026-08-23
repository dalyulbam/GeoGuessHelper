"""로드뷰 실증 스위트 — 지금까지 신고된 모든 링크를 두 조건에서 실제로 띄워 본다.

왜 이 파일이 저장소에 있는가
    이 프로젝트는 "검은 로드뷰"를 여섯 번 고쳤다고 선언하고 여섯 번 다 틀렸다.
    매번 한두 개 링크만 확인하고 나머지로 일반화했기 때문이다.
    이제는 **신고된 모든 링크**를 한 번에 돌리고, 표로 결과를 남긴다.

v0.4.1 에서 추가한 것 — ④ 이동
    "로드뷰맨을 옮겨도 이미지가 직전 장면에 고정된다"는 신고가 또 왔다. 원인은
    watchTiles() 가 <img> 덮개만 걷고 **구체 캔버스는 걷지 않은 것**이었다.
    이 스위트가 그때까지 그걸 못 잡은 이유는 분명하다 — **띄우고 돌려보기만 하고
    이동은 한 번도 시켜보지 않았다.** 그래서 모든 케이스에 이동을 넣는다.
    이동은 사용자가 말한 그 동작 그대로, 지도의 로드뷰맨을 진짜로 끌어다 놓는다.

v0.4.2 에서 추가한 것 — ⑥ 캡처
    이번엔 화면이 아니라 **캡처**가 고정됐다: 이동해도 같은 장면만 찍혔고(실측: 연속 5장이
    353,330바이트로 동일), 때로는 아예 다른 장소가 찍혔다. 원인은 사진구체 링크에서
    동기화(syncArmed)가 한 번도 켜지지 않아 탭 포즈가 링크 값에 얼어붙은 것이었다.
    이 스위트가 또 못 잡은 이유도 같다 — **화면만 보고 캡처는 한 번도 눌러보지 않았다.**
    그래서 이제 이동 전후로 실제 캡처를 찍고, 두 가지를 잰다:
      · 캡처가 이동을 따라오는가 (두 장의 바이트가 다른가)
      · 캡처한 좌표가 **화면이 보여주는 좌표**인가 (80m 이내)
    이 도구에서 "화면과 다른 것을 찍는다"는 조용한 오답이므로, 렌더보다 덜 중요하지 않다.

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
      ④ 이동  로드뷰맨을 진짜로 끌어다 놓으면 화면이 **따라오는가**
      ⑤ 탈출  새로고침 버튼이 구체 캔버스 **위**에 있어 실제로 눌리는가
              (elementFromPoint 로 확인한다. 걷어내는 버튼이 걷어낼 대상에
               가려져 있으면 그건 버튼이 아니라 장식이다.)

그리고 실제 GPU 로 돈다
    헤드리스(SwiftShader)와 실제 GPU 는 캔버스 읽기 동작이 다르다 — 실측:
    같은 화면이 헤드리스에선 mean 117, 실제 GPU 에선 mean 0 으로 읽힌다.
    헤드리스만 보면 사용자 환경을 대표하지 못한다. 그래서 기본이 headed 다.

사용법
    python tests/viewer_matrix.py <포트> [--headless] [--only <키워드>]
"""
from __future__ import annotations

import hashlib
import io
import math
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
    # 우치(폴란드). "이동하다 보면 직전 이미지에 고정된다"는 신고로 들어온 링크.
    ("공식 우치 (이동 신고)", "official",
     "https://www.google.com/maps/@51.7642643,19.4423915,3a,90y,265.83h,91.26t/"
     "data=!3m7!1e1!3m5!1sf558UUQ9nry7Nx3j_8097g!2e0!7i16384!8i8192?entry=ttu"),
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


# 근처에 **실제로 커버리지가 있는** 공식 파노를 찾아 지도를 그 위에 맞춘다.
# 조준은 계산으로 하되 동작은 진짜 드래그다. 아무 데나 떨어뜨리면 파노가 안 바뀌고,
# 그건 "통과"가 아니라 **재현장치 오류**다 — 실제로 처음 시도에서 그렇게 됐다.
_AIM = """async () => {
  if (!window.pano || !window.map) return null;
  const s = new google.maps.StreetViewService();
  const p = pano.getPosition();
  if (!p) return null;
  const cur = String(pano.getPano() || '');
  // 현재 위치에서 그냥 최근접을 찾으면 **자기 자신**이 나온다(공식 파노에서는 늘 그렇다).
  // 그러면 떨어뜨려도 파노가 안 바뀌어 전부 '미판정'이 된다 — 조준이 곧 판정력이다.
  // 그래서 100~350m 쯤 떨어진 지점들을 돌아가며 **다른 파노**를 고른다.
  const offs = [[0.0012,0],[0,0.0017],[-0.0012,0],[0,-0.0017],
                [0.0022,0],[0,0.0031],[-0.0022,0],[0,-0.0031]];
  for (const o of offs) {
    try {
      const { data } = await s.getPanorama({
        location: { lat: p.lat() + o[0], lng: p.lng() + o[1] }, radius: 140,
        preference: google.maps.StreetViewPreference.NEAREST,
        sources: [google.maps.StreetViewSource.OUTDOOR] });
      if (String(data.location.pano) === cur) continue;
      map.setCenter(data.location.latLng); map.setZoom(18);
      return { pano: data.location.pano };
    } catch (e) { /* 이 방향엔 커버리지가 없다 — 다음 */ }
  }
  return null;
}"""

_STATE = """() => {
  const c = document.querySelector('.sphere-canvas');
  const i = document.querySelector('#pano-fallback');
  const id = (window.pano && pano.getPano) ? String(pano.getPano() || '') : '';
  // **화면이 보여주는 좌표.** 캡처를 대조할 기준이므로 거울(currentPose)이 아니라
  // 표시 상태에서 읽는다 — 링크의 사진구체를 띄운 상태면 밑 파노가 아니라 그쪽이 기준이다.
  let exp = { lat: null, lng: null };
  if (typeof photoPinned !== 'undefined' && photoPinned && pinnedPose) {
    exp = { lat: pinnedPose.lat, lng: pinnedPose.lng };
  } else if (window.pano && pano.getPosition && pano.getPosition()) {
    exp = { lat: pano.getPosition().lat(), lng: pano.getPosition().lng() };
  }
  return { shown: id.slice(0, 12), full: id, sphere: !!c,
           fb: !!(i && i.classList.contains('on')),
           armed: (typeof syncArmed !== 'undefined') ? !!syncArmed : null,
           expLat: exp.lat, expLng: exp.lng,
           cp: (typeof panoCopyright !== 'undefined' && window.pano)
               ? String(panoCopyright[id] || '') : '' };
}"""

# 새로고침 버튼이 **실제로 눌리는 자리에 있는가**. 보이는 것과 눌리는 것은 다르다.
_REACH = """() => {
  const b = document.querySelector('#btn-reload-view');
  if (!b) return { there: false, top: null };
  if (b.hidden) return { there: true, top: '(숨김)' };
  const r = b.getBoundingClientRect();
  const el = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  return { there: true, top: el ? (el.id || el.className || el.tagName) : null };
}"""


def rect(pg, sel: str):
    return pg.evaluate("""(s) => { const e = document.querySelector(s); if (!e) return null;
        const r = e.getBoundingClientRect();
        return {x: r.x, y: r.y, width: r.width, height: r.height}; }""", sel)


def meters(alat, alng, blat, blng) -> float | None:
    """두 좌표 사이 거리(m). 하나라도 없으면 None — '재지 못함'과 '가깝다'는 다르다."""
    if None in (alat, alng, blat, blng):
        return None
    dy = (alat - blat) * 111320.0
    dx = (alng - blng) * 111320.0 * math.cos(math.radians((alat + blat) / 2))
    return math.hypot(dx, dy)


def capture_now(pg, wait_s: int = 90) -> dict:
    """캡처 버튼을 실제로 누르고 서버 응답을 받아 온다. 못 누르면 빈 dict."""
    got: dict = {}

    def on_resp(r):
        if "/api/capture" in r.url:
            try:
                got.update(r.json())
            except Exception:  # noqa: BLE001
                pass

    pg.on("response", on_resp)
    try:
        pg.click("#btn-capture", timeout=10000)
    except Exception:  # noqa: BLE001
        pg.remove_listener("response", on_resp)
        return {}
    for _ in range(wait_s):
        if got:
            break
        pg.wait_for_timeout(1000)
    pg.remove_listener("response", on_resp)
    return got


def cap_digest(res: dict) -> str:
    """캡처 파일의 지문. 서버가 저장한 실물을 읽는다 — 응답 필드만 믿지 않는다."""
    f = res.get("file")
    if not f:
        return ""
    p = Path("E:/GeoguessHelper/captures") / f
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else ""


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

        st = pg.evaluate(_STATE)
        reach = pg.evaluate(_REACH)

        # ⑥-1 이동 **전** 캡처 — 뒤에 찍을 것과 비교할 기준선.
        cap_before = capture_now(pg)

        # ④ 이동 — 사용자가 말한 그 동작. 지도의 로드뷰맨을 진짜로 끌어다 놓는다.
        nav = {"tried": False, "pano_changed": None, "screen_changed": None, "sphere_after": None}
        cap = {"before": cap_before, "after": {}, "dist": None, "same_bytes": None}
        png2 = png1
        if pg.evaluate(_AIM):
            time.sleep(2)
            peg = rect(pg, ".gm-svpc")
            mp = rect(pg, "#map")
            if peg and mp:
                nav["tried"] = True
                sx, sy = peg["x"] + peg["width"] / 2, peg["y"] + peg["height"] / 2
                tx, ty = mp["x"] + mp["width"] / 2, mp["y"] + mp["height"] / 2
                pg.mouse.move(sx, sy)
                pg.mouse.down()
                for k in range(1, 21):     # 천천히 — 지도가 커버리지 오버레이를 그릴 시간
                    pg.mouse.move(sx + (tx - sx) * k / 20, sy + (ty - sy) * k / 20)
                    time.sleep(0.05)
                time.sleep(1.0)
                pg.mouse.up()
                time.sleep(13)
                png2, _ = shot()
                st2 = pg.evaluate(_STATE)
                nav["pano_changed"] = st2["full"] != st["full"]
                nav["screen_changed"] = fingerprint(png1) != fingerprint(png2)
                nav["sphere_after"] = st2["sphere"]
                nav["luma"] = mean_luma(png2)

                # ⑥-2 이동 **후** 캡처 — 따라왔는가, 그리고 화면의 좌표를 찍었는가.
                cap["after"] = capture_now(pg)
                cap["dist"] = meters(cap["after"].get("lat"), cap["after"].get("lng"),
                                     st2.get("expLat"), st2.get("expLng"))
                d_before, d_after = cap_digest(cap["before"]), cap_digest(cap["after"])
                cap["same_bytes"] = bool(d_before and d_after and d_before == d_after)

        return {"luma": mean_luma(png0), "luma2": mean_luma(png1),
                "moved": fingerprint(png0) != fingerprint(png1),
                "shot": png0, "shot2": png1, "shot3": png2,
                "reach": reach, "nav": nav, "cap": cap,
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
    print(f"  {'링크':30} {'밝기':>7} {'경로':>7} {'출처':>18} {'드래그':>7} {'이동':>9} {'캡처':>8}  판정")
    print("  " + "-" * 116)

    fails = 0
    unjudged = 0
    for idx, (name, kind, url) in enumerate(cases, 1):
        with sync_playwright() as pw:
            try:
                r = run_one(pw, port, url, headed)
            except Exception as exc:  # noqa: BLE001
                print(f"  {name[:29]:30} {'—':>7} {'—':>7} {'—':>18} {'—':>7} {'—':>9}  "
                      f"✘ 실행오류 {type(exc).__name__}")
                fails += 1
                continue
        # 파일명은 **결정적**이어야 한다 — 해시는 실행마다 달라져 증거로 못 쓴다.
        base = OUT / f"{idx:02d}_{kind}"
        base.with_suffix(".png").write_bytes(r["shot"])
        base.with_name(base.name + "_drag").with_suffix(".png").write_bytes(r["shot2"])
        base.with_name(base.name + "_move").with_suffix(".png").write_bytes(r["shot3"])

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

        # ⑤ 탈출구 — 있기만 해선 안 되고 **눌리는 자리**에 있어야 한다.
        reach = r["reach"]
        if not reach.get("there"):
            bad.append("새로고침 버튼 없음")
        elif reach.get("top") not in ("btn-reload-view",):
            bad.append(f"새로고침 버튼이 가려짐({reach.get('top')})")

        # ④ 이동 — 파노가 안 바뀌었으면 그건 통과가 아니라 **미판정**이다.
        nav = r["nav"]
        if not nav["tried"] or nav["pano_changed"] is None:
            nav_txt, unjudged = "미판정", unjudged + 1
        elif not nav["pano_changed"]:
            nav_txt, unjudged = "미판정", unjudged + 1
        elif not nav["screen_changed"]:
            nav_txt = "✘고정"
            bad.append("이동 후 화면 고정")
        elif nav.get("luma", 999) < BLACK:
            nav_txt = "✘검정"
            bad.append("이동 후 검은 화면")
        else:
            nav_txt = "따라옴"

        # ⑥ 캡처 — "지금 화면 캡처"가 정말 지금 화면인가.
        cap = r["cap"]
        if not cap["after"] or cap["after"].get("status") != "OK":
            why = (cap["after"] or {}).get("status")
            cap_txt = "미판정" if not cap["after"] else f"✘{why}"
            if cap["after"]:
                bad.append(f"이동 후 캡처 실패({why})")
        elif cap["same_bytes"]:
            cap_txt = "✘고정"
            bad.append("캡처가 이동을 안 따라감")
        elif cap["dist"] is None:
            cap_txt = "좌표없음"
        elif cap["dist"] > 80:
            cap_txt = f"✘{cap['dist']:.0f}m"
            bad.append(f"캡처가 화면과 다른 곳({cap['dist']:.0f}m)")
        else:
            cap_txt = f"{cap['dist']:.0f}m"

        if bad:
            fails += 1
        mark = "✔" if not bad else "✘ " + " · ".join(bad)
        print(f"  {name[:29]:30} {r['luma']:7.1f} {route_kind:>7} {src:>18} "
              f"{('예' if r['moved'] else '아니오'):>7} {nav_txt:>9} {cap_txt:>8}  {mark}")

    print()
    tail = f" · 이동 미판정 {unjudged}건" if unjudged else ""
    print(f"  실패 {fails}건{tail} · 스크린샷 {OUT}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
