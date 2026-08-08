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
import socket
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
    function tryReq(req, allowFallback){
      window.__stage="getPanorama";
      svc.getPanorama(req, function(data,status){
        if(status==='OK'){ render(data); }
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


class _LocalServer:
    """localhost 로 정적 HTML 을 잠깐 서빙 — 리퍼러가 http://localhost:* 라 JS 키가 동작."""

    _HOSTS = [("localhost", 8080), ("localhost", 8000), ("localhost", 0)]

    def __init__(self, root: Path):
        self.root = str(root)
        self._srv = None
        self._thr: Thread | None = None
        self.url = ""

    def _free(self, host, port):
        try:
            with socket.socket() as s:
                s.bind((host, port))
            return True
        except OSError:
            return False

    def _pick(self):
        for host, port in self._HOSTS:
            if port == 0:
                s = socket.socket(); s.bind((host, 0))
                p = s.getsockname()[1]; s.close()
                return host, p
            if self._free(host, port):
                return host, port
        s = socket.socket(); s.bind(("localhost", 0))
        p = s.getsockname()[1]; s.close()
        return "localhost", p

    def start(self):
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        host, port = self._pick()
        self.url = f"http://{host}:{port}"
        root = self.root

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=root, **k)

            def log_message(self, *a, **k):  # 조용히
                pass

        self._srv = ThreadingHTTPServer((host, port), Handler)
        self._thr = Thread(target=self._srv.serve_forever, daemon=True)
        self._thr.start()

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()  # 리슨 소켓 반환(포트 누수 방지)


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
    fname = f"capture_{uuid.uuid4().hex[:12]}.png"
    out = settings.captures_dir / fname

    # HTML 은 captures_dir 아래 임시파일로 두고 localhost 로 서빙(파일 URL 아님 → 리퍼러 유지).
    tmp = settings.captures_dir / f".rv_{uuid.uuid4().hex[:8]}.html"
    tmp.write_text(_build_html(pose, settings, key), encoding="utf-8")

    server = _LocalServer(settings.captures_dir)
    server.start()
    try:
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
                if no_pano:
                    return {
                        "status": "NO_PANO",
                        "message": err or "이 위치에는 스트리트뷰 커버리지가 없습니다.",
                    }
                page.screenshot(
                    path=str(out),
                    clip={"x": 0, "y": 0, "width": w, "height": total_h},
                )
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "RENDER_ERROR", "message": f"브라우저 렌더 실패: {exc}"}
    finally:
        server.stop()
        tmp.unlink(missing_ok=True)

    if not out.exists() or out.stat().st_size < 2000:
        return {"status": "RENDER_ERROR", "message": "스크린샷이 비었습니다(렌더 실패)."}

    return {
        "status": "OK",
        "file": fname,
        "url": f"/captures/{fname}",
        "pano_id": pose.get("pano"),
        "lat": pose.get("lat"),
        "lng": pose.get("lng"),
        "heading": pose.get("heading"),
        "pitch": pose.get("pitch"),
        "composited": True,
        "mode": "playwright",
    }
