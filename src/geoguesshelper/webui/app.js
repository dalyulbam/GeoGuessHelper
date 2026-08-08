"use strict";
/* GeoGuessHelper — 2분할 뷰어 프론트엔드
   흐름: /api/config → (JS키 있으면 Google Maps 로드) → 추출 → 2분할 → 캡처 → 분석 */

const $ = (sel, root = document) => root.querySelector(sel);

let CONFIG = null;
let mapsReady = false;
let map = null, pano = null, marker = null, svc = null;
let loadToken = 0;              // initSplit 로드 식별 — 늦게 도착한 stale 콜백 무시(탭 전환 경합 방지)
let syncArmed = true;           // 프로그램적 파노 로드 중(setPov/setPano)엔 false — stale 좌표가 엉뚱한 탭에 기록되는 것 차단
let currentPose = null;         // 활성 탭의 현재 캡처 대상 포즈
let statusTimer = null;
let album = [];                 // 활성 탭의 캡처 {file, url, sel} — 탭 전환 시 참조 교체
let lastAnalysis = null;        // 활성 탭의 마지막 분석 결과(리포트 생성용)
let lastAnalysisFiles = [];     // 분석에 쓰인 캡처 파일들
let LANGS = [];                 // [{code,name}] 지원 언어
let selectedLangs = ["ko"];     // 선택 언어(첫 번째 = 화면 표시 언어)
let reportsMade = [];           // 활성 탭의 생성 보고서 {lang,langName,file,url}

// ── 링크 탭 (여러 링크를 폴더 탭처럼 관리) ─────────────────
// 각 탭 = {id, extracted(추출 응답), current(라이브 포즈), album, reports, analysis, analysisFiles}
let tabs = [];
let activeId = null;
let tabSeq = 0;
const activeTab = () => tabs.find((t) => t.id === activeId) || null;
const tabById = (id) => tabs.find((t) => t.id === id) || null;
// 탭별 보고서 생성 진행 플래그 — 같은 탭 중복 시작만 막고, 다른 탭은 동시 생성 허용.
function reportBusy(id) { const t = tabById(id); return !!(t && t.busy); }
function setBusy(id, on) { const t = tabById(id); if (t) t.busy = on; renderTabbar(); }

// ── 부트 ──────────────────────────────────────────────
async function boot() {
  try {
    CONFIG = await (await fetch("/api/config")).json();
  } catch (e) {
    CONFIG = { hasJsKey: false, hasStaticKey: false, hasAnthropic: false,
      languages: [{ code: "ko", name: "한국어" }], defaultLang: "ko" };
  }
  initLangs();
  renderKeyChips();
  renderIntroHint();
  wireUI();
  renderLangMenu();
  if (CONFIG.hasJsKey) loadGoogleMaps(CONFIG.jsApiKey);
}

// ── 언어 선택 (헤더) ───────────────────────────────────
function initLangs() {
  LANGS = (CONFIG.languages && CONFIG.languages.length)
    ? CONFIG.languages : [{ code: "ko", name: "한국어" }];
  const valid = new Set(LANGS.map((l) => l.code));
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem("gg_langs") || "[]"); } catch (e) {}
  saved = (Array.isArray(saved) ? saved : []).filter((c) => valid.has(c));
  selectedLangs = saved.length ? saved : [valid.has(CONFIG.defaultLang) ? CONFIG.defaultLang : LANGS[0].code];
}

function renderLangMenu() {
  const wrap = $("#lang-options");
  if (!wrap) return;
  const sel = new Set(selectedLangs);
  wrap.innerHTML = LANGS.map((l) =>
    `<label class="lang-opt"><input type="checkbox" value="${l.code}" ${sel.has(l.code) ? "checked" : ""}><span>${esc(l.name)}</span></label>`
  ).join("");
  wrap.querySelectorAll("input[type=checkbox]").forEach((cb) => cb.addEventListener("change", onLangChange));
  updateLangSummary();
}

function onLangChange() {
  const checked = Array.from($("#lang-options").querySelectorAll("input:checked")).map((c) => c.value);
  const next = selectedLangs.filter((c) => checked.includes(c)); // 기존 순서 유지
  checked.forEach((c) => { if (!next.includes(c)) next.push(c); }); // 새로 체크된 것 뒤에
  if (!next.length) next.push(valid0());                           // 최소 1개 강제
  selectedLangs = next;
  try { localStorage.setItem("gg_langs", JSON.stringify(selectedLangs)); } catch (e) {}
  const set = new Set(selectedLangs);
  $("#lang-options").querySelectorAll("input[type=checkbox]").forEach((cb) => (cb.checked = set.has(cb.value)));
  updateLangSummary();
}

