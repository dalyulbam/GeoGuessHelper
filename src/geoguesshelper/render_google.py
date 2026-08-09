"""Approach A — Playwright 헤드리스로 Google StreetViewPanorama + 지도를 렌더해 스크린샷.

Street View Static / Maps Static API 가 프로젝트에 미활성이어도, 브라우저용 Maps
JavaScript API 키만 있으면 로드뷰 장면을 캡처할 수 있다. mdsearch 의 render_roadview.py
(카카오) 패턴을 구글로 재타깃:  LocalServer 로 HTML 서빙 → wait_for_function(__ready)
→ clip 스크린샷.  브라우저(Chromium)가 직접 타일을 받으므로 서버측 httpx/Static 과 무관.

동기 Playwright 를 워커 스레드(asyncio.to_thread)에서 돌린다 — uvicorn 이벤트 루프 안에서
async Playwright 를 중첩 실행할 때의 Windows Proactor 이슈를 피하기 위함.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from threading import Thread

from .config import Settings

# 상단=로드뷰, 하단=지도(hybrid) — 분석기가 기대하는 2분할 레이아웃 그대로.
_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;background:#111;font-family:sans-serif;}
  #pano{width:__W__px;height:__PH__px;}
  #map{width:__W__px;height:__MH__px;}
  .err{display:flex;align-items:center;justify-content:center;height:100%;
    background:#f5f5f5;color:#a11;font-size:14px;padding:10px;text-align:center;box-sizing:border-box;}
</style>
</head><body>
<div id="pano"></div>
<div id="map"></div>
<script>
window.__ready=false; window.__noPano=false; window.__err=""; window.__stage="script";
function __fail(msg){
  window.__err=String(msg); window.__noPano=true; window.__ready=true;
  var el=document.getElementById('pano'); if(el) el.innerHTML='<div class="err">'+String(msg)+'</div>';
}
// 잘못된 키/리퍼러면 구글이 이 콜백을 부른다 → 25s 대기 대신 즉시 실패.
window.gm_authFailure=function(){ __fail('JS 키 인증 실패 (InvalidKey/RefererNotAllowed)'); };

var USE_PANO=__USE_PANO__, PANO="__PANO__", HAS_LOC=__HAS_LOC__;
var LAT=__LAT__, LNG=__LNG__, HEADING=__HEADING__, PITCH=__PITCH__, ZOOM=__ZOOM__;
var RADIUS=__RADIUS__, SETTLE=__SETTLE__;

function __init(){
  try{
    window.__stage="init";
    var svc=new google.maps.StreetViewService();
    var pano=new google.maps.StreetViewPanorama(document.getElementById('pano'),{
      disableDefaultUI:true, showRoadLabels:false, motionTracking:false,
      linksControl:false, addressControl:false, zoomControl:false, zoom:ZOOM
    });
    function locReq(){ return {location:{lat:LAT,lng:LNG}, radius:RADIUS,
      preference:google.maps.StreetViewPreference.NEAREST,
      sources:[google.maps.StreetViewSource.OUTDOOR]}; }

    function render(data){
      window.__stage="render";
      var loc=data.location.latLng;
      pano.setPano(data.location.pano);
      pano.setPov({heading:HEADING, pitch:PITCH});
      pano.setZoom(ZOOM);
      var map=new google.maps.Map(document.getElementById('map'),{
        center:loc, zoom:15, mapTypeId:'hybrid', disableDefaultUI:true, gestureHandling:'none'
      });
      new google.maps.Marker({position:loc, map:map});
      var panoSettled=false, mapDone=false;
      function ready(){ if(panoSettled && mapDone){ window.__stage="ready"; window.__ready=true; } }
      google.maps.event.addListenerOnce(map,'tilesloaded',function(){ mapDone=true; ready(); });
      setTimeout(function(){ mapDone=true; ready(); }, 7000);   // 지도 타일 지연 대비 폴백
      setTimeout(function(){ panoSettled=true; ready(); }, SETTLE); // 로드뷰 타일 정착 대기
    }
    // 제3자(사용자 기여) 파노는 status=OK 로 응답하지만 **타일을 한 장도 받아오지 못해**
    // 완전히 검은 화면이 된다(실측: 밝기 1.1, 타일 요청 0건). 저작권 문자열이 유일하게
    // 신뢰할 수 있는 판별자다 — 구글 공식은 "© <연도> Google" 형태다.
    function isOfficial(data){
      var c = (data && data.copyright) || '';
      return c.indexOf('Google') !== -1;
    }
    function tryReq(req, allowFallback){
      window.__stage="getPanorama";
      svc.getPanorama(req, function(data,status){
        if(status==='OK'){
          if(!isOfficial(data)){
            window.__thirdParty = (data && data.copyright) || 'unknown';
            // 좌표가 있으면 근처의 **공식** 파노로 갈아탄다. 검은 화면을 캡처하느니
            // 조금 떨어진 실제 이미지가 낫다(보고서에 그 사실을 표시한다).
            if(HAS_LOC && allowFallback !== 'official'){
              window.__stage="officialFallback";
              var r = locReq();
              r.radius = Math.max(RADIUS, 300);
              r.sources = [google.maps.StreetViewSource.GOOGLE];
              svc.getPanorama(r, function(d2,s2){
                if(s2==='OK' && isOfficial(d2)){ window.__switched = d2.location.pano; render(d2); }
                else { __fail('제3자 파노라마만 존재해 이미지를 표시할 수 없습니다 ('+window.__thirdParty+')'); }
              });
              return;
            }
            __fail('제3자 파노라마라 이미지를 표시할 수 없습니다 ('+window.__thirdParty+')');
            return;
          }
          render(data);
        }
        else if(allowFallback && HAS_LOC){ tryReq(locReq(), false); } // 스테일 pano → 좌표 재시도
        else { __fail('스트리트뷰 커버리지 없음 (status='+status+')'); }
      });
    }
    tryReq(USE_PANO ? {pano:PANO} : locReq(), USE_PANO);
  }catch(e){ __fail('렌더 오류: '+(e&&e.message||e)); }
}
</script>
<script async src="https://maps.googleapis.com/maps/api/js?key=__KEY__&v=weekly&callback=__init"></script>
</body></html>
"""


