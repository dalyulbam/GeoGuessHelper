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

# lh3 의 사용자 기여 이미지 토큰. 타일 URL 이든 photo URL 이든 이 접두는 같다.
_GPMS_RE = re.compile(r"(https://lh3\.googleusercontent\.com/gpms-cs-s/[A-Za-z0-9_\-]+)")

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
var RADIUS=__RADIUS__, SETTLE=__SETTLE__, OFFICIAL_ONLY=__OFFICIAL_ONLY__;

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
    // 제3자(사용자 기여) 파노도 **정상적으로 렌더된다**. 다만 타일이 구글 공식 파노와 다른
    // 곳에서 온다: 공식은 streetviewpixels-pa.googleapis.com(키 기반, HTTP 200), 제3자는
    // lh3.googleusercontent.com(키 없는 CDN). 그 CDN 은 IP 단위 레이트리밋이 있어서
    // 짧은 시간에 많이 호출하면 **HTTP 429** 를 주고 화면이 검게 남는다(실측 확인).
    //
    // 그래서 여기서는 제3자라고 미리 갈아타지 않는다 — 요청받은 파노를 그대로 렌더하고,
    // 실제로 검게 나온 경우에만 파이썬 쪽에서 판정한다. 요청한 장소가 아닌 곳을 조용히
    // 분석하는 것이 검은 화면보다 훨씬 나쁘기 때문이다(지오게서 복기 도구다).
    function isOfficial(data){
      var c = (data && data.copyright) || '';
      return c.indexOf('Google') !== -1;
    }
    // 나중에 파이썬이 '무엇을 실제로 렌더했는지' 확인할 수 있게 기록해 둔다.
    function noteInfo(data){
      try{
        window.__renderedPano = data.location.pano;
        window.__copyright = data.copyright || '';
        window.__official = isOfficial(data);
        window.__renderedLat = data.location.latLng.lat();
        window.__renderedLng = data.location.latLng.lng();
        window.__desc = data.location.description || data.location.shortDescription || '';
      }catch(e){}
    }
    function tryReq(req, allowFallback){
      window.__stage="getPanorama";
      svc.getPanorama(req, function(data,status){
        if(status==='OK'){ noteInfo(data); render(data); }
        else if(allowFallback && HAS_LOC){ tryReq(locReq(), false); } // 스테일 pano → 좌표 재시도
        else { __fail('스트리트뷰 커버리지 없음 (status='+status+')'); }
      });
    }
    // OFFICIAL_ONLY 는 재시도용 — 첫 렌더가 검게 나왔을 때만 파이썬이 켠다.
    if(OFFICIAL_ONLY && HAS_LOC){
      var r0 = locReq();
      r0.radius = Math.max(RADIUS, 400);
      r0.sources = [google.maps.StreetViewSource.GOOGLE];
      svc.getPanorama(r0, function(d0,s0){
        if(s0==='OK' && isOfficial(d0)){ noteInfo(d0); render(d0); }
        else { __fail('근처에 구글 공식 스트리트뷰가 없습니다 (status='+s0+')'); }
      });
    } else {
      tryReq(USE_PANO ? {pano:PANO} : locReq(), USE_PANO);
    }
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