function valid0() { return (CONFIG.defaultLang) || (LANGS[0] && LANGS[0].code) || "ko"; }
function primaryLang() { return selectedLangs[0] || valid0(); }
// 링크 '시작 좌표'(추출 원점) — 보고서 파일명에 기록. 이동해도 원점 유지.
function startCoord() {
  const t = activeTab();
  const p = (t && t.extracted) || currentPose || {};
  return { lat: p.lat, lng: p.lng };
}
function researchOn() { const el = $("#research-on"); return el ? el.checked : true; }
function langName(code) { const l = LANGS.find((x) => x.code === code); return l ? l.name : code; }
function updateLangSummary() {
  const names = selectedLangs.map(langName);
  $("#lang-summary").textContent = names[0] + (names.length > 1 ? ` +${names.length - 1}` : "");
}

function renderKeyChips() {
  const chips = [
    ["지도/로드뷰", CONFIG.hasJsKey],
    ["캡처", CONFIG.hasStaticKey || CONFIG.hasJsKey],
    ["분석", CONFIG.hasAnthropic],
  ];
  $("#keychips").innerHTML = chips
    .map(([n, on]) => `<span class="chip ${on ? "on" : "off"}">${n} ${on ? "✓" : "—"}</span>`)
    .join("");
}

function renderIntroHint() {
  const h = $("#intro-hint");
  if (CONFIG.hasJsKey) {
    h.innerHTML = "";
    return;
  }
  h.innerHTML = `<div class="warn">
    ⚠️ <b>GOOGLE_MAPS_JS_API_KEY 미설정</b> — 링크 <b>추출은 지금도 동작</b>하지만, 인터랙티브 2분할 로드뷰/지도는
    키가 있어야 뜹니다.<br>루트에 <code>.env</code> 를 만들고 <code>GOOGLE_MAPS_JS_API_KEY</code> 를 넣은 뒤 서버를 재시작하세요.
    (<code>.env.example</code> 참고)</div>`;
}

// ── 링크 탭 관리 ───────────────────────────────────────
function snapshotActive() {
  // 현재 전역 상태를 활성 탭에 반영(전환 전에 호출).
  const t = activeTab();
  if (!t) return;
  t.current = currentPose;
  t.album = album;
  t.reports = reportsMade;
  t.analysis = lastAnalysis;
  t.analysisFiles = lastAnalysisFiles;
}

function loadActive() {
  // 활성 탭 상태를 전역으로 복원하고 화면 전체를 다시 그린다.
  renderTabbar();
  const t = activeTab();
  if (!t) {  // 탭이 없으면 인트로
    currentPose = null; album = []; reportsMade = [];
    lastAnalysis = null; lastAnalysisFiles = [];
    showIntro();
    return;
  }
  currentPose = t.current;
  album = t.album;
  reportsMade = t.reports;
  lastAnalysis = t.analysis;
  lastAnalysisFiles = t.analysisFiles;

  renderPose(t.extracted);
  renderAlbum();
  renderReports();
  if (lastAnalysis && lastAnalysis.analysis) renderAnalysis(lastAnalysis);
  else { $("#analysis-card").hidden = true; }

  if (CONFIG.hasJsKey) {
    $("#intro").hidden = true; $("#fallback").hidden = true;
    if (mapsReady) initSplit(t.current || t.extracted);
  } else {
    showFallback(t.extracted);
  }
}

function showIntro() {
  $("#intro").hidden = false;
  $("#fallback").hidden = true;
  ["#pose-card", "#album-card", "#analysis-card", "#reports-card"].forEach((s) => ($(s).hidden = true));
}

function newTab(pose) {
  snapshotActive();
  // 같은 링크가 이미 열려 있으면 그 탭으로 이동(중복 방지).
  const key = pose.resolved_url || pose.input_url || (pose.pano || `${pose.lat},${pose.lng}`);
  const dup = tabs.find((t) => (t.extracted.resolved_url || t.extracted.input_url ||
    (t.extracted.pano || `${t.extracted.lat},${t.extracted.lng}`)) === key);
  if (dup) { activeId = dup.id; return dup; }
  const id = ++tabSeq;
  const t = {
    id, extracted: pose,
    current: { lat: pose.lat, lng: pose.lng, pano: pose.pano,
      heading: pose.heading, pitch: pose.pitch, fov: pose.fov },
    album: [], reports: [], analysis: null, analysisFiles: [], busy: false,
  };
  tabs.push(t);
  activeId = id;
  return t;
}