def _make_handler(root: str, only: str):
    """허용된 파일 하나만 응답하는 핸들러. 디렉터리 목록·다른 파일은 전부 404."""
    from http.server import SimpleHTTPRequestHandler

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=root, **k)

        def _allowed(self) -> bool:
            from urllib.parse import unquote, urlparse

            return Path(unquote(urlparse(self.path).path)).name == only

        def do_GET(self):  # noqa: N802
            if not self._allowed():
                self.send_error(404)
                return
            super().do_GET()

        def do_HEAD(self):  # noqa: N802
            if not self._allowed():
                self.send_error(404)
                return
            super().do_HEAD()

        def list_directory(self, path):  # 디렉터리 목록 금지
            self.send_error(404)
            return None

        def log_message(self, *a, **k):  # 조용히
            pass

    return Handler


_MAPS_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;background:#fff}
  .cell{width:__W__px;height:__H__px}
</style></head><body>
__CELLS__
<script>
window.__ready=false; window.__err="";
window.gm_authFailure=function(){ window.__err='JS 키 인증 실패'; window.__ready=true; };
var LEVELS=__LEVELS__, LAT=__LAT__, LNG=__LNG__;
function __init(){
  try{
    var pos={lat:LAT,lng:LNG}, left=LEVELS.length;
    LEVELS.forEach(function(lv,i){
      var m=new google.maps.Map(document.getElementById('m'+i),{
        center:pos, zoom:lv.zoom, mapTypeId:lv.maptype,
        disableDefaultUI:true, gestureHandling:'none', keyboardShortcuts:false
      });
      new google.maps.Marker({position:pos, map:m});
      google.maps.event.addListenerOnce(m,'tilesloaded',function(){ if(--left<=0) window.__ready=true; });
    });
    setTimeout(function(){ window.__ready=true; }, 12000);   // 타일 지연 폴백
  }catch(e){ window.__err=String(e); window.__ready=true; }
}
</script>
<script async src="https://maps.googleapis.com/maps/api/js?key=__KEY__&v=weekly&callback=__init"></script>
</body></html>
"""


def render_locator_maps(lat: float, lng: float, settings: Settings, key: str) -> list[bytes]:
    """줌 단계별 위치 지도를 브라우저로 렌더해 셀별 스크린샷으로 돌려준다.

    Static Maps API 는 서버측 GET 이라 리퍼러 제한이 걸린 JS 키로는 403 이 난다
    (GOOGLE_MAPS_STATIC_KEY 미설정 시 흔함). 캡처가 이미 쓰고 있는 것과 같은 폴백 —
    localhost 로 HTML 을 띄우면 리퍼러 제한을 만족하므로 JS 키만으로 렌더된다.
    브라우저 1회 기동으로 N개 셀을 각각 찍는다.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return []

    levels = settings.report_map_levels or []
    if not levels:
        return []
    w, h = settings.report_map_w, settings.report_map_h
    safe_key = re.sub(r"[^A-Za-z0-9_.\-]", "", key or "")
    cells = "".join(f'<div class="cell" id="m{i}"></div>' for i in range(len(levels)))
    lv_json = "[" + ",".join(
        '{zoom:%d,maptype:"%s"}' % (int(l["zoom"]), re.sub(r"[^a-z]", "", str(l.get("maptype") or "hybrid")))
        for l in levels
    ) + "]"
    html = (_MAPS_HTML
            .replace("__W__", str(w)).replace("__H__", str(h))
            .replace("__CELLS__", cells).replace("__LEVELS__", lv_json)
            .replace("__LAT__", repr(_f(lat, 0.0))).replace("__LNG__", repr(_f(lng, 0.0)))
            .replace("__KEY__", safe_key))

    tmp = settings.captures_dir / f".mp_{uuid.uuid4().hex[:8]}.html"
    tmp.write_text(html, encoding="utf-8")
    server = _LocalServer(settings.captures_dir, tmp.name)
    out: list[bytes] = []
    try:
        server.start()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                ctx = browser.new_context(
                    viewport={"width": w + 40, "height": (h + 20) * len(levels) + 40},
                    device_scale_factor=2, bypass_csp=True, ignore_https_errors=True,
                )
                page = ctx.new_page()
                page.goto(f"{server.url}/{tmp.name}", wait_until="load", timeout=25000)
                page.wait_for_function("window.__ready===true", timeout=30000)
                if page.evaluate("window.__err || ''"):
                    return []
                page.wait_for_timeout(700)      # 라벨/타일 정착
                for i in range(len(levels)):
                    try:
                        out.append(page.locator(f"#m{i}").screenshot(type="jpeg", quality=82))
                    except Exception:  # noqa: BLE001
                        out.append(b"")
            finally:
                browser.close()
    except Exception:  # noqa: BLE001 — 지도는 부가 정보. 실패해도 보고서는 나간다.
        return []
    finally:
        server.stop()
        tmp.unlink(missing_ok=True)
    return out