_PHOTO_TOKEN_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:#111}#p{width:640px;height:400px}</style></head><body>
<div id="p"></div><script>
window.__done=false;
window.gm_authFailure=function(){ window.__done=true; };
function __init(){
  try{
    var pano=new google.maps.StreetViewPanorama(document.getElementById('p'),
      {pano:"__PANO__", disableDefaultUI:true, motionTracking:false, zoom:1});
    pano.setPov({heading:0, pitch:0});
  }catch(e){}
  setTimeout(function(){ window.__done=true; }, __WAIT__);
}
</script>
<script async src="https://maps.googleapis.com/maps/api/js?key=__KEY__&v=weekly&callback=__init"></script>
</body></html>
"""


def photo_token_for(pano: str, settings: Settings, key: str) -> str | None:
    """제3자 파노의 lh3 이미지 토큰(`…/gpms-cs-s/<토큰>`)을 알아낸다.

    왜 서버가 필요한가 — Maps JS API 는 파노 타일을 **워커에서** 받는다. 그래서 페이지
    안의 PerformanceObserver 에는 그 요청이 한 건도 잡히지 않는다(실측: perfCount=0).
    반면 Playwright 는 CDP 로 브라우저 전체의 요청을 보므로 워커 요청도 그대로 보인다.

    타일이 429 로 막혀도 **요청 URL 자체는 나가므로** 토큰은 확보된다. 그리고 같은
    토큰의 photo 형식(`=w..-h..-k-no-pi..-ya..`)은 그 제한에 걸리지 않는다 —
    실측: 타일 429 인 바로 그 순간 photo 는 HTTP 200, 1280x800, 밝기 118.9.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return None
    safe_pano = re.sub(r"[^A-Za-z0-9_.\-]", "", pano or "")
    if not safe_pano:
        return None
    # 날것의 사진구체 id 는 SDK 가 모른다 — 감싼 형식이어야 렌더가 되고,
    # 렌더가 돼야 타일 요청이 나가고, 그래야 토큰을 주울 수 있다.
    from .linkresolver import wrap_pano_id

    if not safe_pano.startswith("CAoS"):
        alt = wrap_pano_id(safe_pano)
        if alt:
            safe_pano = alt
    html = (_PHOTO_TOKEN_HTML
            .replace("__PANO__", safe_pano)
            .replace("__WAIT__", "5000")
            .replace("__KEY__", re.sub(r"[^A-Za-z0-9_.\-]", "", key or "")))
    tmp = settings.captures_dir / f".pt_{uuid.uuid4().hex[:8]}.html"
    tmp.write_text(html, encoding="utf-8")
    server = _LocalServer(settings.captures_dir, tmp.name)
    found: list[str] = []
    try:
        server.start()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_context(viewport={"width": 660, "height": 420},
                                       ignore_https_errors=True).new_page()

            def _on_req(r):
                if "gpms-cs-s" in r.url and not found:
                    m = _GPMS_RE.search(r.url)
                    if m:
                        found.append(m.group(1))

            page.on("request", _on_req)
            page.goto(f"{server.url}/{tmp.name}", wait_until="load", timeout=25000)
            page.wait_for_function("window.__done===true", timeout=30000)
            browser.close()
    except Exception:  # noqa: BLE001
        return found[0] if found else None
    finally:
        server.stop()
        tmp.unlink(missing_ok=True)
    return found[0] if found else None


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


def _build_html(pose: dict, settings: Settings, key: str, *, official_only: bool = False) -> str:
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
        "__OFFICIAL_ONLY__": "true" if official_only else "false",
    }
    html = _HTML
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