function activateTab(id) {
  if (id === activeId) return;
  snapshotActive();
  activeId = id;
  loadActive();
}

function closeTab(id) {
  const idx = tabs.findIndex((t) => t.id === id);
  if (idx < 0) return;
  const wasActive = id === activeId;
  tabs.splice(idx, 1);
  if (wasActive) {
    activeId = tabs.length ? tabs[Math.max(0, idx - 1)].id : null;
    loadActive();  // 이웃 탭 또는 인트로
  } else {
    renderTabbar();
  }
}

function tabTitle(t) {
  const bg = t.analysis && t.analysis.analysis && t.analysis.analysis.best_guess;
  if (bg && (bg.city || bg.country)) return [bg.city, bg.country].filter(Boolean).join(", ");
  const p = t.extracted || {};
  if (p.pano) return short(p.pano);
  if (p.lat != null) return `${fmt(p.lat)}, ${fmt(p.lng)}`;
  return "링크";
}

function renderTabbar() {
  const bar = $("#tabbar");
  if (!bar) return;
  if (!tabs.length) { bar.hidden = true; bar.innerHTML = ""; return; }
  bar.hidden = false;
  bar.innerHTML = tabs.map((t) => {
    const n = (t.album && t.album.length) ? `<span class="tab-n">${t.album.length}</span>` : "";
    const busy = t.busy ? `<span class="tab-busy" title="보고서 생성 중">⏳</span>` : "";
    return `<div class="tab${t.id === activeId ? " active" : ""}${t.busy ? " busy" : ""}" data-id="${t.id}">` +
      `<span class="tab-title" title="${esc(tabTitle(t))}">${esc(tabTitle(t))}</span>${busy}${n}` +
      `<button class="tab-x" data-close="${t.id}" title="탭 닫기">×</button></div>`;
  }).join("");
  bar.querySelectorAll(".tab").forEach((el) =>
    el.addEventListener("click", (e) => {
      if (e.target.closest(".tab-x")) return;
      activateTab(Number(el.dataset.id));
    }));
  bar.querySelectorAll(".tab-x").forEach((el) =>
    el.addEventListener("click", (e) => { e.stopPropagation(); closeTab(Number(el.dataset.close)); }));
}

// ── Google Maps 로더 (인라인 콜백) ─────────────────────
function loadGoogleMaps(key) {
  window.__gmapsReady = () => {
    mapsReady = true;
    // pano-only(좌표 null) 링크도 렌더되도록 loadActive 와 동일 조건.
    if (currentPose && (currentPose.lat != null || currentPose.pano)) initSplit(currentPose);
  };
  const s = document.createElement("script");
  s.async = true;
  s.src =
    "https://maps.googleapis.com/maps/api/js?key=" +
    encodeURIComponent(key) +
    "&v=weekly&loading=async&callback=__gmapsReady";
  s.onerror = () =>
    setStatus("Google Maps SDK 로드 실패 — API 키/리퍼러(http://localhost:*) 설정 확인", "err", 6000);
  document.head.appendChild(s);
}

// ── UI 배선 ────────────────────────────────────────────
function wireUI() {
  $("#btn-extract").addEventListener("click", doExtract);
  $("#url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doExtract();
  });
  $("#btn-paste").addEventListener("click", async () => {
    try {
      const t = await navigator.clipboard.readText();
      if (t) {
        $("#url").value = t.trim();
        $("#url").focus();
      }
    } catch (e) {
      setStatus("클립보드 접근 실패 — 직접 붙여넣으세요", "err");
    }
  });
  $("#btn-capture").addEventListener("click", doCapture);
  $("#btn-quick-report").addEventListener("click", doQuickReport);
  $("#btn-analyze").addEventListener("click", doAnalyze);
  $("#btn-album-report").addEventListener("click", doAlbumReport);
  $("#btn-report").addEventListener("click", doReport);
  // 웹 리서치 토글 상태 복원 + 저장
  const rt = $("#research-on");
  if (rt) {
    try { const s = localStorage.getItem("gg_research"); if (s !== null) rt.checked = s === "1"; } catch (e) {}
    rt.addEventListener("change", () => {
      try { localStorage.setItem("gg_research", rt.checked ? "1" : "0"); } catch (e) {}
    });
  }
  $("#url").focus();
}