class _LocalServer:
    """임시 HTML **한 개만** localhost 임시 포트로 잠깐 서빙한다.

    이전 구현의 두 가지 문제를 함께 고친다:
      · 8080/8000 고정 포트를 "비었는지 확인한 뒤 바인드"하는 TOCTOU — 동시 렌더가 같은
        포트를 골랐고, Windows 의 SO_REUSEADDR 는 둘 다 바인드를 허용해 조용히 포트를
        뺏거나(요청이 엉뚱한 서버로) 드물게 WinError 10013 으로 터졌다.
        → 이제 포트 0 을 넘겨 **OS 가 빈 포트를 원자적으로** 할당한다. 경합 자체가 없다.
      · captures/ 디렉터리 전체를 목록까지 붙여 공개했다 — 같은 PC 의 다른 프로세스가 모든
        캡처를 열람할 수 있었다. → 이제 이번 렌더의 임시 파일 1개 외에는 전부 404.

    호스트명은 localhost 를 유지한다. JS 키의 리퍼러 제한이 http://localhost:* 이고,
    127.0.0.1 은 구글이 다른 호스트로 취급하기 때문이다.
    """

    def __init__(self, root: Path, only: str):
        self.root = str(root)
        self.only = only
        self._srv = None
        self._thr: Thread | None = None
        self.url = ""

    def start(self):
        from http.server import ThreadingHTTPServer

        # port 0 → 커널이 사용 가능한 포트를 골라 그대로 바인드(확인-후-바인드 경합 없음)
        self._srv = ThreadingHTTPServer(("localhost", 0), _make_handler(self.root, self.only))
        port = self._srv.server_address[1]
        self.url = f"http://localhost:{port}"
        self._thr = Thread(target=self._srv.serve_forever, daemon=True)
        self._thr.start()

    def stop(self):
        # 절반만 시작된 상태에서 호출돼도 안전해야 한다(start() 가 중간에 실패한 경우).
        srv, self._srv = self._srv, None
        if srv is None:
            return
        try:
            if self._thr is not None and self._thr.is_alive():
                srv.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            srv.server_close()  # 리슨 소켓 반환(포트 누수 방지)
        except Exception:  # noqa: BLE001
            pass