def render_sync(pose: dict, settings: Settings, key: str, *, official_only: bool = False) -> dict:
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
    tmp.write_text(_build_html(pose, settings, key, official_only=official_only), encoding="utf-8")

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
                # 타일 응답 계측 — 제3자 파노는 lh3.googleusercontent.com(키 없는 CDN)에서
                # 오고 IP 단위 레이트리밋에 걸리면 HTTP 429 로 검은 화면이 된다.
                # 공식 파노는 streetviewpixels-pa.googleapis.com(키 기반).
                tiles = {"ok": 0, "err": 0, "codes": {}}
                # 제3자 파노의 gpms 토큰을 **요청 URL에서** 걷어 둔다.
                # 응답이 429여도 요청은 나가므로 토큰은 확보된다 — 그 토큰의
                # photo 형식(=w..-h..-k-no-pi..-ya..)은 타일과 달리 제한을 받지 않는다.
                gpms: list[str] = []

                def _on_req(r):
                    if "gpms-cs-s" in r.url and not gpms:
                        m = _GPMS_RE.search(r.url)
                        if m:
                            gpms.append(m.group(1))

                page.on("request", _on_req)

                def _on_resp(r):
                    host = r.url.split("/")[2] if "//" in r.url else ""
                    if "lh3.googleusercontent" in host or "streetviewpixels" in host or "ggpht" in host:
                        tiles["codes"][r.status] = tiles["codes"].get(r.status, 0) + 1
                        if r.status >= 400:
                            tiles["err"] += 1
                        else:
                            tiles["ok"] += 1

                page.on("response", _on_resp)
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
                info = page.evaluate(
                    "({pano:window.__renderedPano||'',copyright:window.__copyright||'',"
                    "official:!!window.__official,lat:window.__renderedLat,lng:window.__renderedLng,"
                    "desc:window.__desc||''})"
                )
                if no_pano:
                    return {
                        "status": "NO_PANO",
                        "message": err or "이 위치에는 스트리트뷰 커버리지가 없습니다.",
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

    # 검은 화면 판정은 **픽셀로** 한다. 제3자냐 아니냐로 미리 재단하지 않는다 —
    # 제3자 파노도 평소엔 정상 렌더되고, 검게 나오는 원인은 lh3 CDN 의 429 다.
    dark = _top_is_black(out, settings)
    if dark is not None and dark < 8.0:
        out.unlink(missing_ok=True)
        throttled = tiles["codes"].get(429, 0)
        # 타일이 막혔어도 토큰을 잡았다면 photo 형식으로 되살릴 수 있다.
        # (실측: 타일 429 인 순간에도 같은 토큰의 =w..-h..-pi..-ya.. 는 HTTP 200)
        if gpms:
            return {
                "status": "USE_PHOTO_URL",
                "photo_base": gpms[0],
                "message": "타일이 제한돼 직접 이미지 경로로 전환합니다.",
                "tiles": tiles,
                "rendered_pano_id": info.get("pano") or None,
                "copyright": info.get("copyright") or None,
                "lat": info.get("lat"), "lng": info.get("lng"),
            }
        return {
            "status": "TILES_RATE_LIMITED" if throttled else "BLACK_RENDER",
            "message": (
                f"로드뷰 타일을 받지 못했습니다(밝기 {dark:.1f}, HTTP 429 ×{throttled})."
                " 사용자 기여 파노라마는 키 없는 CDN 에서 오며 IP 단위로 제한됩니다 —"
                " 잠시 후 다시 시도하거나 근처 구글 공식 지점으로 이동하세요."
                if throttled else
                f"로드뷰가 검게 렌더됐습니다(밝기 {dark:.1f}, 타일 ok={tiles['ok']} err={tiles['err']})."
            ),
            "third_party": None if info.get("official") else (info.get("copyright") or None),
            "tiles": tiles,
            "rendered_pano_id": info.get("pano") or None,
        }

    # 요청한 파노와 **실제 렌더된 파노가 다르면** 반드시 알린다. 조용히 다른 거리를
    # 분석하는 것이 이 도구에서 가장 나쁜 실패다.
    requested = pose.get("pano") or ""
    rendered = info.get("pano") or ""
    substituted = bool(requested and rendered and requested != rendered)

    return {
        "status": "OK",
        "file": fname,
        "url": f"/captures/{fname}",
        "third_party": None if info.get("official") else (info.get("copyright") or None),
        "copyright": info.get("copyright") or None,
        "official": bool(info.get("official")),
        "substituted": substituted or None,
        "requested_pano_id": requested or None,
        "tiles": tiles,
        # 좌표·pano 는 **실제로 렌더된 것**을 돌려준다. 예전에는 요청값을 그대로 돌려줘
        # 분석기가 다른 장소를 보면서 요청한 좌표라고 믿을 수 있었다.
        "pano_id": rendered or pose.get("pano"),
        "lat": info.get("lat") if info.get("lat") is not None else pose.get("lat"),
        "lng": info.get("lng") if info.get("lng") is not None else pose.get("lng"),
        "heading": pose.get("heading"),
        "pitch": pose.get("pitch"),
        "composited": True,
        "mode": "playwright",
    }