// ── 추출 ───────────────────────────────────────────────
async function doExtract() {
  const url = $("#url").value.trim();
  if (!url) return;
  setStatus("추출 중…");
  let pose;
  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      setStatus("추출 오류: " + (err.detail || res.status), "err", 6000);
      return;
    }
    pose = await res.json();
  } catch (e) {
    setStatus("네트워크 오류", "err");
    return;
  }
  const nBefore = tabs.length;
  newTab(pose);            // 새 링크 = 새 탭(같은 링크면 그 탭으로 이동)
  loadActive();            // 탭 상태를 화면에 복원(새 탭이면 빈 상태 + 뷰어 초기화)
  setStatus(tabs.length > nBefore ? "추출 완료" : "이미 열린 탭으로 이동", "ok");
  if (CONFIG.hasJsKey && !mapsReady) setStatus("지도 SDK 로딩 중…");
}

function renderPose(pose) {
  const rows = [
    ["위도 lat", fmt(pose.lat)],
    ["경도 lng", fmt(pose.lng)],
    ["heading", pose.heading != null ? fmt(pose.heading) + "°" : "—"],
    ["pitch", pose.pitch != null ? fmt(pose.pitch) + "°" : "—"],
    ["fov", pose.fov != null ? fmt(pose.fov) : "—"],
    ["pano", pose.pano ? `<span title="${pose.pano}">${short(pose.pano)}</span> <span class="badge">${pose.pano_kind || "?"}</span>` : "— (좌표로 최근접 탐색)"],
  ];
  $("#pose-body").innerHTML = rows
    .map(([k, v]) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`)
    .join("");
  const gmaps = pose.resolved_url || pose.input_url ||
    `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${pose.lat},${pose.lng}`;
  $("#open-gmaps").href = gmaps;
  $("#pose-card").hidden = false;
  // 캡처는 Static 키 없어도 브라우저 렌더(JS 키)로 가능 → JS 키만 있어도 활성화.
  const canCapture = CONFIG.hasStaticKey || CONFIG.hasJsKey;
  $("#btn-capture").disabled = !canCapture;
  $("#btn-capture").title = canCapture
    ? "자동: 정적 실패 시 브라우저 렌더로 폴백"
    : "캡처는 GOOGLE_MAPS_JS_API_KEY 또는 GOOGLE_MAPS_STATIC_KEY 필요";
  // 바로 보고서 = 캡처 + 분석 → 캡처 키와 ANTHROPIC 둘 다 필요
  $("#btn-quick-report").disabled = !canCapture || !CONFIG.hasAnthropic;
  $("#btn-quick-report").title = !canCapture
    ? "캡처 키 필요"
    : (!CONFIG.hasAnthropic ? "분석에 ANTHROPIC_API_KEY 필요" : "지금 화면을 캡처 → 분석 → 보고서");
}

// ── 2분할 뷰어 ─────────────────────────────────────────
function initSplit(pose) {
  if (!mapsReady) return;
  $("#intro").hidden = true;
  $("#fallback").hidden = true;

  const myToken = ++loadToken;   // 이 로드의 식별자(전환 시 이후 로드가 우선)
  syncArmed = false;             // 로드 중 setPov/비동기 setPano 가 만드는 stale 이벤트 무시
  const p = { lat: pose.lat, lng: pose.lng };
  if (!map) {
    map = new google.maps.Map($("#map"), {
      center: p, zoom: 14, mapTypeId: "hybrid", streetViewControl: true,
      fullscreenControl: false,
    });
    pano = new google.maps.StreetViewPanorama($("#pano"), {
      position: p, pov: { heading: pose.heading || 0, pitch: pose.pitch || 0 },
      zoom: 1, motionTracking: false, linksControl: true, addressControl: true,
      fullscreenControl: false,
    });
    map.setStreetView(pano);                       // ← 두 패널 연결
    marker = new google.maps.Marker({ position: p, map });
    pano.addListener("position_changed", syncFromPano);
    pano.addListener("pov_changed", syncFromPano);
    svc = new google.maps.StreetViewService();
  } else {
    map.setCenter(p);
    marker.setPosition(p);
    pano.setPov({ heading: pose.heading || 0, pitch: pose.pitch || 0 });
  }

  // setPano 는 비동기 로드 → 동기 syncFromPano 는 이전 위치를 읽으므로 호출하지 않는다.
  // setPov(위 else 분기)도 pov_changed 를 즉시 발생시키므로, 로드가 정착할 때까지 sync 를 무장 해제한다.
  if (pose.pano) {
    pano.setPano(pose.pano);
  } else {
    const req = {
      location: p, radius: (CONFIG.defaults && CONFIG.defaults.radius) || 50,
      preference: google.maps.StreetViewPreference.NEAREST,
      sources: [google.maps.StreetViewSource.OUTDOOR],
    };
    svc.getPanorama(req)
      .then(({ data }) => {
        if (myToken !== loadToken) return;   // 다른 탭으로 전환됨 → stale 무시
        pano.setPano(data.location.pano);
      })
      .catch(() => {
        if (myToken === loadToken)
          setStatus("이 위치엔 스트리트뷰 커버리지가 없습니다 (ZERO_RESULTS)", "err", 5000);
      });
  }
  // 파노가 정착할 시간을 준 뒤 재무장 — 최신 로드(토큰 일치)에서만. 이후 사용자 이동은 정상 동기화.
  setTimeout(() => { if (myToken === loadToken) syncArmed = true; }, 1000);
}

function syncFromPano() {
  if (!pano || !syncArmed) return;   // 로드 중(stale 이벤트)이면 활성 탭 좌표를 건드리지 않는다
  const pos = pano.getPosition();
  if (!pos) return;
  const pov = pano.getPov();
  map.setCenter(pos);
  marker.setPosition(pos);
  currentPose = {
    lat: pos.lat(), lng: pos.lng(), pano: pano.getPano(),
    heading: pov.heading, pitch: pov.pitch,
    fov: currentPose ? currentPose.fov : 90, zoom: pano.getZoom(),
  };
  const t = activeTab();          // 이동한 위치를 활성 탭에 유지(전환 후 복원)
  if (t) t.current = currentPose;
}

// 키 없을 때 대체 화면
function showFallback(pose) {
  $("#intro").hidden = true;
  const fb = $("#fallback");
  const gmaps = pose.resolved_url || pose.input_url;
  fb.innerHTML = `<div class="fb">
    <h2>📍 추출됨 — 인터랙티브 지도는 API 키가 필요합니다</h2>
    <p>좌표 <b>${fmt(pose.lat)}, ${fmt(pose.lng)}</b> · heading ${fmt(pose.heading)}° · pitch ${fmt(pose.pitch)}°</p>
    <p><code>GOOGLE_MAPS_JS_API_KEY</code> 를 <code>.env</code> 에 넣으면 이 자리에 로드뷰▲/지도▼ 2분할이 뜹니다.</p>
    ${gmaps ? `<p><a href="${gmaps}" target="_blank" rel="noopener">→ Google 지도에서 바로 보기 ↗</a></p>` : ""}
    ${CONFIG.hasStaticKey ? `<p class="muted">우측 <b>“현재 장면 캡처”</b>는 Static API 로 지금도 동작합니다.</p>` : ""}
    <div id="fb-preview"></div>
  </div>`;
  fb.hidden = false;
}

// ── 캡처 ───────────────────────────────────────────────
async function doCapture() {
  if (!currentPose || (currentPose.lat == null && !currentPose.pano)) {
    setStatus("먼저 링크를 추출하세요", "err");
    return;
  }
  const originId = activeId;           // 캡처 시작 탭 — 완료 시 이 탭 앨범에 저장
  const pose = currentPose;            // 클릭 순간 포즈 고정
  const mode = ($("#capture-mode") && $("#capture-mode").value) || "auto";
  const modeLabel = mode === "playwright" ? "브라우저 렌더"
    : mode === "static" ? "Static API 합성" : "자동";
  setStatus(`캡처 중… (${modeLabel})`);
  let r;
  try {
    r = await (await fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pose, mode }),
    })).json();
  } catch (e) {
    setStatus("캡처 네트워크 오류", "err");
    return;
  }
  if (r.status === "OK") {
    addToAlbum(r, originId);
    const via = r.mode === "playwright"
      ? (r.fallback_from ? "브라우저 렌더 — Static 미가용 자동 폴백" : "브라우저 렌더")
      : (r.composited ? "로드뷰+지도 합성" : "로드뷰만 — Pillow 미설치");
    setStatus("캡처 완료 (" + via + ")", "ok");
    // fallback 화면이면 큰 미리보기도(시작 탭이 아직 활성일 때만)
    const fp = $("#fb-preview");
    if (fp && originId === activeId) fp.innerHTML = `<img class="big-img" src="${r.url}" alt="capture">`;
  } else {
    setStatus("캡처 실패: " + (r.message || r.status), "err", 6000);
  }
}

function addToAlbum(r, tabId) {
  const id = tabId != null ? tabId : activeId;
  const t = tabById(id);
  if (t) t.album.push({ file: r.file, url: r.url, sel: true });
  if (id === activeId) {              // 시작 탭이 여전히 활성 → 화면 앨범 갱신
    if (t) album = t.album;
    renderAlbum();
  }
  renderTabbar();                     // 캡처 수 배지(모든 탭) 갱신
}

function renderAlbum() {
  $("#album-card").hidden = album.length === 0;
  $("#album-count").textContent = album.length ? `(${album.length})` : "";
  const wrap = $("#album");
  wrap.innerHTML = "";
  album.forEach((a, i) => {
    const d = document.createElement("div");
    d.className = "thumb" + (a.sel ? " sel" : "");
    d.innerHTML = `<img src="${a.url}" alt="capture ${i + 1}"><span class="tick">✓</span>`;
    d.addEventListener("click", () => {
      a.sel = !a.sel;
      renderAlbum();
    });
    wrap.appendChild(d);
  });
  const anySel = album.some((a) => a.sel);
  $("#btn-analyze").disabled = !anySel || !CONFIG.hasAnthropic;
  $("#btn-analyze").title = CONFIG.hasAnthropic ? "" : "분석은 ANTHROPIC_API_KEY 필요";
  $("#btn-album-report").disabled = !anySel || !CONFIG.hasAnthropic;
  $("#btn-album-report").title = CONFIG.hasAnthropic ? "선택 장면 분석 + 보고서" : "보고서는 ANTHROPIC_API_KEY 필요";
}

// ── 분석 ───────────────────────────────────────────────
async function doAnalyze() {
  const files = album.filter((a) => a.sel).map((a) => a.file);
  if (!files.length) return;
  const originId = activeId;           // 분석 시작 탭 — 결과는 이 탭에 저장
  if (reportBusy(originId)) { setStatus("이 탭은 이미 분석/보고서 진행 중입니다", "err"); return; }
  setBusy(originId, true);             // 같은 탭 분석↔보고서 동시 실행 방지(다른 탭은 동시 허용)
  const lang = primaryLang();
  setStatus(`분석 중… (${files.length}장 · ${langName(lang)})`);
  $("#btn-analyze").disabled = true;
  let r;
  try {
    r = await (await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files, lang }),
    })).json();
  } catch (e) {
    setBusy(originId, false);
    setStatus("분석 네트워크 오류", "err");
    renderAlbum();
    return;
  }
  setBusy(originId, false);
  if (r.status === "OK" && r.analysis) {
    const t = tabById(originId);
    if (t) { t.analysis = r; t.analysisFiles = files; }
    if (originId === activeId) {
      lastAnalysis = r;
      lastAnalysisFiles = files;
      renderAnalysis(r);
      setStatus("분석 완료", "ok");
    } else {                           // 다른 탭으로 이동함 → 시작 탭에만 저장
      renderTabbar();                  // 제목 도시명 갱신
      setStatus(`분석 완료 (${t ? tabTitle(t) : "닫힌"} 탭)`, "ok");
    }
  } else {
    setStatus("분석 실패: " + (r.message || r.status), "err", 7000);
  }
  renderAlbum();
}

// ── 보고서 생성 (docs/report/*.html, 선택 언어들) ──────────
// 분석 결과 카드의 "보고서 생성" — 이미 분석된 것을 선택 언어들로 보고서화
async function doReport() {
  if (!lastAnalysis) { setStatus("먼저 분석하세요", "err"); return; }
  const originId = activeId;
  if (reportBusy(originId)) { setStatus("이 탭은 이미 분석/보고서 진행 중입니다", "err"); return; }
  setBusy(originId, true);
  await generateReports(
    { files: lastAnalysisFiles, langs: selectedLangs, analysis: lastAnalysis,
      research: researchOn(), start: startCoord() },
    originId
  );
}

// 앨범 카드의 "선택 장면 보고서" — 선택 장면을 분석+보고서 한 번에
async function doAlbumReport() {
  const files = album.filter((a) => a.sel).map((a) => a.file);
  if (!files.length) { setStatus("먼저 장면을 선택하세요", "err"); return; }
  const originId = activeId;
  if (reportBusy(originId)) { setStatus("이 탭은 이미 분석/보고서 진행 중입니다", "err"); return; }
  setBusy(originId, true);
  await generateReports(
    { files, langs: selectedLangs, research: researchOn(), start: startCoord() },
    originId
  );
}

// 포즈 카드의 "바로 보고서 추출" — 현재 화면 캡처 → 분석 → 보고서
async function doQuickReport() {
  if (!currentPose || (currentPose.lat == null && !currentPose.pano)) {
    setStatus("먼저 링크를 추출하세요", "err");
    return;
  }
  if (!CONFIG.hasAnthropic) { setStatus("분석에 ANTHROPIC_API_KEY 필요", "err", 5000); return; }
  const originId = activeId;
  if (reportBusy(originId)) { setStatus("이 탭은 이미 분석/보고서 진행 중입니다", "err"); return; }
  setBusy(originId, true);
  const pose = currentPose;            // 클릭 순간 포즈 고정
  const mode = ($("#capture-mode") && $("#capture-mode").value) || "auto";
  const rsN = researchOn() ? " · 웹 리서치(최대 1~2분)" : "";
  setStatus(`바로 보고서… 캡처+분석+생성 (${selectedLangs.length}개 언어${rsN})`);
  let r;
  try {
    r = await (await fetch("/api/scene-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pose, mode, langs: selectedLangs,
        research: researchOn(), start: startCoord() }),
    })).json();
  } catch (e) {
    setStatus("바로 보고서 네트워크 오류", "err");
    setBusy(originId, false);
    return;
  }
  setBusy(originId, false);
  if (r.status === "CAPTURE_FAIL") {
    setStatus("캡처 실패: " + (r.message || (r.capture && r.capture.status) || ""), "err", 7000);
    return;
  }
  if (r.capture && r.capture.status === "OK") addToAlbum(r.capture, originId);
  handleReportResult(r, originId);
}

// 공통: /api/report 호출 + 결과 처리 (originId = 시작 탭)
async function generateReports(payload, originId) {
  const rsN = payload.research ? " · 웹 리서치(최대 1~2분)" : "";
  setStatus(`보고서 생성 중… (${payload.langs.length}개 언어${rsN})`);
  let r;
  try {
    r = await (await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })).json();
  } catch (e) {
    setStatus("보고서 네트워크 오류", "err");
    setBusy(originId, false);
    return;
  }
  setBusy(originId, false);
  handleReportResult(r, originId);
}

// 결과를 '시작 탭'(originId)에 저장. 그 탭이 여전히 활성일 때만 화면을 갱신한다.
function handleReportResult(r, originId) {
  const t = tabById(originId != null ? originId : activeId);
  const isActive = !!t && t.id === activeId;

  // 서버가 분석을 새로 했으면(빠른/앨범 보고서) 시작 탭에 반영
  if (r.primaryAnalysis && r.primaryAnalysis.analysis) {
    if (t) { t.analysis = r.primaryAnalysis; t.analysisFiles = r.primaryAnalysis.images || t.analysisFiles; }
    if (isActive) {
      lastAnalysis = t.analysis;
      lastAnalysisFiles = t.analysisFiles;
      renderAnalysis(t.analysis);
    }
  }

  const reps = (r && r.reports) || [];
  if (reps.length) {
    if (t) { t.reports = t.reports || []; t.reports.unshift(...reps); }   // 시작 탭에 저장(최신 위)
    if (isActive) { reportsMade = t.reports; renderReports(); }
    renderTabbar();
    if (reps[0] && reps[0].url) window.open(reps[0].url, "_blank");
    const failed = (r.errors && r.errors.length) ? ` · 실패 ${r.errors.length}` : "";
    const where = isActive ? "" : ` (${t ? tabTitle(t) : "닫힌"} 탭)`;
    setStatus(`보고서 ${reps.length}개 생성 완료${failed}${where}`, "ok");
  } else {
    const msg = (r && (r.message || r.detail)) ||
      (r && r.errors && r.errors[0] && r.errors[0].message) || (r && r.status) || "실패";
    setStatus("보고서 실패: " + msg, "err", 7000);
  }
}

function renderReports() {
  $("#reports-card").hidden = reportsMade.length === 0;
  $("#reports-count").textContent = reportsMade.length ? `(${reportsMade.length})` : "";
  $("#reports-list").innerHTML = reportsMade.map((rp) => {
    const badge = rp.combined ? `<span class="rl-multi" title="한 파일에 여러 언어 + 상단 스위처">통합</span>` : "";
    return `<div class="report-row"><span class="rl-lang">${esc(rp.langName || rp.lang || "")}</span>${badge}` +
      `<a href="${esc(rp.url)}" target="_blank" rel="noopener" title="docs/report/${esc(rp.file)}">${esc(rp.file)} ↗</a></div>`;
  }).join("");
}

function renderAnalysis(r) {
  const a = r.analysis;
  const g = a.best_guess || {};
  const conf = Math.round((g.confidence || 0) * 100);
  const cf = a.coarse_filters || {};
  const cues = a.cues || [];
  const langs = a.languages_detected || [];
  const alts = (a.alternatives || [])
    .map((x) => `${x.country} ${Math.round((x.confidence || 0) * 100)}%`)
    .join(" · ");

  let html = `<div class="guess">
    <div class="country">${esc(g.country || "?")} ${g.country_iso ? `(${esc(g.country_iso)})` : ""}</div>
    <div class="region">${esc([g.region_or_state, g.city].filter(Boolean).join(" · "))}</div>
    <div class="confbar"><i style="width:${conf}%"></i></div>
    <div class="conf-label">best-guess 신뢰도 ${conf}%</div>
  </div>`;

  const chips = [
    cf.driving_side && `주행 ${cf.driving_side}`,
    cf.hemisphere && `${cf.hemisphere}반구`,
    cf.approx_climate_biome,
  ].filter(Boolean);
  if (chips.length) html += `<div class="chips">${chips.map((c) => `<span class="c">${esc(c)}</span>`).join("")}</div>`;

  if (cues.length) {
    html += `<div class="h4">식별 단서</div><table class="atbl">
      <tr><th>범주</th><th>관찰</th><th>가중</th></tr>` +
      cues.map((c) => `<tr>
        <td>${esc(c.category || "")}</td>
        <td>${esc(c.observation || "")}${c.supports && c.supports.length ? ` <span class="muted">[${c.supports.join(",")}]</span>` : ""}</td>
        <td class="w-${(c.weight || "").toLowerCase()}">${esc(c.weight || "")}</td>
      </tr>`).join("") + `</table>`;
  }

  if (langs.length) {
    html += `<div class="h4">언어/문자</div><table class="atbl">` +
      langs.map((l) => `<tr>
        <td>${esc(l.language || "")}${l.script ? ` <span class="muted">(${esc(l.script)})</span>` : ""}</td>
        <td>${esc(l.evidence || "")}</td>
      </tr>`).join("") + `</table>`;
  }

  if (a.cultural_economic_read)
    html += `<div class="h4">문화·경제</div><div class="read">${esc(a.cultural_economic_read)}</div>`;

  if (alts) html += `<div class="alt">대안: ${esc(alts)}</div>`;
  html += `<div class="cost">${r.images.length}장 · ${r.model} · ~$${r.cost_usd}</div>`;

  $("#analysis").innerHTML = html;
  $("#analysis-lang").textContent = langName(r.lang || primaryLang());
  $("#analysis-card").hidden = false;
  $("#btn-report").hidden = false;
  renderTabbar();   // 분석되면 탭 제목이 도시명으로 갱신
}

// ── 유틸 ───────────────────────────────────────────────
function fmt(x) {
  if (x == null) return "—";
  return typeof x === "number" ? Math.round(x * 1e5) / 1e5 : x;
}
function short(s) {
  return s && s.length > 16 ? s.slice(0, 8) + "…" + s.slice(-4) : s;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function setStatus(msg, kind = "", ms = 3000) {
  const el = $("#status");
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
  el.hidden = false;
  clearTimeout(statusTimer);
  if (ms) statusTimer = setTimeout(() => (el.hidden = true), ms);
}

boot();