def _top_is_black(path: Path, settings: Settings) -> float | None:
    """캡처 상단(로드뷰 영역)의 평균 밝기. Pillow 없으면 None(판정 생략)."""
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            g = im.convert("L")
            w, h = g.size
            cut = max(1, int(h * settings.viewport_h / max(1, settings.viewport_h + settings.map_h)))
            top = g.crop((0, 0, w, cut))
            data = list(top.get_flattened_data()) if hasattr(top, "get_flattened_data") else list(top.getdata())
            return sum(data) / max(1, len(data))
    except Exception:  # noqa: BLE001
        return None


def _f(x, default):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _zoom_from_pose(pose: dict, settings: Settings) -> float:
    """브라우저 pano.getZoom() 값이 있으면 그대로, 없으면 fov 로부터 근사(zoom≈log2(180/fov))."""
    z = pose.get("zoom")
    if z is not None:
        try:
            return max(0.0, min(5.0, float(z)))
        except (TypeError, ValueError):
            pass
    import math

    fov = _f(pose.get("fov"), settings.default_fov)
    fov = max(15.0, min(120.0, fov))
    return max(0.0, min(5.0, round(math.log2(180.0 / fov), 3)))


def _build_html(pose: dict, settings: Settings, key: str) -> str:
    # pano/key 는 JS 문자열 리터럴/URL 로 들어가므로 안전 문자만 남긴다(</script> 브레이크아웃 방지).
    # 유효한 구글 pano id 는 base64url 계열([A-Za-z0-9_-]) → 무해한 값이 아니면 좌표 경로로 폴백됨.
    pano = re.sub(r"[^A-Za-z0-9_-]", "", pose.get("pano") or "")
    safe_key = re.sub(r"[^A-Za-z0-9_.\-]", "", key or "")
    has_pano = bool(pano)
    has_loc = pose.get("lat") is not None and pose.get("lng") is not None
    lat = _f(pose.get("lat"), 0.0)
    lng = _f(pose.get("lng"), 0.0)
    repl = {
        "__W__": str(settings.viewport_w),
        "__PH__": str(settings.viewport_h),
        "__MH__": str(settings.map_h),
        "__KEY__": safe_key,
        "__USE_PANO__": "true" if has_pano else "false",
        "__PANO__": pano,
        "__HAS_LOC__": "true" if has_loc else "false",
        "__LAT__": repr(lat),
        "__LNG__": repr(lng),
        "__HEADING__": repr(_f(pose.get("heading"), 0.0)),
        "__PITCH__": repr(_f(pose.get("pitch"), 0.0)),
        "__ZOOM__": repr(_zoom_from_pose(pose, settings)),
        "__RADIUS__": str(int(pose.get("radius") or settings.capture_radius_m)),
        "__SETTLE__": "2600",
    }
    html = _HTML
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


