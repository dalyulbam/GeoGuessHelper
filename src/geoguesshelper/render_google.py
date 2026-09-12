"""Approach A — Playwright 헤드리스로 Google StreetViewPanorama + 지도를 렌더해 스크린샷.

Street View Static / Maps Static API 가 프로젝트에 미활성이어도, 브라우저용 Maps
JavaScript API 키만 있으면 로드뷰 장면을 캡처할 수 있다. mdsearch 의 render_roadview.py
(카카오) 패턴을 구글로 재타깃:  LocalServer 로 HTML 서빙 → wait_for_function(__ready)
→ clip 스크린샷.  브라우저(Chromium)가 직접 타일을 받으므로 서버측 httpx/Static 과 무관.

동기 Playwright 를 워커 스레드에서 돌린다 — uvicorn 이벤트 루프 안에서 async Playwright 를
중첩 실행할 때의 Windows Proactor 이슈를 피하기 위함. 그 스레드는 _RenderWorker 가 하나만
유지하며(아래), 브라우저도 거기서 한 번만 띄워 재사용한다.
"""
from __future__ import annotations

import atexit
import queue
import re
import time
import uuid
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from threading import Lock, Thread, current_thread

from .config import Settings


class RenderStalled(RuntimeError):
    """렌더가 워커 상한을 넘겼다 — 호출부가 RENDER_TIMEOUT 으로 바꿔 돌려준다."""


_STOP = object()   # _RenderWorker 은퇴 sentinel

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
var MAPWAIT=__MAPWAIT__;

// 하단 지도는 로드뷰와 **동시에** 띄운다.
// 예전에는 getPanorama 가 돌아온 뒤에야 지도를 만들었다. 그래서 지도 타일 시간이
// 파노 조회 시간 **뒤에** 붙어 임계경로가 됐고, ready 조건이 그 둘의 합이었다.
// 좌표를 이미 알고 있으면(HAS_LOC) 기다릴 이유가 없다 — 먼저 띄우고, 실제 파노 위치가
// 나오면 그때 중심만 옮긴다. 옮겨도 같은 줌의 인접 타일이라 대개 이미 받아 둔 것이다.
var __map=null, __mapCenter=null, __mapDone=false, __panoSettled=false;
function __mapReady(){ __mapDone=true; __maybeReady(); }
function __maybeReady(){
  if(__panoSettled && __mapDone){ window.__stage="ready"; window.__ready=true; }
}
function __near(a,b){                 // 같은 자리로 볼 만한가(약 20m)
  try{
    var la=(typeof a.lat==='function')?a.lat():a.lat, ln=(typeof a.lng==='function')?a.lng():a.lng;
    var lb=(typeof b.lat==='function')?b.lat():b.lat, nb=(typeof b.lng==='function')?b.lng():b.lng;
    return Math.abs(la-lb)<2e-4 && Math.abs(ln-nb)<2e-4;
  }catch(e){ return false; }
}
function __startMap(center){
  if(__map){
    // 실제 위치가 달라졌으면(스테일 pano 좌표 폴백·공식 파노 대체) 이전 '준비됨'을
    // 재사용하면 안 된다 — 새 타일이 오기 전에 찍힌다. 준비 상태를 다시 세운다.
    if(center && !__near(center,__mapCenter)){
      __mapCenter=center; __mapDone=false; __map.setCenter(center);
      google.maps.event.addListenerOnce(__map,'tilesloaded',__mapReady);
      setTimeout(__mapReady, MAPWAIT);
    }
    return __map;
  }
  __mapCenter=center;
  __map=new google.maps.Map(document.getElementById('map'),{
    center:center, zoom:15, mapTypeId:'hybrid', disableDefaultUI:true, gestureHandling:'none'
  });
  google.maps.event.addListenerOnce(__map,'tilesloaded',__mapReady);
  setTimeout(__mapReady, MAPWAIT);   // 지도 타일 지연 상한 — 이제 병렬 경로의 상한일 뿐이다
  return __map;
}

function __init(){
  try{
    window.__stage="init";
    var svc=new google.maps.StreetViewService();
    var pano=new google.maps.StreetViewPanorama(document.getElementById('pano'),{
      disableDefaultUI:true, showRoadLabels:false, motionTracking:false,
      linksControl:false, addressControl:false, zoomControl:false, zoom:ZOOM
    });
    if(HAS_LOC) __startMap({lat:LAT,lng:LNG});   // ← 파노 조회를 기다리지 않는다
    function locReq(){ return {location:{lat:LAT,lng:LNG}, radius:RADIUS,
      preference:google.maps.StreetViewPreference.NEAREST,
      sources:[google.maps.StreetViewSource.OUTDOOR]}; }

    function render(data){
      window.__stage="render";
      var loc=data.location.latLng;
      pano.setPano(data.location.pano);
      pano.setPov({heading:HEADING, pitch:PITCH});
      pano.setZoom(ZOOM);
      var map=__startMap(loc);          // 이미 떠 있으면 중심만 옮긴다
      new google.maps.Marker({position:loc, map:map});
      setTimeout(function(){ __panoSettled=true; __maybeReady(); }, SETTLE);
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


# ── 헤드리스 브라우저 풀 ──────────────────────────────────────────────────────
#
# 예전에는 렌더 한 건마다 Chromium 을 띄웠다 닫았다. 그 close 가 느렸다.
#
# 실측(Windows 11 + Avast 실시간 보호, 2026-09-10): 렌더 자체는 4.60초에 끝나는데
# 벽시계는 52.90초였다. 차이 47초가 전부 browser.close() 다. 페이지를 **하나도 열지 않은**
# 빈 브라우저를 띄웠다 닫기만 해도 15.98초 / 65.55초가 걸렸다(3회 중 1회만 0.16초) —
# 렌더·네트워크와 무관하다. 백신이 %TEMP% 의 크로미움 임시 프로필을 스캔·잠그는 동안
# 삭제가 지연되는 것으로, 실제로 지워지지 못한 playwright_chromiumdev_profile-* 가
# 47개 쌓여 있었다.
#
# 그래서 브라우저를 프로세스 수명 동안 재사용한다. 이 비용이 "캡처마다" 에서 "종료 시 1회"
# 로 옮겨간다. 컨텍스트까지 재사용하면 HTTP 캐시가 살아 Maps JS(28건 468KB) 재다운로드도
# 사라진다 — 실측 goto 1.65초 → 0.71초.
#
# Playwright 동기 API 객체는 **만든 스레드에 묶인다**. asyncio.to_thread 는 호출마다 다른
# 스레드를 줄 수 있으므로, 전용 워커 스레드 하나에서 모든 브라우저 작업을 돌린다.
# 데몬 스레드라 종료가 느려도 프로세스 종료를 막지 않는다.


def _connected(browser) -> bool:
    try:
        return bool(browser.is_connected())
    except Exception:  # noqa: BLE001 — 드라이버가 이미 죽었으면 연결도 물어볼 수 없다
        return False


class _RenderWorker:
    """모든 동기 Playwright 호출을 담당하는 단일 데몬 스레드."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._thr: Thread | None = None
        self._lock = Lock()
        self._retired = False

    def _loop(self) -> None:
        while True:
            fut, fn = self._q.get()
            if fn is _STOP:
                # 은퇴 신호. 남아 있는(아직 시작도 안 한) 일은 실행하지 않고 실패로 끝낸다 —
                # 예전에는 그대로 실행돼, 호출부가 이미 임시파일·로컬서버를 정리한 뒤에
                # 뒤늦게 캡처 파일을 만들 수 있었다.
                while True:
                    try:
                        f2, _ = self._q.get_nowait()
                    except queue.Empty:
                        break
                    if f2.set_running_or_notify_cancel():
                        f2.set_exception(RenderStalled("렌더 워커가 은퇴해 이 작업은 실행되지 않았습니다."))
                return
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 — 호출자에게 그대로 넘긴다
                fut.set_exception(exc)

    def submit(self, fn) -> Future:
        with self._lock:
            if self._retired:
                raise RenderStalled("은퇴한 렌더 워커에는 작업을 넣지 않는다.")
            if self._thr is None or not self._thr.is_alive():
                self._thr = Thread(target=self._loop, name="pw-render", daemon=True)
                self._thr.start()
        fut: Future = Future()
        self._q.put((fut, fn))
        return fut

    def retire(self) -> None:
        """더는 받지 않고, 지금 도는 일이 끝나면 스레드를 끝낸다.

        막힌 일은 취소할 수 없다(동기 Playwright). 그래서 '죽이기'가 아니라 '끝나면
        나가기'다 — 그 일이 언젠가 끝나면 남은 큐를 비우고 스레드가 반환된다.
        예전 판은 sentinel 이 없어 스레드가 queue.get() 에서 영구 대기했고,
        타임아웃 한 번마다 하나씩 쌓였다.
        """
        with self._lock:
            if self._retired:
                return
            self._retired = True
        self._q.put((Future(), _STOP))

    def is_current(self) -> bool:
        return current_thread() is self._thr


def _one_shot(fn):
    """재사용을 끈 경우(render_reuse_browser=False) — 예전처럼 매번 띄우고 닫는다."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            return fn(browser)
        finally:
            browser.close()


class _BrowserPool:
    """워커 스레드 위에서 Chromium 한 개(+ 컨텍스트)를 살려 둔다."""

    def __init__(self) -> None:
        self._worker = _RenderWorker()
        self._pw = None
        self._browser = None
        self._ctx = None
        self._ctx_key: tuple | None = None
        self.launches = 0
        self.stalls = 0          # 워커 상한에 걸려 브라우저를 버린 횟수(진단용)
        self._gen = 0            # 워커 세대 — 은퇴가 한 번만 일어나게 한다
        self._gen_lock = Lock()

    # 아래 _* 메서드는 **워커 스레드 위에서만** 불린다.
    def _browser_on_worker(self):
        from playwright.sync_api import sync_playwright

        if self._browser is not None and _connected(self._browser):
            return self._browser
        self._forget()
        if self._pw is None:
            self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True, args=["--no-sandbox"])
        self.launches += 1
        return self._browser

    def _forget(self) -> None:
        """죽었다고 판단한 핸들을 버린다. close 는 시도만 하고 실패는 삼킨다."""
        self._ctx = None
        self._ctx_key = None
        b, self._browser = self._browser, None
        if b is not None:
            try:
                b.close()
            except Exception:  # noqa: BLE001
                pass

    def _retire(self, gen: int) -> None:
        """막힌 워커를 통째로 은퇴시키고 새 워커를 세운다.

        `gen` 은 호출자가 **작업을 넣을 때** 본 세대다. 그 사이 다른 호출자가 이미
        교체했다면 아무것도 하지 않는다 — 예전 판은 세대 검사가 없어서, 같은 막힌
        워커를 기다리던 두 호출 중 두 번째가 **멀쩡한 새 워커를 은퇴시켰다**.
        stalls 도 실제 멎은 워커 수가 아니라 대기 호출 수만큼 부풀었다.

        동기 Playwright 객체는 **만든 스레드에 묶여** 있어서 밖에서 그 브라우저를 닫을 수
        없다. 그 스레드에 정리 작업을 넣어 봐야 막힌 일 **뒤에 줄을 설 뿐**이다 —
        실측(260912): 그렇게 했더니 다음 캡처가 31.69초를 기다렸다. 앞의 일이 끝나기를
        기다린 것이고, 상한을 둔 의미가 없어진다.

        그래서 참조를 끊고 새 워커·새 브라우저로 간다. 옛 워커는 버려지지 않는다 —
        큐에 정리 작업을 하나 넣어 두므로, 막힌 일이 언젠가 끝나면 그때 스스로 닫는다.
        """
        with self._gen_lock:
            if gen != self._gen:
                return                  # 이미 다른 호출자가 갈아 끼웠다
            old, pw, browser = self._worker, self._pw, self._browser
            self._gen += 1
            self.stalls += 1
            self._worker = _RenderWorker()
            self._pw = None
            self._browser = None
            self._ctx = None
            self._ctx_key = None

        def _close_old():
            for obj, meth in ((browser, "close"), (pw, "stop")):
                if obj is None:
                    continue
                try:
                    getattr(obj, meth)()
                except Exception:  # noqa: BLE001
                    pass

        try:
            old.submit(_close_old)      # 막힌 일이 끝나는 순간 실행된다
        except Exception:  # noqa: BLE001
            pass
        old.retire()                    # 그 뒤 스레드를 끝낸다(누수 방지)

    def context(self, browser, key: tuple, **kw):
        """같은 설정이면 컨텍스트를 재사용한다 — 캐시가 살아 Maps JS 를 다시 안 받는다."""
        if self._ctx is not None and self._ctx_key == key:
            try:
                if self._ctx in browser.contexts:
                    return self._ctx
            except Exception:  # noqa: BLE001
                pass
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:  # noqa: BLE001
                pass
        self._ctx = browser.new_context(**kw)
        self._ctx_key = key
        return self._ctx

    def run(self, settings: Settings, fn):
        """fn(browser) 를 워커 스레드에서 실행한다.

        브라우저가 죽어 있어서 실패한 경우에만 한 번 다시 띄워 재시도한다. 살아 있는데
        난 예외(타임아웃 등)는 진짜 실패이므로 그대로 올린다 — 재시도하면 대기만 두 배다.

        상한을 거는 이유 — 워커는 **한 개**다. 한 건이 브라우저 안에서 멎으면 뒤의 모든
        캡처가 이 큐에서 영원히 기다린다. 서버 쪽 asyncio.wait_for 는 그 요청만 풀어 줄 뿐
        워커는 그대로 잡혀 있으므로, 여기서도 끊고 **그 브라우저를 버려야** 다음이 산다.
        """
        if not getattr(settings, "render_reuse_browser", True):
            job = lambda: _one_shot(fn)  # noqa: E731
        else:
            def job():
                browser = self._browser_on_worker()
                try:
                    return fn(browser)
                except Exception:  # noqa: BLE001
                    if _connected(browser):
                        raise
                    self._forget()
                    return fn(self._browser_on_worker())

        if self._worker.is_current():
            return job()

        limit = float(getattr(settings, "render_worker_timeout_s", 0) or 0)
        with self._gen_lock:
            gen, worker = self._gen, self._worker
        fut = worker.submit(job)
        if limit <= 0:
            return fut.result()
        try:
            return fut.result(timeout=limit)
        except FuturesTimeout as exc:
            # 아직 시작도 안 한 경우가 있다 — 그때는 취소가 실제로 먹는다.
            fut.cancel()
            self._retire(gen)
            raise RenderStalled(
                f"브라우저 렌더가 {limit:.0f}초 안에 끝나지 않아 중단했습니다"
                " — 브라우저를 버리고 다시 띄웁니다."
            ) from exc

    def shutdown(self, timeout: float = 2.0) -> None:
        """프로세스 종료 시 1회. 못 닫아도 붙잡지 않는다 — 드라이버가 브라우저를 정리한다."""
        if self._browser is None and self._pw is None:
            return

        def _close():
            self._forget()
            pw, self._pw = self._pw, None
            if pw is not None:
                try:
                    pw.stop()
                except Exception:  # noqa: BLE001
                    pass

        try:
            self._worker.submit(_close).result(timeout=timeout)
        except Exception:  # noqa: BLE001
            pass


_POOL = _BrowserPool()
atexit.register(_POOL.shutdown)


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
        import playwright  # type: ignore  # noqa: F401
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

    def work(browser):
        # 토큰 수집은 **캐시가 없는 편이** 낫다(캐시 히트면 요청이 안 나가 토큰을 못 줍는다)
        # → 캡처용 공용 컨텍스트를 쓰지 않고 매번 새로 만들고 닫는다. 컨텍스트 생성은 0.06초다.
        ctx = browser.new_context(viewport={"width": 660, "height": 420},
                                  ignore_https_errors=True)
        try:
            page = ctx.new_page()

            def _on_req(r):
                if "gpms-cs-s" in r.url and not found:
                    m = _GPMS_RE.search(r.url)
                    if m:
                        found.append(m.group(1))

            page.on("request", _on_req)
            page.goto(f"{server.url}/{tmp.name}", wait_until="load", timeout=25000)
            page.wait_for_function("window.__done===true", timeout=30000)
        finally:
            ctx.close()

    try:
        server.start()
        _POOL.run(settings, work)
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
        import playwright  # type: ignore  # noqa: F401
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

    def work(browser):
        # 셀 개수·크기에 따라 뷰포트가 달라지므로 컨텍스트는 이 호출 전용으로 만들고 닫는다.
        # 비싼 것은 브라우저 기동/종료이지 컨텍스트가 아니다(실측 0.06초).
        ctx = browser.new_context(
            viewport={"width": w + 40, "height": (h + 20) * len(levels) + 40},
            device_scale_factor=2, bypass_csp=True, ignore_https_errors=True,
        )
        try:
            page = ctx.new_page()
            page.goto(f"{server.url}/{tmp.name}", wait_until="load", timeout=25000)
            page.wait_for_function("window.__ready===true", timeout=30000)
            if page.evaluate("window.__err || ''"):
                return
            page.wait_for_timeout(700)      # 라벨/타일 정착
            for i in range(len(levels)):
                try:
                    out.append(page.locator(f"#m{i}").screenshot(type="jpeg", quality=82))
                except Exception:  # noqa: BLE001
                    out.append(b"")
        finally:
            ctx.close()

    try:
        server.start()
        _POOL.run(settings, work)
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


def _top_is_black(path: Path, settings: Settings, *, pane_h: int | None = None) -> float | None:
    """캡처 상단(로드뷰 영역)의 평균 밝기. Pillow 없으면 None(판정 생략)."""
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            g = im.convert("L")
            w, h = g.size
            ph = pane_h or settings.viewport_h
            cut = max(1, int(h * ph / max(1, ph + settings.map_h)))
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


def _build_html(pose: dict, settings: Settings, key: str, *, official_only: bool = False,
                pane_w: int | None = None, pane_h: int | None = None) -> str:
    # pano/key 는 JS 문자열 리터럴/URL 로 들어가므로 안전 문자만 남긴다(</script> 브레이크아웃 방지).
    # 유효한 구글 pano id 는 base64url 계열([A-Za-z0-9_-]) → 무해한 값이 아니면 좌표 경로로 폴백됨.
    pano = re.sub(r"[^A-Za-z0-9_-]", "", pose.get("pano") or "")
    safe_key = re.sub(r"[^A-Za-z0-9_.\-]", "", key or "")
    has_pano = bool(pano)
    has_loc = pose.get("lat") is not None and pose.get("lng") is not None
    lat = _f(pose.get("lat"), 0.0)
    lng = _f(pose.get("lng"), 0.0)
    repl = {
        "__W__": str(pane_w or settings.viewport_w),
        "__PH__": str(pane_h or settings.viewport_h),
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
        # 예전에는 여기가 2600 이었다 — 타일이 이미 다 와도 브라우저 안에서 2.6초를 버렸다.
        # 이제 JS 는 최소 페인트 여유만 두고, 정착 판정은 파이썬이 타일 응답으로 한다
        # (_wait_tiles_quiet). 느린 회선에서는 그쪽이 오히려 더 기다린다.
        "__SETTLE__": str(max(0, int(settings.render_settle_ms))),
        "__MAPWAIT__": str(max(0, int(getattr(settings, "render_map_wait_ms", 2500)))),
        "__OFFICIAL_ONLY__": "true" if official_only else "false",
    }
    html = _HTML
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


def _wait_tiles_quiet(page, tiles: dict, *, quiet_ms: int, max_ms: int) -> float:
    """타일 응답이 quiet_ms 동안 멎으면 '정착'으로 본다 — 고정 대기(예전 2.6초)의 대체.

    타일 카운터는 이미 page.on("response") 가 채우고 있다. 이벤트는 wait_for_timeout 으로
    양보하는 동안 배달되므로, 짧게 자면서 카운터가 멈추는 순간을 잡으면 된다.
    빠른 회선에서는 0.5초 안에 끝나고, 느린 회선에서는 max_ms 까지 더 기다린다.
    """
    t0 = time.monotonic()
    deadline = t0 + max(0.0, max_ms / 1000.0)
    last_n = -1
    last_change = t0
    while time.monotonic() < deadline:
        n = int(tiles.get("ok", 0)) + int(tiles.get("err", 0))
        now = time.monotonic()
        if n != last_n:
            last_n, last_change = n, now
        elif (now - last_change) * 1000.0 >= quiet_ms:
            break
        page.wait_for_timeout(40)
    return time.monotonic() - t0


def render_sync(pose: dict, settings: Settings, key: str, *, official_only: bool = False) -> dict:
    """동기 Playwright 렌더. 실제 브라우저 작업은 _POOL 의 워커 스레드에서 돈다.

    반환은 capture_static 과 동형의 dict.
    """
    try:
        import playwright  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        return {
            "status": "NO_DEP",
            "message": "playwright 미설치 — `uv sync --extra all` 후 `playwright install chromium` 하세요.",
        }

    # 로드뷰 영역은 화면 패널과 같은 종횡비로 잡는다 — 같은 SDK 를 같은 비율·같은 zoom
    # 으로 돌리면 화면 캔버스와 동일한 프레이밍이 나온다(보고서 이미지의 요구 조건).
    from . import streetview as _sv

    ph = settings.viewport_h
    w = max(320, min(1600, round(ph * _sv.view_aspect(pose, settings))))
    total_h = ph + settings.map_h
    jpeg = settings.capture_format == "jpeg"
    fname = f"capture_{uuid.uuid4().hex[:12]}.{'jpg' if jpeg else 'png'}"
    out = settings.captures_dir / fname

    # HTML 은 captures_dir 아래 임시파일로 두고 localhost 로 서빙(파일 URL 아님 → 리퍼러 유지).
    tmp = settings.captures_dir / f".rv_{uuid.uuid4().hex[:8]}.html"
    tmp.write_text(_build_html(pose, settings, key, official_only=official_only,
                               pane_w=w, pane_h=ph), encoding="utf-8")

    # server.start() 는 반드시 try 안에 있어야 한다. 예전에는 try 바깥이라, start() 가
    # 던지면 finally 의 tmp 정리가 실행되지 않아 API 키가 든 .rv_*.html 이 계속 쌓였고,
    # 예외가 FastAPI 까지 올라가 클라이언트는 JSON 이 아닌 text/plain 500 을 받았다.
    server = _LocalServer(settings.captures_dir, tmp.name)
    # 타일 응답 계측 — 제3자 파노는 lh3.googleusercontent.com(키 없는 CDN)에서 오고 IP 단위
    # 레이트리밋에 걸리면 HTTP 429 로 검은 화면이 된다. 공식 파노는
    # streetviewpixels-pa.googleapis.com(키 기반). 스크린샷 뒤의 판정에도 쓰므로 바깥에 둔다.
    tiles: dict = {"ok": 0, "err": 0, "codes": {}}
    # 제3자 파노의 gpms 토큰을 **요청 URL에서** 걷어 둔다. 응답이 429여도 요청은 나가므로
    # 토큰은 확보된다 — 그 토큰의 photo 형식(=w..-h..-k-no-pi..-ya..)은 제한을 받지 않는다.
    gpms: list[str] = []
    info: dict = {}
    settle_s = 0.0

    def work(browser):
        """브라우저 안에서 끝나는 일. 조기 종료할 결과가 있으면 그 dict 를 돌려준다."""
        nonlocal info, settle_s
        ctx_kw = dict(
            viewport={"width": w, "height": total_h},
            device_scale_factor=2,          # 표지판/문자 가독성 ↑ (분석 정확도)
            bypass_csp=True,
            ignore_https_errors=True,       # Avast TLS 가로채기 대비
        )
        # 컨텍스트를 재사용해야 HTTP 캐시가 살아 Maps JS 를 매번 다시 안 받는다(1.65s → 0.71s).
        # 뷰포트 폭만 포즈마다 다르므로 페이지 쪽에서 따로 맞춘다.
        reuse = getattr(settings, "render_reuse_browser", True)
        context = (_POOL.context(browser, ("render", 2, total_h), **ctx_kw)
                   if reuse else browser.new_context(**ctx_kw))
        page = context.new_page()
        try:
            page.set_viewport_size({"width": w, "height": total_h})
            logs: list[str] = []
            page.on("console", lambda m: logs.append(f"{m.type}:{m.text}"))
            page.on("pageerror", lambda e: logs.append(f"pageerror:{e}"))

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
            # 타일이 실제로 멎을 때까지만 기다린다(예전에는 브라우저 안에서 무조건 2.6초).
            settle_s = _wait_tiles_quiet(
                page, tiles,
                quiet_ms=int(settings.render_quiet_ms),
                max_ms=int(settings.render_settle_max_ms),
            )
            shot: dict = {
                "path": str(out),
                "clip": {"x": 0, "y": 0, "width": w, "height": total_h},
            }
            if jpeg:
                shot["type"] = "jpeg"
                shot["quality"] = settings.capture_quality
            page.screenshot(**shot)
            return None
        finally:
            page.close()
            if not reuse:
                context.close()

    try:
        server.start()
        early = _POOL.run(settings, work)
        if early is not None:
            return early
    except RenderStalled as exc:
        # 워커가 상한에 걸려 브라우저를 버린 경우 — 실패 사유를 뭉뚱그리지 않는다.
        return {"status": "RENDER_TIMEOUT", "message": str(exc), "stalled": True}
    except Exception as exc:  # noqa: BLE001
        return {"status": "RENDER_ERROR", "message": f"브라우저 렌더 실패: {exc}"}
    finally:
        server.stop()
        tmp.unlink(missing_ok=True)

    if not out.exists() or out.stat().st_size < 2000:
        return {"status": "RENDER_ERROR", "message": "스크린샷이 비었습니다(렌더 실패)."}

    # 검은 화면 판정은 **픽셀로** 한다. 제3자냐 아니냐로 미리 재단하지 않는다 —
    # 제3자 파노도 평소엔 정상 렌더되고, 검게 나오는 원인은 lh3 CDN 의 429 다.
    dark = _top_is_black(out, settings, pane_h=ph)
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
        # 성능 관찰용 — 정착에 실제로 쓴 시간과, 프로세스가 지금까지 브라우저를 몇 번 띄웠는지.
        # 재사용이 동작 중이면 launches 는 캡처 수와 무관하게 1 근처에 머문다.
        "settle_s": round(settle_s, 2),
        "browser_launches": _POOL.launches,
        "render_stalls": _POOL.stalls,
    }