def render_sync(pose: dict, settings: Settings, key: str) -> dict:
    """동기 Playwright 렌더(워커 스레드에서 호출). 반환 capture_static 과 동형의 dict."""
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return {
            "status": "NO_DEP",
            "message": "playwright 미설치 — `uv sync --extra all` 후 `playwright install chromium` 하세요.",
        }

    w, total_h = settings.viewport_w, settings.viewport_h + settings.map_h
    jpeg = settings.capture_format == "jpeg"
    fname = f"capture_{uuid.uuid4().hex[:12]}.{'jpg' if jpeg else 'png'}"
    out = settings.captures_dir / fname

    # HTML 은 captures_dir 아래 임시파일로 두고 localhost 로 서빙(파일 URL 아님 → 리퍼러 유지).
    tmp = settings.captures_dir / f".rv_{uuid.uuid4().hex[:8]}.html"
    tmp.write_text(_build_html(pose, settings, key), encoding="utf-8")

    # server.start() 는 반드시 try 안에 있어야 한다. 예전에는 try 바깥이라, start() 가
    # 던지면 finally 의 tmp 정리가 실행되지 않아 API 키가 든 .rv_*.html 이 계속 쌓였고,
    # 예외가 FastAPI 까지 올라가 클라이언트는 JSON 이 아닌 text/plain 500 을 받았다.
    server = _LocalServer(settings.captures_dir, tmp.name)
    try:
        server.start()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = browser.new_context(
                    viewport={"width": w, "height": total_h},
                    device_scale_factor=2,          # 표지판/문자 가독성 ↑ (분석 정확도)
                    bypass_csp=True,
                    ignore_https_errors=True,       # Avast TLS 가로채기 대비
                )
                page = context.new_page()
                logs: list[str] = []
                page.on("console", lambda m: logs.append(f"{m.type}:{m.text}"))
                page.on("pageerror", lambda e: logs.append(f"pageerror:{e}"))
                page.goto(f"{server.url}/{tmp.name}", wait_until="load", timeout=25000)
                try:
                    page.wait_for_function("window.__ready===true", timeout=28000)
                except Exception:  # noqa: BLE001  — 준비 신호 타임아웃
                    stage = page.evaluate("window.__stage || '?'")
                    err = page.evaluate("window.__err || ''")
                    tail = " | ".join(logs[-6:])
                    return {
                        "status": "RENDER_TIMEOUT",
                        "message": f"브라우저 렌더가 준비 신호를 못 냈습니다(타임아웃, stage={stage})."
                        + (f"  err={err}" if err else "")
                        + (f"  console=[{tail}]" if tail else ""),
                    }
                no_pano = page.evaluate("window.__noPano===true")
                err = page.evaluate("window.__err || ''")
                third_party = page.evaluate("window.__thirdParty || ''")
                switched = page.evaluate("window.__switched || ''")
                if no_pano:
                    return {
                        "status": "THIRD_PARTY_ONLY" if third_party else "NO_PANO",
                        "message": err or "이 위치에는 스트리트뷰 커버리지가 없습니다.",
                        "third_party": third_party or None,
                    }
                shot: dict = {
                    "path": str(out),
                    "clip": {"x": 0, "y": 0, "width": w, "height": total_h},
                }
                if jpeg:
                    shot["type"] = "jpeg"
                    shot["quality"] = settings.capture_quality
                page.screenshot(**shot)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "RENDER_ERROR", "message": f"브라우저 렌더 실패: {exc}"}
    finally:
        server.stop()
        tmp.unlink(missing_ok=True)

    if not out.exists() or out.stat().st_size < 2000:
        return {"status": "RENDER_ERROR", "message": "스크린샷이 비었습니다(렌더 실패)."}

    # 최후 방어선 — 위 판별을 빠져나온 검은 화면을 실제 픽셀로 잡는다.
    # (판별 로직이 놓쳐도 검은 이미지를 분석에 보내 비용을 태우는 일은 없어야 한다)
    dark = _top_is_black(out, settings)
    if dark is not None and dark < 6.0:
        out.unlink(missing_ok=True)
        return {
            "status": "BLACK_RENDER",
            "message": (f"로드뷰가 검게 렌더됐습니다(밝기 {dark:.1f})."
                        + (f" 제3자 파노({third_party})라 이미지를 받을 수 없습니다." if third_party else "")),
            "third_party": third_party or None,
        }

    return {
        "status": "OK",
        "file": fname,
        "url": f"/captures/{fname}",
        "third_party": third_party or None,
        "switched_to_official": switched or None,
        "pano_id": switched or pose.get("pano"),
        "lat": pose.get("lat"),
        "lng": pose.get("lng"),
        "heading": pose.get("heading"),
        "pitch": pose.get("pitch"),
        "composited": True,
        "mode": "playwright",
    }
