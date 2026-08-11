"use strict";
/* GeoGuessHelper — 2분할 뷰어 프론트엔드
   흐름: /api/config → (JS키 있으면 Google Maps 로드) → 추출 → 2분할 → 캡처 → 큐에 보고서 작업 등록

   동시성의 진실은 서버에 있다. 보고서 버튼은 작업을 **큐에 등록**할 뿐이고, 서버가 한 번에
   하나씩 처리한다. 새로고침하거나 브라우저 창을 하나 더 열어도 우회되지 않는다. */

const $ = (sel, root = document) => root.querySelector(sel);

let CONFIG = null;
let mapsReady = false;
let map = null, pano = null, marker = null, svc = null;

// ── 파노라마 동기화 상태 ────────────────────────────────────────
// 링크 탭이 몇 개든 StreetViewPanorama 는 **하나**다. 그래서 "지금 이 파노라마가 보여주는
// 것이 어느 탭의 것인가"를 정확히 알아야 한다. 예전에는 고정 1000ms 타이머로 재무장했는데,
// 좌표 링크는 svc.getPanorama 왕복이 붙어 보통 1.2~2초가 걸린다 → 타이머가 먼저 풀리는
// 구간에 사용자가 화면을 만지면 **이전 탭의 좌표가 새 탭에 기록**됐다.
// 이제는 시간이 아니라 "요청한 파노가 실제로 올라왔다"는 사실로만 무장한다.
let loadToken = 0;              // initSplit 로드 식별자
let syncArmed = false;          // 파노라마 → 탭 기록을 허용할지
let syncTabId = null;           // 무장 시점에 고정된 대상 탭 (activeTab() 을 그때그때 읽지 않는다)
let syncWantPano = null;        // 이 로드가 요청한 pano id — 실제로 이게 올라와야만 무장

let currentPose = null;         // 활성 탭의 현재 캡처 대상 포즈
let statusTimer = null;
let album = [];                 // 활성 탭의 캡처 {file, url, sel}
let lastAnalysis = null;
let lastAnalysisFiles = [];
let LANGS = [];
let selectedLangs = ["ko"];
let reportsMade = [];
let extractToken = 0;           // 추출 응답 역전 방지
let extracting = false;

// ── 링크 탭 ────────────────────────────────────────────────────
let tabs = [];
let activeId = null;
let tabSeq = 0;
const activeTab = () => tabs.find((t) => t.id === activeId) || null;
const tabById = (id) => tabs.find((t) => t.id === id) || null;

// ── 작업 큐(클라이언트 측 미러) ─────────────────────────────────
// 서버의 job 을 그대로 비추기만 한다. 여기서 동시성을 판단하지 않는다.
const jobs = new Map();         // jobId → {job, tabId, es, result}
let queueStats = { queued: 0, running: 0, concurrency: 1 };
let orphanReports = [];         // 탭이 닫힌 뒤 도착한 보고서 — 링크를 잃지 않게 보관

function tabBusy(id) {
  for (const rec of jobs.values()) {
    if (rec.tabId === id && rec.job && (rec.job.status === "QUEUED" || rec.job.status === "RUNNING")) return rec.job;
  }
  return null;
}

// ── 부트 ──────────────────────────────────────────────────────
async function boot() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error(res.status);
    CONFIG = await res.json();
  } catch (e) {
    CONFIG = { hasJsKey: false, hasStaticKey: false, hasAnthropic: false,
      languages: [{ code: "ko", name: "한국어" }], defaultLang: "ko",
      queue: { concurrency: 1 }, knowledge: {} };
  }
  initLangs();
  renderKeyChips();
  renderIntroHint();
  wireUI();
  renderLangMenu();
  renderKnowledge();
  refreshJobs();
  setInterval(refreshJobs, 5000);   // SSE 가 끊겨도 상태가 굳지 않도록 하는 안전망
  if (CONFIG.hasJsKey) loadGoogleMaps(CONFIG.jsApiKey);
}

// ── 언어 선택 ──────────────────────────────────────────────────
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
  const next = selectedLangs.filter((c) => checked.includes(c));
  checked.forEach((c) => { if (!next.includes(c)) next.push(c); });
  if (!next.length) next.push(valid0());
  selectedLangs = next;
  try { localStorage.setItem("gg_langs", JSON.stringify(selectedLangs)); } catch (e) {}
  const set = new Set(selectedLangs);
  $("#lang-options").querySelectorAll("input[type=checkbox]").forEach((cb) => (cb.checked = set.has(cb.value)));
  updateLangSummary();
}

function valid0() { return (CONFIG.defaultLang) || (LANGS[0] && LANGS[0].code) || "ko"; }
function primaryLang() { return selectedLangs[0] || valid0(); }
function startCoord() {
  const t = activeTab();
  const p = (t && t.extracted) || currentPose || {};
  return { lat: p.lat, lng: p.lng };
}
function researchOn() { const el = $("#research-on"); return el ? el.checked : true; }
function langName(code) { const l = LANGS.find((x) => x.code === code); return l ? l.name : code; }

/** 서버가 조사에 쓸 기준 언어(영어 우선) — 사용자가 미리 알 수 있게 표시한다. */
function baseLangOf(langs) {
  const pri = (CONFIG && CONFIG.baseLangPriority) || ["en", "fr", "es", "de", "ja", "ko", "zh"];
  for (const p of pri) if (langs.includes(p)) return p;
  return langs[0];
}

function updateLangSummary() {
  const names = selectedLangs.map(langName);
  $("#lang-summary").textContent = names[0] + (names.length > 1 ? ` +${names.length - 1}` : "");
  const hint = $("#lang-base-hint");
  if (hint) {
    const b = baseLangOf(selectedLangs);
    hint.textContent = selectedLangs.length > 1
      ? `조사는 ${langName(b)}로 1회 → 나머지는 번역`
      : `조사 언어: ${langName(b)}`;
  }
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
  if (CONFIG.hasJsKey) { h.innerHTML = ""; return; }
  h.innerHTML = `<div class="warn">
    ⚠️ <b>GOOGLE_MAPS_JS_API_KEY 미설정</b> — 링크 <b>추출은 지금도 동작</b>하지만, 인터랙티브 2분할 로드뷰/지도는
    키가 있어야 뜹니다.<br>루트에 <code>.env</code> 를 만들고 <code>GOOGLE_MAPS_JS_API_KEY</code> 를 넣은 뒤 서버를 재시작하세요.
    (<code>.env.example</code> 참고)</div>`;
}

// ── 탭 관리 ────────────────────────────────────────────────────
function snapshotActive() {
  const t = activeTab();
  if (!t) return;
  t.current = currentPose;
  t.album = album;
  t.reports = reportsMade;
  t.analysis = lastAnalysis;
  t.analysisFiles = lastAnalysisFiles;
}

function loadActive() {
  renderTabbar();
  const t = activeTab();
  if (!t) {
    currentPose = null; album = []; reportsMade = [];
    lastAnalysis = null; lastAnalysisFiles = [];
    clearFallback();   // 탭이 없으면 덮은 것도 없어야 한다 — 인트로 위에 캔버스가 남던 문제
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
    if (mapsReady) initSplit(t.current || t.extracted, t.id);
  } else {
    showFallback(t.extracted);
  }
}

function showIntro() {
  $("#intro").hidden = false;
  $("#fallback").hidden = true;
  ["#pose-card", "#album-card", "#analysis-card", "#reports-card"].forEach((s) => ($(s).hidden = true));
}

/** 링크의 정체성 키 — 같은 장소를 가리키는 URL 변형을 하나로 본다. */
function tabKey(pose) {
  if (pose.pano) return "pano:" + pose.pano;
  if (pose.lat != null && pose.lng != null) {
    return "geo:" + Number(pose.lat).toFixed(5) + "," + Number(pose.lng).toFixed(5);
  }
  return "url:" + (pose.resolved_url || pose.input_url || Math.random());
}

function newTab(pose) {
  snapshotActive();
  const key = tabKey(pose);
  const dup = tabs.find((t) => t.key === key);
  if (dup) { activeId = dup.id; return dup; }
  const id = ++tabSeq;
  const t = {
    id, key, extracted: pose,
    // 손으로 고른 키 목록은 새 필드를 조용히 떨군다 — 실제로 ya_offset 이 그렇게 사라져
    // 사진구체가 18° 돌아간 채 떴다. 링크에서 온 사실은 통째로 들고 다니고, 바뀌는 것만 덮는다.
    current: Object.assign({}, pose, {
      lat: pose.lat, lng: pose.lng, pano: pose.pano,
      heading: pose.heading, pitch: pose.pitch, fov: pose.fov,
      photo_url: pose.photo_url || null,
    }),
    album: [], reports: [], analysis: null, analysisFiles: [],
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
  const running = tabBusy(id);
  if (running && !confirm(
      "이 탭에서 보고서 작업이 진행 중입니다.\n" +
      "탭을 닫아도 작업은 서버에서 계속 진행되며, 완료된 보고서는 아래 '작업 큐'에 남습니다.\n\n닫을까요?")) {
    return;
  }
  const wasActive = id === activeId;
  tabs.splice(idx, 1);
  if (syncTabId === id) { syncArmed = false; syncTabId = null; }
  if (wasActive) {
    activeId = tabs.length ? tabs[Math.max(0, idx - 1)].id : null;
    loadActive();
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

/** 탭바는 **제자리 갱신**한다.
 *  예전처럼 innerHTML 로 통째로 다시 그리면, 백그라운드 작업이 끝나 재렌더가 일어나는
 *  순간(=사용자가 가장 클릭을 많이 하는 순간) mousedown 과 mouseup 사이에 노드가 교체돼
 *  클릭이 통째로 삼켜졌다. */
function renderTabbar() {
  const bar = $("#tabbar");
  if (!bar) return;
  if (!tabs.length) { bar.hidden = true; bar.innerHTML = ""; return; }
  bar.hidden = false;

  const seen = new Set();
  tabs.forEach((t) => {
    seen.add(String(t.id));
    let el = bar.querySelector(`.tab[data-id="${t.id}"]`);
    if (!el) {
      el = document.createElement("div");
      el.className = "tab";
      el.dataset.id = String(t.id);
      el.innerHTML = `<span class="tab-title"></span><span class="tab-busy" hidden>⏳</span>` +
        `<span class="tab-n" hidden></span><button class="tab-x" title="탭 닫기">×</button>`;
      el.addEventListener("click", (e) => {
        if (e.target.closest(".tab-x")) return;
        activateTab(Number(el.dataset.id));
      });
      el.querySelector(".tab-x").addEventListener("click", (e) => {
        e.stopPropagation();
        closeTab(Number(el.dataset.id));
      });
      bar.appendChild(el);
    }
    const title = tabTitle(t);
    const titleEl = el.querySelector(".tab-title");
    if (titleEl.textContent !== title) { titleEl.textContent = title; titleEl.title = title; }
    const job = tabBusy(t.id);
    el.classList.toggle("active", t.id === activeId);
    el.classList.toggle("busy", !!job);
    const busyEl = el.querySelector(".tab-busy");
    busyEl.hidden = !job;
    if (job) busyEl.title = job.status === "QUEUED" ? "대기 중" : (job.stage || "진행 중");
    const nEl = el.querySelector(".tab-n");
    const n = (t.album && t.album.length) || 0;
    nEl.hidden = !n;
    if (n) nEl.textContent = String(n);
  });
  Array.from(bar.querySelectorAll(".tab")).forEach((el) => {
    if (!seen.has(el.dataset.id)) el.remove();
  });
}

// ── Google Maps 로더 ───────────────────────────────────────────
function loadGoogleMaps(key) {
  window.__gmapsReady = () => {
    mapsReady = true;
    const t = activeTab();
    if (t) initSplit(t.current || t.extracted, t.id);
  };
  const s = document.createElement("script");
  s.async = true;
  s.src = "https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(key) +
    "&v=weekly&loading=async&callback=__gmapsReady";
  s.onerror = () =>
    setStatus("Google Maps SDK 로드 실패 — API 키/리퍼러(http://localhost:*) 설정 확인", "err", 6000);
  document.head.appendChild(s);
}

// ── UI 배선 ────────────────────────────────────────────────────
function wireUI() {
  $("#btn-extract").addEventListener("click", doExtract);
  $("#url").addEventListener("keydown", (e) => { if (e.key === "Enter") doExtract(); });
  $("#btn-paste").addEventListener("click", async () => {
    try {
      const t = await navigator.clipboard.readText();
      if (t) { $("#url").value = t.trim(); $("#url").focus(); }
    } catch (e) { setStatus("클립보드 접근 실패 — 직접 붙여넣으세요", "err"); }
  });
  $("#btn-capture").addEventListener("click", doCapture);
  $("#btn-quick-report").addEventListener("click", doQuickReport);
  $("#btn-analyze").addEventListener("click", doAnalyze);
  $("#btn-album-report").addEventListener("click", doAlbumReport);
  $("#btn-report").addEventListener("click", doReport);
  const syn = $("#btn-synth");
  if (syn) syn.addEventListener("click", doSynthesize);
  const rt = $("#research-on");
  if (rt) {
    try { const s = localStorage.getItem("gg_research"); if (s !== null) rt.checked = s === "1"; } catch (e) {}
    rt.addEventListener("change", () => {
      try { localStorage.setItem("gg_research", rt.checked ? "1" : "0"); } catch (e) {}
    });
  }
  $("#url").focus();
}

// ── 공통 fetch ─────────────────────────────────────────────────
/** 항상 res.ok 를 확인한다. 서버가 text/plain 500 을 주면 .json() 이 던지고,
 *  예전에는 그게 전부 "네트워크 오류"로 뭉개져 원인을 알 수 없었다. */
async function api(url, opts) {
  const res = await fetch(url, opts);
  const ct = res.headers.get("content-type") || "";
  let body = null;
  if (ct.includes("application/json")) {
    try { body = await res.json(); } catch (e) { body = null; }
  } else {
    const txt = await res.text().catch(() => "");
    body = txt ? { detail: txt.slice(0, 300) } : null;
  }
  if (!res.ok) {
    const msg = (body && (body.detail || body.message)) || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

// ── 추출 ───────────────────────────────────────────────────────
async function doExtract() {
  const url = $("#url").value.trim();
  if (!url) return;
  if (extracting) { setStatus("이미 추출 중입니다", "err"); return; }
  extracting = true;
  const myToken = ++extractToken;      // 응답 역전 시 늦게 온 것을 버린다
  $("#btn-extract").disabled = true;
  setStatus("추출 중…", "", 0);
  let pose;
  try {
    pose = await api("/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  } catch (e) {
    if (myToken === extractToken) setStatus("추출 오류: " + e.message, "err", 6000);
    return;
  } finally {
    extracting = false;
    $("#btn-extract").disabled = false;
  }
  if (myToken !== extractToken) return;   // 더 최신 추출이 이미 처리됨
  const nBefore = tabs.length;
  newTab(pose);
  loadActive();
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
    ["pano", pose.pano ? `<span title="${esc(pose.pano)}">${esc(short(pose.pano))}</span> <span class="badge">${esc(pose.pano_kind || "?")}</span>` : "— (좌표로 최근접 탐색)"],
  ];
  $("#pose-body").innerHTML = rows
    .map(([k, v]) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`)
    .join("");
  const gmaps = pose.resolved_url || pose.input_url ||
    `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${pose.lat},${pose.lng}`;
  $("#open-gmaps").href = gmaps;
  $("#pose-card").hidden = false;
  updateActionButtons();
}

/** 버튼 활성 상태를 한곳에서 계산한다(여러 곳에서 제각각 켜고 끄던 것을 정리). */
function updateActionButtons() {
  const canCapture = CONFIG.hasStaticKey || CONFIG.hasJsKey;
  const job = activeId != null ? tabBusy(activeId) : null;
  const anySel = album.some((a) => a.sel);

  const cap = $("#btn-capture");
  cap.disabled = !canCapture || !!job;
  cap.title = !canCapture
    ? "캡처는 GOOGLE_MAPS_JS_API_KEY 또는 GOOGLE_MAPS_STATIC_KEY 필요"
    : (job ? "이 탭의 작업이 끝난 뒤 가능합니다" : "자동: 정적 실패 시 브라우저 렌더로 폴백");

  const quick = $("#btn-quick-report");
  quick.disabled = !canCapture || !CONFIG.hasAnthropic || !!job;
  quick.title = !canCapture ? "캡처 키 필요"
    : !CONFIG.hasAnthropic ? "분석에 ANTHROPIC_API_KEY 필요"
    : job ? "이 탭의 작업이 진행 중입니다" : "지금 화면을 캡처 → 분석 → 보고서 (큐에 등록)";

  const an = $("#btn-analyze");
  an.disabled = !anySel || !CONFIG.hasAnthropic || !!job;
  an.title = !CONFIG.hasAnthropic ? "분석은 ANTHROPIC_API_KEY 필요" : (job ? "작업 진행 중" : "");

  const ar = $("#btn-album-report");
  ar.disabled = !anySel || !CONFIG.hasAnthropic || !!job;
  ar.title = !CONFIG.hasAnthropic ? "보고서는 ANTHROPIC_API_KEY 필요" : (job ? "작업 진행 중" : "선택 장면 분석 + 보고서");

  const rp = $("#btn-report");
  rp.disabled = !!job;
}

// ── 2분할 뷰어 ─────────────────────────────────────────────────
function initSplit(pose, tabId) {
  if (!mapsReady || !pose) return;
  $("#intro").hidden = true;
  $("#fallback").hidden = true;

  const myToken = ++loadToken;
  syncArmed = false;                 // 로드가 끝나기 전에는 절대 기록하지 않는다
  syncTabId = null;
  syncWantPano = null;

  const hasLoc = pose.lat != null && pose.lng != null;
  const p = hasLoc ? { lat: Number(pose.lat), lng: Number(pose.lng) } : null;

  if (!map) {
    // 최초 1회. 좌표가 없는 pano 전용 링크여도 지도는 만들어야 하므로 (0,0) 으로 띄운 뒤
    // 파노가 올라오면 syncFromPano 가 제자리를 잡아준다.
    const center = p || { lat: 0, lng: 0 };
    map = new google.maps.Map($("#map"), {
      center, zoom: p ? 14 : 2, mapTypeId: "hybrid", streetViewControl: true,
      fullscreenControl: false,
    });
    // 파노 ID 를 이미 아는데도 생성자에 position 을 넘기면 **암묵적인 위치 조회가 하나 더**
    // 돌고, 곧이어 부르는 setPano() 와 경쟁한다. 늦게 도착한 쪽이 이기면 화면이 어긋난다
    // (사용자 신고: 첫 추출은 검은데 로드뷰맨을 옮겼다 되돌리면 뜬다 — 그 수동 동작이
    //  경쟁 없는 단일 로드를 강제한 것이다). 그래서 ID 가 있으면 position 을 주지 않는다.
    pano = new google.maps.StreetViewPanorama($("#pano"), {
      ...(pose.pano ? {} : { position: center }),
      pov: { heading: pose.heading || 0, pitch: pose.pitch || 0 },
      zoom: 1, motionTracking: false, linksControl: true, addressControl: true,
      fullscreenControl: false,
    });
    map.setStreetView(pano);
    marker = new google.maps.Marker({ position: center, map });
    pano.addListener("position_changed", syncFromPano);
    pano.addListener("pov_changed", syncFromPano);
    // 파노가 바뀌면 우회 이미지를 즉시 버린다(이전 장소를 덮어 보이면 안 된다).
    pano.addListener("pano_changed", onPanoChanged);
    // 시점을 돌리면 우회 이미지도 그 시점으로 다시 받는다. 막힌 적 없으면 아무 일도 안 한다.
    pano.addListener("pov_changed", () => {
      // pin(사진구체) 중에는 뷰어가 직접 그린다. 여기서 또 그리면 밑 파노의
      // — 즉 **다른 장면의** — 시점으로 정지 이미지를 받아 낭비하고 헷갈린다.
      if (gpmsBlocked && !photoPinned) scheduleFallback();
    });
    svc = new google.maps.StreetViewService();
  } else if (p) {
    map.setCenter(p);
    marker.setPosition(p);
    pano.setPov({ heading: pose.heading || 0, pitch: pose.pitch || 0 });
  }

  clearFallback();
  if (pose.pano) {
    armWhenLoaded(myToken, pose.pano, tabId);
    // **먼저 해석되는 파노인지 확인하고 나서** 넘긴다.
    // CIHM…/일부 사진구체는 Maps JS API 가 아예 모른다(실측: ID·좌표 조회 모두 ZERO_RESULTS).
    // 그런 ID 를 setPano() 에 넣으면 조용히 아무 일도 일어나지 않고, 뷰어는 **직전 탭의 장면을
    // 그대로 들고 있는다** — 사용자에게는 "먹통" 또는 "엉뚱한 파노"로 보인다.
    svc.getPanorama({ pano: pose.pano })
      .then(() => { if (myToken === loadToken) pano.setPano(pose.pano); })
      .catch(() => { if (myToken === loadToken) panoUnavailable(pose, myToken, tabId); });
  } else if (p) {
    const req = {
      location: p, radius: (CONFIG.defaults && CONFIG.defaults.radius) || 50,
      preference: google.maps.StreetViewPreference.NEAREST,
      sources: [google.maps.StreetViewSource.OUTDOOR],
    };
    svc.getPanorama(req)
      .then(({ data }) => {
        if (myToken !== loadToken) return;             // 다른 탭으로 전환됨 → stale 무시
        armWhenLoaded(myToken, data.location.pano, tabId);
        pano.setPano(data.location.pano);
      })
      .catch(() => {
        if (myToken !== loadToken) return;
        // 커버리지 없음. 파노라마는 **이전 탭 화면을 그대로 들고 있다**.
        // 여기서 무장하면 그 이전 탭의 좌표가 이 탭에 기록된다 → 절대 무장하지 않는다.
        syncArmed = false; syncTabId = null;
        setStatus("이 위치엔 스트리트뷰 커버리지가 없습니다 — 이 탭에서는 캡처가 이전 위치를 쓰지 않도록 동기화를 꺼둡니다", "err", 7000);
      });
  } else {
    setStatus("좌표도 pano 도 없는 링크입니다", "err", 5000);
  }
}

/** 요청한 파노가 **실제로 올라온 뒤에만** 동기화를 켠다.
 *  시간(1000ms)이 아니라 사실을 기준으로 하므로, 느린 회선이나 느린 getPanorama 왕복에도
 *  이전 탭의 좌표가 새 탭에 새는 일이 없다. */
function armWhenLoaded(myToken, wantPano, tabId) {
  syncWantPano = wantPano;
  const tryArm = () => {
    if (myToken !== loadToken) return false;                 // 더 최신 로드가 있다
    if (!pano || !pano.getPano) return false;
    if (wantPano && pano.getPano() !== wantPano) return false; // 아직 그 파노가 아니다
    syncArmed = true;
    syncTabId = tabId;
    reportViewerState(pano, tabById(tabId));   // 로드 직후 실제 상태를 즉시 보여준다
    return true;
  };
  if (tryArm()) return;
  const lis = google.maps.event.addListener(pano, "pano_changed", () => {
    if (tryArm() || myToken !== loadToken) google.maps.event.removeListener(lis);
  });
  // 안전망: pano_changed 가 오지 않는 경우에도 "실제로 그 파노인지"는 반드시 확인한다.
  setTimeout(() => { if (myToken === loadToken && !syncArmed) tryArm(); }, 3000);
}

/** 뷰어가 **실제로** 무엇을 보여주고 있는지 포즈 카드에 드러낸다.
 *
 *  이게 없으면 "포즈 카드엔 pano A 라고 쓰여 있는데 화면은 pano B(그리고 검정)" 같은
 *  상황을 사용자가 알 방법이 없다. 실제로 그런 신고가 있었다 — 카드는 official 파노를
 *  가리키는데 뷰어 저작권은 제3자였다. 파노 ID 형식으로는 제3자 여부를 알 수 없고
 *  (22자 official 형식인데 제3자인 경우가 있다), **저작권 문자열만이** 판별자다.
 */
function reportViewerState(p, tab) {
  const el = $("#viewer-state");
  if (!el || !p) return;
  // 링크의 원본 이미지를 띄운 상태면 **그 사실을 말한다**. 밑에 깔린 파노는 다른 장면이므로
  // 그걸 "표시 중"으로 보여 주면 정확히 반대되는 정보를 주게 된다(사용자 혼동의 원인이었다).
  if (photoPinned && pinnedPose) {
    el.innerHTML =
      `<span class="vs-k">표시 중</span><code>${esc(short(pinnedPose.pano || ""))}</code>`
      + `<span class="vs-cp third">사진구체 · 자체 뷰어</span>`
      + `<span class="vs-note">이 파노는 지도 SDK 가 제공하지 않아 원본 파노라마를 직접 띄웁니다.`
      + ` <b>끌어서 돌리고 휠로 확대</b>할 수 있으며, 캡처는 지금 보이는 시점을 그대로 찍습니다.</span>`;
    el.hidden = false;
    return;
  }
  let loc = null;
  try { loc = p.getLocation(); } catch (e) {}
  const shown = p.getPano ? p.getPano() : "";
  const want = (tab && tab.extracted && tab.extracted.pano) || "";
  // 저작권은 StreetViewLocation 에 **없다**. getLocation() 이 주는 것은 위치 정보뿐이고,
  // copyright 는 StreetViewService.getPanorama() 가 주는 StreetViewPanoramaData 에 있다.
  // 예전엔 loc.copyright 를 읽어서 항상 빈 값이었고, 그래서 제3자 파노인데도
  // 저작권 칩과 안내가 한 번도 뜨지 않았다(사용자 스크린샷으로 확인).
  const cp = panoCopyright[shown] || "";
  if (shown && !(shown in panoCopyright)) fetchCopyright(shown, tab);
  const desc = (loc && (loc.description || loc.shortDescription)) || "";
  const official = !cp || cp.indexOf("Google") !== -1;
  const mismatch = want && shown && want !== shown;

  const bits = [];
  if (shown) bits.push(`<span class="vs-k">표시 중</span><code>${esc(short(shown))}</code>`);
  if (cp) bits.push(`<span class="vs-cp${official ? "" : " third"}">${esc(cp)}</span>`);
  if (desc) bits.push(`<span class="vs-d">${esc(desc)}</span>`);
  if (mismatch) {
    bits.push(`<span class="vs-warn">⚠ 링크의 파노(<code>${esc(short(want))}</code>)와 다릅니다 —`
      + ` 캡처는 <b>지금 보이는 장면</b>을 찍습니다</span>`);
  }
  if (!official) {
    bits.push(`<span class="vs-note">사용자 기여 파노(${esc(cp)})입니다 — 타일이 제한되면`
      + ` 직접 이미지 경로로 자동 전환합니다.</span>`);
  }
  el.innerHTML = bits.join("");
  el.hidden = bits.length === 0;
}

/* ── 제3자 파노 타일 제한(429) 우회 ───────────────────────────────────────────
 * 사용자 기여 파노의 타일은 키 없는 lh3.googleusercontent.com/gpms-cs-s/<토큰> 에서
 * 오는데, 그 CDN 은 IP 단위 제한을 걸어 429 를 주고 화면이 통째로 검게 남는다.
 *
 * 핵심 관찰: **429 로 막힌 요청에도 토큰은 URL 에 그대로 있다.** 그리고 같은 토큰의
 * photo 형식(`=w..-h..-k-no-pi<pitch>-ya<heading>-ro0-fo<fov>`)은 타일과 달리 그
 * 제한에 걸리지 않는다(실측: 타일 429 인 그 순간에 photo 는 HTTP 200, 1280x800, 밝기 118.9).
 * 그래서 토큰만 주우면 원하는 시점 그대로 되살릴 수 있다.
 *
 * 브라우저에서는 Maps JS API 내부의 img 요청을 가로챌 수 없지만, PerformanceObserver 가
 * 그 요청들을 resource 엔트리로 흘려 주므로 거기서 토큰을 줍는다.
 */
const panoCopyright = Object.create(null);   // pano id → 저작권 문자열 (없으면 "")
let gpmsToken = null;                        // 마지막으로 관측한 gpms 토큰
let gpmsBlocked = false;                     // 그 토큰의 타일이 실제로 막혔는가
let fallbackTimer = null;
let tileWatch = null;
let watchedPano = null;
let photoPinned = false;
let pinnedPose = null;
let pinnedUnder = null;    // pin 시점의 '밑바닥' 파노 id — 이게 바뀌면 사용자가 이동한 것이다
let pinnedTabId = null;
let lastPaintKey = "";   // 링크가 준 원본 이미지를 띄운 상태 — 타일 감시가 걷어내면 안 된다

/** 저작권은 getPanorama() 로만 얻을 수 있다. 파노당 한 번만 묻고 캐시한다. */
function fetchCopyright(panoId, tab) {
  panoCopyright[panoId] = "";                 // 재진입 방지 — 응답 오면 덮어쓴다
  if (!svc) return;
  svc.getPanorama({ pano: panoId })
    .then(({ data }) => {
      panoCopyright[panoId] = (data && data.copyright) || "";
      if (pano && pano.getPano && pano.getPano() === panoId) reportViewerState(pano, tab);
    })
    .catch(() => {});
}

/** 타일이 실제로 안 왔는지 판정한다.
 *
 *  페이지 안에서는 요청을 볼 수 없다 — Maps JS API 가 파노 타일을 **워커에서** 받기 때문에
 *  PerformanceObserver 에 한 건도 잡히지 않는다(실측: 429 가 났는데도 perfCount=0).
 *
 *  그래서 요청이 아니라 **결과**를 본다. 캔버스 오염(taint)이 신호가 된다:
 *    · 타일이 하나라도 그려졌다 → 교차출처 이미지가 들어갔다 → getImageData 가 SecurityError
 *    · 타일이 한 장도 못 왔다   → 오염될 일이 없다 → 픽셀을 읽을 수 있고, 그 값은 검다
 *  즉 "읽히는데 검다" 가 곧 "타일이 안 왔다" 다. 타이밍 추정이 아니라 화면 그 자체다.
 */
function tilesMissing() {
  // 우리가 만든 사진구체 캔버스는 제외한다 — 그건 교차출처가 아니어서 늘 읽히고,
  // 그러면 "읽히는데 검다" 판정이 엉뚱하게 성립할 수 있다.
  const cv = document.querySelector("#pano canvas:not(.sphere-canvas)");
  if (!cv || !cv.width) return false;
  try {
    const s = 24;
    const c = document.createElement("canvas");
    c.width = s; c.height = s;
    const g = c.getContext("2d");
    g.drawImage(cv, 0, 0, s, s);
    const d = g.getImageData(0, 0, s, s).data;   // 오염됐으면 여기서 던진다
    let sum = 0;
    for (let i = 0; i < d.length; i += 4) sum += (d[i] + d[i + 1] + d[i + 2]) / 3;
    return sum / (d.length / 4) < 8;             // 사실상 완전한 검정
  } catch (e) {
    return false;                                 // 오염됨 = 타일이 왔다 = 정상
  }
}

/** 파노가 올라온 뒤 몇 번 확인한다 — 타일은 조금씩 늦게 도착한다.
 *
 *  pano_changed 는 한 파노를 여는 동안에도 여러 번 튈 수 있다. 그래서 "무엇을 보고 있는지"
 *  를 파노 ID 로 못 박고, **ID 가 실제로 바뀐 경우에만** 상태를 버린다. 예전엔 이벤트마다
 *  clearFallback() 을 불러서, 서버 토큰 조회(≈10초)가 돌아오는 사이에 그 결과가 지워졌다.
 */
/** 파노가 바뀔 때의 **유일한** 판단 지점.
 *
 *  예전에는 pin 상태에서 그냥 조기 반환했다. 그 결과 오버레이가 영원히 남아,
 *  사용자가 로드뷰맨을 옮겨도 화면이 그대로였다(실측: 스크린샷 3장이 픽셀 단위로 동일).
 *  오버레이는 플래그가 아니라 **지금 보고 있는 파노의 함수**여야 한다 —
 *  밑바닥 파노가 pin 시점과 달라지면 그것은 곧 '사용자가 다른 곳으로 갔다'는 뜻이다.
 */
function onPanoChanged() {
  const id = pano && pano.getPano ? pano.getPano() : "";
  if (!photoPinned) { watchTiles(); return; }
  if (!id || id === pinnedUnder) return;            // 아직 같은 자리다
  const tid = pinnedTabId;
  clearFallback();                                   // 오버레이를 걷는다
  syncArmed = true; syncTabId = tid;                 // 이제 진짜 파노를 따라간다
  reportViewerState(pano, tabById(tid));
  syncFromPano();
  setStatus("원본 이미지 표시를 끄고 이동한 파노를 보여줍니다", "ok", 4000);
  watchTiles();
}

function watchTiles() {
  const id = pano && pano.getPano ? pano.getPano() : "";
  if (!id) return;
  if (photoPinned) return;                 // 링크 원본 이미지를 띄운 상태 — 건드리지 않는다
  if (id === watchedPano) return;          // 같은 파노를 두 번 감시하지 않는다
  watchedPano = id;
  gpmsToken = null; gpmsBlocked = false;
  hideFallback();

  // **공식 파노는 감시하지 않는다.** WebGL 은 preserveDrawingBuffer 가 기본 false 라,
  // 실제 GPU 브라우저에서 캔버스를 뒤늦게 읽으면 화면과 무관하게 늘 검게 나온다
  // (실측: 헤드리스는 mean 117, 같은 화면이 헤디드에선 mean 0 — 합성 스크린샷은 126.4).
  // 그래서 '읽히는데 검다' 판정은 CORS 가 열려 있는 공식 타일에서 항상 참이 돼 버린다.
  // 제3자 타일은 CORS 가 없어 캔버스를 오염시키므로 그 경우엔 판정이 성립한다.
  if (!(id in panoCopyright)) fetchCopyright(id, tabById(syncTabId));
  const cp = panoCopyright[id];
  if (cp === undefined) { setTimeout(() => { if (watchedPano === id) recheck(id); }, 900); return; }
  if (!cp || cp.indexOf("Google") !== -1) return;   // 공식 — 감시 불필요

  startTileWatch(id);
}

/** 저작권 응답이 늦게 오는 경우를 위한 재확인. */
function recheck(id) {
  if (photoPinned || watchedPano !== id) return;
  const cp = panoCopyright[id];
  if (!cp || cp.indexOf("Google") !== -1) return;
  startTileWatch(id);
}

function startTileWatch(id) {
  let n = 0;
  clearInterval(tileWatch);
  tileWatch = setInterval(() => {
    if (watchedPano !== id) { clearInterval(tileWatch); return; }   // 다른 파노로 이동
    if (++n > 8) { clearInterval(tileWatch); return; }
    if (!tilesMissing()) return;
    clearInterval(tileWatch);
    gpmsBlocked = true;
    // 토큰은 서버만 알아낼 수 있다 — Maps JS 는 타일을 워커에서 받아 페이지에서 안 보인다.
    api(`/api/pano-photo?pano=${encodeURIComponent(id)}`)
      .then((r) => {
        if (!r || !r.base || watchedPano !== id) return;
        gpmsToken = r.base;
        paintFallback();
      })
      .catch(() => {});
  }, 1000);
}

function scheduleFallback() {
  clearTimeout(fallbackTimer);
  fallbackTimer = setTimeout(paintFallback, 200);
}

/* ── 사진구체: 그림이 아니라 **뷰어** ──────────────────────────────────────────
 *
 * 예전에는 서버가 잘라 준 평면 이미지를 <img> 로 덮었다. 그건 pointer-events:none 인
 * 그림이었고, 그 아래 파노는 비어 있었으므로(ZERO_RESULTS) 사용자가 잡아 끌 수 없었다.
 * 화면은 떴지만 **기능이 없었다** — 사용자 신고: "시점 돌리기가 안 된다".
 *
 * 이제 등장방형 원본을 텍스처로 받아 직접 투영한다(sphere.js). 구글 지도가 하는 것과 같다.
 * 저해상도를 먼저 띄워 즉시 보이게 하고, 고해상도로 조용히 갈아끼운다.
 */
let sphere = null;
let sphereToken = null;

function sphereUrl(base, w) {
  return `/api/pano-image?base=${encodeURIComponent(base)}&w=${w}&mode=equi`;
}

function startSphere(pose, tabId) {
  stopSphere();
  const host = $("#pano");
  if (!host || !gpmsToken) return;
  hideFallback();

  const make = window.createSphereViewer;
  if (!make) { paintFallback(); return; }          // sphere.js 미로드 → 옛 방식으로 물러선다

  // 텍스처 경도 오프셋. 0.5 는 "heading == lh3 ya" 를 뜻한다(실측 교정값).
  // 그런데 lh3 의 ya 는 사진구체 자체 좌표계라 진북 heading 과 어긋날 수 있다
  // (실측: 같은 링크에서 h=36.84 인데 ya=18.8405). 링크에서 뽑은 차이만큼 돌려 준다.
  const yaOff = Number(pose.ya_offset) || 0;
  sphere = make(host, {
    heading: Number(pose.heading) || 0,
    pitch: Number(pose.pitch) || 0,
    fov: Math.max(20, Math.min(120, Number(pose.fov) || 75)),   // 링크의 `75y` = 수직 FOV
    lonOffset: 0.5 - yaOff / 360,
    onChange: (pov) => onSphereView(pov, tabId),
  });
  if (!sphere) { paintFallback(); return; }        // WebGL 없음 → 정지 이미지라도 보여준다

  sphereToken = gpmsToken;
  const base = gpmsToken;
  // ① 1024 를 먼저 — 즉시 보이게. ② 4096 으로 조용히 교체 — 선명하게.
  sphere.setTexture(sphereUrl(base, 1024))
    .then(() => {
      if (!sphere || sphereToken !== base) return;
      return sphere.setTexture(sphereUrl(base, 4096));
    })
    .catch((e) => {
      if (!sphere || sphereToken !== base) return;
      // 4096 이 GPU 한계를 넘는 경우가 있다 — 2048 로 한 번 더 물러선다.
      return sphere.setTexture(sphereUrl(base, 2048)).catch(() => {
        setStatus(`파노라마를 띄우지 못했습니다 — ${e.message || e}`, "err", 6000);
      });
    });
  onSphereView(sphere.getPov(), tabId);
}

function stopSphere() {
  if (sphere) { try { sphere.destroy(); } catch (e) { /* 무시 */ } }
  sphere = null; sphereToken = null;
}

/** 뷰어에서 시점이 바뀌면 **탭 포즈에 그대로 기록**한다.
 *  캡처는 서버가 pose 의 heading/pitch/fov 로 이미지를 다시 겨눠 받는다. 여기서 기록하지
 *  않으면 "현재 장면 캡처"가 화면과 다른 장면을 찍는다 — 지오게서 복기 도구에서 그건 오답이다. */
function onSphereView(pov, tabId) {
  const t = tabById(tabId);
  if (!t || !pov) return;
  t.current = Object.assign({}, t.current || t.extracted, {
    heading: pov.heading, pitch: pov.pitch, fov: pov.fov,
  });
  if (t.id === activeId) { currentPose = t.current; renderPose(t.current); }
}

/** Maps JS API 가 이 파노를 모를 때. 조용히 실패하지 않고 **가진 것으로 최선**을 한다.
 *
 *  ① 링크에 `!6s` 직접 이미지가 있으면 그것을 그대로 띄운다. 사진구체(CIHM…)는 JS API 가
 *     서비스하지 않지만 그 이미지는 받아진다 — 구글 지도 본체가 쓰는 바로 그 주소다.
 *  ② 없으면 좌표로 최근접을 찾되, **다른 장소로 대체됐음을 반드시 알린다**.
 *  ③ 둘 다 안 되면 명확히 말한다. 직전 탭 화면을 그대로 두고 침묵하는 것이 최악이다.
 */
function panoUnavailable(pose, myToken, tabId) {
  const note = (msg, kind) => setStatus(msg, kind || "err", 8000);
  if (pose.photo_url) {
    photoPinned = true; pinnedPose = pose; pinnedTabId = tabId;
    gpmsToken = String(pose.photo_url).replace(/=w\d+-h\d+.*$/, "");
    gpmsBlocked = true;
    watchedPano = pose.pano;
    // 지금 밑에 깔려 있는 파노를 앵커로 잡는다 — 여기서 달라지면 사용자가 이동한 것이다.
    pinnedUnder = (pano.getPano && pano.getPano()) || null;
    lastPaintKey = "";
    startSphere(pose, tabId);
    reportViewerState(pano, tabById(tabId));
    note("이 파노는 지도 SDK 가 제공하지 않는 사진구체입니다 — 원본 파노라마를 직접 띄웁니다", "ok");
    return;
  }
  if (pose.lat == null || pose.lng == null) {
    note("이 파노는 지도 SDK 로 열 수 없고, 좌표도 없어 대체할 수 없습니다");
    return;
  }
  svc.getPanorama({
    location: { lat: Number(pose.lat), lng: Number(pose.lng) },
    radius: (CONFIG.defaults && CONFIG.defaults.radius) || 50,
    preference: google.maps.StreetViewPreference.NEAREST,
    sources: [google.maps.StreetViewSource.OUTDOOR],
  })
    .then(({ data }) => {
      if (myToken !== loadToken) return;
      // 링크가 가리킨 파노가 아니다 — 동기화는 켜지 않는다(캡처가 엉뚱한 곳을 찍지 않도록).
      syncArmed = false; syncTabId = null;
      pano.setPano(data.location.pano);
      note("링크의 파노를 열 수 없어 **근처 다른 파노**로 대체했습니다 — 같은 장소가 아닐 수 있습니다");
    })
    .catch(() => {
      if (myToken !== loadToken) return;
      syncArmed = false; syncTabId = null;
      note("이 위치엔 지도 SDK 가 제공하는 스트리트뷰가 없습니다");
    });
}


/** 잡아 둔 토큰으로 현재 시점의 이미지를 받아 파노 위에 덮는다. */
function paintFallback() {
  const host = $("#pano");
  if (!host || !pano || !gpmsToken || !gpmsBlocked) return;
  let img = $("#pano-fallback");
  if (!img) {
    img = document.createElement("img");
    img.id = "pano-fallback";
    host.appendChild(img);
  }
  // **항상 살아 있는 시점을 쓴다.** 예전엔 pin 상태에서 링크의 고정 각도를 썼는데,
  // 그러면 URL 이 늘 같아 이미지가 그대로 박혀 버렸다(사용자 신고: 아무리 돌려도 안 바뀜).
  // 사진구체 URL 은 ya/pi/fo 로 시점을 지정할 수 있으므로, 밑 파노를 돌리면 그대로 따라간다.
  // pin 할 때 밑 파노의 pov 를 링크 값에 맞춰 두므로 시작 각도도 어긋나지 않는다.
  const pov = pano.getPov ? pano.getPov() : { heading: 0, pitch: 0 };
  const w = Math.min(1600, Math.max(640, host.clientWidth || 900));
  const h = Math.min(1200, Math.max(400, host.clientHeight || 600));
  const fov = Math.max(20, Math.min(120, 180 / Math.pow(2, pano.getZoom ? pano.getZoom() : 1)));
  // 각도를 1도로 반올림한다 — 드래그 중 URL 이 반복되므로 브라우저 캐시가 그대로 듣는다.
  const ya = Math.round(((pov.heading || 0) % 360 + 360) % 360);
  const pi = Math.round(pov.pitch || 0);
  const fo = Math.round(fov);
  const key = `${ya}|${pi}|${fo}|${w}x${h}|${gpmsToken.slice(-12)}`;
  if (key === lastPaintKey && img.classList.contains("on")) return;
  lastPaintKey = key;

  // **서버를 거쳐 받는다.** 이미지 주소 자체는 멀쩡하지만(실측: 서버 217KB, 헤드리스 200),
  // 실제 사용자 브라우저에서는 검게 남는 경우가 있었다. 광고/추적 차단 확장이나 백신 웹실드가
  // googleusercontent.com 을 막으면 페이지 코드로는 손쓸 방법이 없기 때문이다.
  // 같은 출처로 받으면 그 층이 통째로 사라지고, 덤으로 캔버스도 오염되지 않는다.
  const url = `/api/pano-image?base=${encodeURIComponent(gpmsToken)}`
    + `&w=${w}&h=${h}&pi=${pi}&ya=${ya}&fo=${fo}`;

  // 미리 받아 두고 **디코드가 끝난 뒤에** 바꾼다. img.src 를 바로 갈면 드래그 중 화면이 깜빡인다.
  const pre = new Image();
  pre.onload = () => {
    if (lastPaintKey !== key) return;          // 그새 더 최신 시점이 요청됐다
    img.src = url;
    img.classList.add("on");
  };
  pre.onerror = () => {
    if (lastPaintKey !== key) return;
    lastPaintKey = "";
    // 조용히 검게 두지 않는다 — 무엇이 실패했는지 말한다.
    setStatus("원본 이미지를 받지 못했습니다 — 서버 로그를 확인하세요", "err", 6000);
  };
  pre.src = url;
}

/** 파노 이동 시 우회 이미지를 걷는다 — 다른 장소를 덮어 보이면 절대 안 된다. */
function clearFallback() {
  watchedPano = null; gpmsToken = null; gpmsBlocked = false; photoPinned = false; pinnedPose = null;
  pinnedUnder = null; pinnedTabId = null; lastPaintKey = "";
  stopSphere();          // 덮은 것은 같은 자리에서 걷는다 — 뷰어도 예외가 아니다
  clearTimeout(fallbackTimer); clearInterval(tileWatch);
  hideFallback();
}

function hideFallback() {
  const img = $("#pano-fallback");
  if (img) { img.classList.remove("on"); img.removeAttribute("src"); }
}

function syncFromPano() {
  if (!pano || !syncArmed || syncTabId == null) return;
  const pos = pano.getPosition();
  if (!pos) return;
  // 무장 이후에도 파노가 우리가 요청한 것인지 재확인 — 사용자가 화살표로 이동하면
  // pano id 는 바뀌지만 그건 정상적인 '이 탭 안에서의 이동'이므로 허용한다.
  const pov = pano.getPov();
  if (map) { map.setCenter(pos); }
  if (marker) marker.setPosition(pos);
  const nextPano = pano.getPano();
  const t0 = tabById(syncTabId);
  const prev = (t0 && t0.current) || {};
  reportViewerState(pano, t0);
  const same = prev.pano && prev.pano === nextPano;
  // 여기도 마찬가지 — 이전 포즈를 바탕에 깔고 바뀐 것만 덮는다.
  const next = Object.assign({}, prev, {
    lat: pos.lat(), lng: pos.lng(), pano: nextPano,
    heading: pov.heading, pitch: pov.pitch,
    fov: currentPose ? currentPose.fov : 90, zoom: pano.getZoom(),
    // 같은 파노 안에서 시점만 돌린 것이면 이미지 URL 이 그대로 유효하다
    // (시점은 URL 파라미터로 다시 겨눈다). 파노가 바뀌면 그 파노의 사실은 전부 버린다.
    photo_url: same ? (prev.photo_url || null) : null,
    photo_ya: same ? (prev.photo_ya != null ? prev.photo_ya : null) : null,
    photo_pi: same ? (prev.photo_pi != null ? prev.photo_pi : null) : null,
    ya_offset: same ? (prev.ya_offset || 0) : 0,
  });
  const t = tabById(syncTabId);       // ← activeTab() 이 아니다. 무장 시점에 고정된 탭.
  if (t) t.current = next;
  if (syncTabId === activeId) currentPose = next;
}

function showFallback(pose) {
  $("#intro").hidden = true;
  const fb = $("#fallback");
  const gmaps = pose.resolved_url || pose.input_url;
  fb.innerHTML = `<div class="fb">
    <h2>📍 추출됨 — 인터랙티브 지도는 API 키가 필요합니다</h2>
    <p>좌표 <b>${fmt(pose.lat)}, ${fmt(pose.lng)}</b> · heading ${fmt(pose.heading)}° · pitch ${fmt(pose.pitch)}°</p>
    <p><code>GOOGLE_MAPS_JS_API_KEY</code> 를 <code>.env</code> 에 넣으면 이 자리에 로드뷰▲/지도▼ 2분할이 뜹니다.</p>
    ${gmaps ? `<p><a href="${esc(gmaps)}" target="_blank" rel="noopener">→ Google 지도에서 바로 보기 ↗</a></p>` : ""}
    ${CONFIG.hasStaticKey ? `<p class="muted">우측 <b>“현재 장면 캡처”</b>는 Static API 로 지금도 동작합니다.</p>` : ""}
    <div id="fb-preview"></div>
  </div>`;
  fb.hidden = false;
}

// ── 캡처 ───────────────────────────────────────────────────────
async function doCapture() {
  if (!currentPose || (currentPose.lat == null && !currentPose.pano)) {
    setStatus("먼저 링크를 추출하세요", "err");
    return;
  }
  const originId = activeId;
  if (tabBusy(originId)) { setStatus("이 탭은 이미 작업이 진행 중입니다", "err"); return; }
  const btn = $("#btn-capture");
  if (btn.disabled) return;
  btn.disabled = true;                       // 더블클릭으로 중복 캡처가 쌓이던 것 차단
  const pose = currentPose;
  const mode = ($("#capture-mode") && $("#capture-mode").value) || "auto";
  const modeLabel = mode === "playwright" ? "브라우저 렌더" : mode === "static" ? "Static API 합성" : "자동";
  setStatus(`캡처 중… (${modeLabel})`, "", 0);
  let r;
  try {
    r = await api("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pose, mode }),
    });
  } catch (e) {
    setStatus("캡처 실패: " + e.message, "err", 7000);
    updateActionButtons();
    return;
  }
  if (r.status === "OK") {
    addToAlbum(r, originId);
    const via = r.mode === "playwright"
      ? (r.fallback_from ? "브라우저 렌더 — Static 미가용 자동 폴백" : "브라우저 렌더")
      : (r.composited ? "로드뷰+지도 합성" : "로드뷰만 — Pillow 미설치");
    if (r.substituted) {
      // 요청한 지점이 아닌 곳을 찍었다 — 반드시 알린다. 조용히 다른 거리를 분석하는 것이
      // 이 도구에서 가장 나쁜 실패다.
      setStatus(`⚠ 요청한 지점의 타일을 받지 못해(${r.substituted_reason || "검은 화면"}) `
        + `근처 구글 공식 지점으로 대체 촬영했습니다 — 분석 대상이 원래 위치와 다릅니다.`,
        "err", 12000);
    } else {
      setStatus("캡처 완료 (" + via + ")"
        + (r.third_party ? ` · 사용자 기여 파노 ${r.third_party}` : ""), "ok");
    }
    const fp = $("#fb-preview");
    if (fp && originId === activeId) fp.innerHTML = `<img class="big-img" src="${esc(r.url)}" alt="capture">`;
  } else if (r.status === "TILES_RATE_LIMITED") {
    // 검은 이미지를 앨범에 넣지 않는다 — 넣으면 그대로 분석 비용으로 나간다.
    setStatus("사용자 기여 파노라마의 타일 서버가 IP 단위로 제한(HTTP 429)을 걸었습니다. "
      + "잠시 뒤 다시 시도하면 정상적으로 나옵니다 — 이 지점이 촬영 불가한 것은 아닙니다.",
      "err", 12000);
  } else if (r.status === "BLACK_RENDER") {
    setStatus("로드뷰가 검게 렌더됐습니다. 잠시 후 다시 시도하거나 조금 이동해 보세요.", "err", 9000);
  } else {
    setStatus("캡처 실패: " + (r.message || r.status), "err", 7000);
  }
  updateActionButtons();
}

function addToAlbum(r, tabId) {
  const id = tabId != null ? tabId : activeId;
  const t = tabById(id);
  if (t) t.album.push({ file: r.file, url: r.url, sel: true });
  if (id === activeId) {
    if (t) album = t.album;
    renderAlbum();
  }
  renderTabbar();
}

function renderAlbum() {
  $("#album-card").hidden = album.length === 0;
  $("#album-count").textContent = album.length ? `(${album.length})` : "";
  const wrap = $("#album");
  wrap.innerHTML = "";
  album.forEach((a, i) => {
    const d = document.createElement("div");
    d.className = "thumb" + (a.sel ? " sel" : "");
    d.innerHTML = `<img src="${esc(a.url)}" alt="capture ${i + 1}"><span class="tick">✓</span>`;
    d.addEventListener("click", () => { a.sel = !a.sel; renderAlbum(); });
    wrap.appendChild(d);
  });
  updateActionButtons();
}

// ── 분석 (즉시 실행 — 큐를 거치지 않는 가벼운 단일 호출) ────────
async function doAnalyze() {
  const files = album.filter((a) => a.sel).map((a) => a.file);
  if (!files.length) return;
  const originId = activeId;
  if (tabBusy(originId)) { setStatus("이 탭은 이미 작업이 진행 중입니다", "err"); return; }
  const lang = primaryLang();
  setStatus(`분석 중… (${files.length}장 · ${langName(lang)})`, "", 0);
  $("#btn-analyze").disabled = true;
  let r;
  try {
    r = await api("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files, lang }),
    });
  } catch (e) {
    setStatus("분석 실패: " + e.message, "err", 7000);
    updateActionButtons();
    return;
  }
  if (r.status === "OK" && r.analysis) {
    const t = tabById(originId);
    if (t) { t.analysis = r; t.analysisFiles = files; }
    if (originId === activeId) {
      lastAnalysis = r; lastAnalysisFiles = files;
      renderAnalysis(r);
      setStatus("분석 완료", "ok");
    } else {
      renderTabbar();
      setStatus(`분석 완료 (${t ? tabTitle(t) : "닫힌"} 탭)`, "ok");
    }
  } else {
    setStatus("분석 실패: " + (r.message || r.status), "err", 7000);
  }
  updateActionButtons();
}

// ── 보고서 = 큐에 작업 등록 ────────────────────────────────────
function newClientKey() {
  return "ck_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);
}

async function submitJob(kind, payload, originId, label) {
  payload.tabId = originId;
  payload.clientKey = newClientKey();     // 중복 제출 방지(서버가 같은 키를 병합)
  payload.label = label;
  let r;
  try {
    r = await api(`/api/jobs/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    setStatus((e.status === 429 ? "대기열이 가득 찼습니다: " : "작업 등록 실패: ") + e.message, "err", 7000);
    return null;
  }
  queueStats = r.queue || queueStats;
  trackJob(r.job, originId);
  const pos = r.position || 0;
  setStatus(pos > 0
    ? `대기열에 등록 — 앞에 ${pos}건 (순차 처리)`
    : "작업 시작 — 진행 상황은 아래 '작업 큐'에서 볼 수 있습니다", "ok", 5000);
  renderQueue();
  renderTabbar();
  updateActionButtons();
  return r.job;
}

async function doQuickReport() {
  if (!currentPose || (currentPose.lat == null && !currentPose.pano)) {
    setStatus("먼저 링크를 추출하세요", "err"); return;
  }
  if (!CONFIG.hasAnthropic) { setStatus("분석에 ANTHROPIC_API_KEY 필요", "err", 5000); return; }
  const originId = activeId;
  if (tabBusy(originId)) { setStatus("이 탭은 이미 작업이 진행 중입니다", "err"); return; }
  await submitJob("scene-report", {
    pose: currentPose,
    mode: ($("#capture-mode") && $("#capture-mode").value) || "auto",
    langs: selectedLangs, research: researchOn(), start: startCoord(),
  }, originId, "바로 보고서 · " + (activeTab() ? tabTitle(activeTab()) : ""));
}

async function doAlbumReport() {
  const files = album.filter((a) => a.sel).map((a) => a.file);
  if (!files.length) { setStatus("먼저 장면을 선택하세요", "err"); return; }
  const originId = activeId;
  if (tabBusy(originId)) { setStatus("이 탭은 이미 작업이 진행 중입니다", "err"); return; }
  await submitJob("report", {
    files, langs: selectedLangs, research: researchOn(), start: startCoord(),
  }, originId, "선택 장면 보고서 · " + (activeTab() ? tabTitle(activeTab()) : ""));
}

async function doReport() {
  if (!lastAnalysis) { setStatus("먼저 분석하세요", "err"); return; }
  const originId = activeId;
  if (tabBusy(originId)) { setStatus("이 탭은 이미 작업이 진행 중입니다", "err"); return; }
  await submitJob("report", {
    files: lastAnalysisFiles, langs: selectedLangs, analysis: lastAnalysis,
    research: researchOn(), start: startCoord(),
  }, originId, "보고서 · " + (activeTab() ? tabTitle(activeTab()) : ""));
}

// ── 작업 추적 (SSE + 폴링 안전망) ──────────────────────────────
function trackJob(job, tabId) {
  const rec = jobs.get(job.id) || {};
  rec.job = job;
  rec.tabId = tabId != null ? tabId : rec.tabId;
  jobs.set(job.id, rec);
  if (!rec.es && (job.status === "QUEUED" || job.status === "RUNNING")) {
    try {
      const es = new EventSource(`/api/jobs/${job.id}/stream`);
      rec.es = es;
      es.onmessage = (ev) => {
        let d;
        try { d = JSON.parse(ev.data); } catch (e) { return; }
        if (d.job) rec.job = d.job;
        if (d.type === "done") {
          rec.job = d.job;
          onJobDone(rec);
          es.close(); rec.es = null;
        }
        renderQueue(); renderTabbar(); updateActionButtons();
      };
      es.onerror = () => { es.close(); rec.es = null; };   // 폴링이 이어받는다
    } catch (e) { /* EventSource 미지원 → 폴링만 */ }
  }
}

async function refreshJobs() {
  let d;
  try { d = await api("/api/jobs"); } catch (e) { return; }
  queueStats = d.queue || queueStats;
  for (const j of d.jobs || []) {
    const rec = jobs.get(j.id);
    if (!rec) {
      // 새로고침으로 잃어버린 작업도 서버에서 되찾는다.
      jobs.set(j.id, { job: j, tabId: j.tabId });
      if (j.status === "QUEUED" || j.status === "RUNNING") trackJob(j, j.tabId);
      continue;
    }
    const wasTerminal = rec.job && ["DONE", "FAILED", "CANCELED"].includes(rec.job.status);
    rec.job = j;
    if (!wasTerminal && ["DONE", "FAILED", "CANCELED"].includes(j.status)) onJobDone(rec);
  }
  renderQueue(); renderTabbar(); updateActionButtons();
}

async function onJobDone(rec) {
  if (rec._handled) return;
  rec._handled = true;
  let full;
  try { full = await api(`/api/jobs/${rec.job.id}`); } catch (e) { return; }
  rec.job = full.job;
  const result = full.job.result || {};
  const t = tabById(rec.tabId);

  if (full.job.status === "CANCELED") {
    setStatus("작업이 취소되었습니다", "err", 5000);
    renderQueue(); renderTabbar(); updateActionButtons();
    return;
  }
  if (full.job.status === "FAILED") {
    setStatus("작업 실패: " + (full.job.error || "알 수 없는 오류"), "err", 9000);
    renderQueue(); renderTabbar(); updateActionButtons();
    return;
  }

  // 캡처가 있었으면 원래 탭의 앨범에 넣는다
  if (result.capture && result.capture.status === "OK" && t) addToAlbum(result.capture, rec.tabId);

  if (result.primaryAnalysis && result.primaryAnalysis.analysis && t) {
    t.analysis = result.primaryAnalysis;
    t.analysisFiles = result.primaryAnalysis.images || t.analysisFiles;
    if (rec.tabId === activeId) {
      lastAnalysis = t.analysis; lastAnalysisFiles = t.analysisFiles;
      renderAnalysis(t.analysis);
    }
  }

  const reps = result.reports || [];
  if (reps.length) {
    if (t) {
      t.reports = t.reports || [];
      t.reports.unshift(...reps);
      if (rec.tabId === activeId) { reportsMade = t.reports; renderReports(); }
    } else {
      // 탭이 닫힌 뒤 도착 — 링크를 잃지 않도록 별도 보관하고 큐 카드에 남긴다.
      orphanReports.unshift(...reps);
    }
    const k = result.knowledge || {};
    const kmsg = k.created || k.merged || (k.recalled || []).length
      ? ` · 지식 +${k.created || 0}/~${k.merged || 0}${(k.recalled || []).length ? ` (재사용 ${k.recalled.length})` : ""}`
      : "";
    // 소요 시간은 이제 목표치(300초)가 있는 값이라 결과에 같이 보여준다.
    const tmsg = result.elapsed_s
      ? ` · ${Math.round(result.elapsed_s)}초${result.budget_s && result.elapsed_s > result.budget_s ? " (예산 초과)" : ""}`
      : "";
    setStatus(`보고서 ${reps.length}건 완료${t ? "" : " (닫힌 탭 — 아래 큐에서 열 수 있습니다)"}${tmsg}${kmsg}`, "ok", 9000);
    flashTab(rec.tabId);
  }
  renderQueue(); renderTabbar(); renderKnowledge(); updateActionButtons();
}

function flashTab(id) {
  const el = $(`#tabbar .tab[data-id="${id}"]`);
  if (!el) return;
  el.classList.add("done-flash");
  setTimeout(() => el.classList.remove("done-flash"), 2500);
}

async function cancelJob(id) {
  try { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); }
  catch (e) { setStatus("취소 실패: " + e.message, "err"); return; }
  setStatus("취소 요청됨", "ok");
  refreshJobs();
}

// ── 작업 큐 패널 ───────────────────────────────────────────────
const STAGE_LABEL = {
  start: "시작", capture: "캡처", captured: "캡처 완료", analyze: "분석",
  analyzed: "식별", recall: "지식 회상", research: "웹 리서치", researched: "리서치 완료",
  translate: "번역", render: "보고서 작성", knowledge: "지식 적재",
  canceling: "취소 중", done: "완료", failed: "실패", canceled: "취소됨",
};

function renderQueue() {
  const card = $("#queue-card");
  const list = $("#queue-list");
  if (!card || !list) return;
  const all = Array.from(jobs.values()).sort((a, b) => (b.job.createdAt || 0) - (a.job.createdAt || 0));
  const active = all.filter((r) => r.job.status === "QUEUED" || r.job.status === "RUNNING");
  card.hidden = all.length === 0;
  $("#queue-count").textContent = active.length
    ? `실행 ${queueStats.running || 0} · 대기 ${queueStats.queued || 0}`
    : `(${all.length})`;

  list.innerHTML = all.slice(0, 12).map((rec) => {
    const j = rec.job;
    const t = tabById(rec.tabId);
    const st = j.status;
    const cls = st === "RUNNING" ? "run" : st === "QUEUED" ? "wait"
      : st === "DONE" ? "ok" : st === "FAILED" ? "err" : "cancel";
    const stage = STAGE_LABEL[j.stage] || j.stage || "";
    const where = t ? esc(tabTitle(t)) : (rec.tabId != null ? "닫힌 탭" : "");
    const secs = j.elapsed != null ? `${Math.round(j.elapsed)}초` : "";
    const bar = (st === "RUNNING")
      ? `<div class="qbar"><i style="width:${Math.max(3, j.pct || 0)}%"></i></div>` : "";
    const reps = (j.result && j.result.reports) || [];
    const links = reps.map((rp) =>
      `<a class="qlink" href="${esc(rp.url)}" target="_blank" rel="noopener">${esc(rp.langName || rp.lang || "보고서")} ↗</a>`
    ).join(" ");
    const btn = (st === "QUEUED" || st === "RUNNING")
      ? `<button class="qcancel" data-cancel="${esc(j.id)}">취소</button>` : "";
    return `<div class="qrow ${cls}">
      <div class="qhead"><span class="qstatus">${st === "QUEUED" ? "대기" : st === "RUNNING" ? "진행" : st === "DONE" ? "완료" : st === "FAILED" ? "실패" : "취소"}</span>
        <span class="qlabel" title="${esc(j.label || "")}">${esc(j.label || j.kind)}</span>
        ${where ? `<span class="qtab">${where}</span>` : ""}${btn}</div>
      <div class="qmeta">${esc(stage)}${secs ? " · " + secs : ""}${j.error ? " · " + esc(String(j.error).slice(0, 80)) : ""}</div>
      ${bar}${links ? `<div class="qlinks">${links}</div>` : ""}
    </div>`;
  }).join("");

  list.querySelectorAll("[data-cancel]").forEach((b) =>
    b.addEventListener("click", () => cancelJob(b.dataset.cancel)));

  const orph = $("#queue-orphans");
  if (orph) {
    orph.hidden = orphanReports.length === 0;
    orph.innerHTML = orphanReports.length
      ? `<div class="qorph-h">닫힌 탭의 보고서</div>` + orphanReports.slice(0, 10).map((rp) =>
          `<a class="qlink" href="${esc(rp.url)}" target="_blank" rel="noopener">${esc(rp.file)} ↗</a>`).join(" ")
      : "";
  }
}

// ── 지식 저장소 패널 ───────────────────────────────────────────
async function renderKnowledge() {
  const card = $("#knowledge-card");
  if (!card) return;
  if (!CONFIG.knowledgeEnabled) { card.hidden = true; return; }
  let k;
  try { k = await api("/api/knowledge"); } catch (e) { return; }
  card.hidden = false;
  $("#k-stats").innerHTML =
    `<span class="chip">원자 ${k.atoms || 0}</span>` +
    `<span class="chip">엔티티 ${k.entities || 0}</span>` +
    `<span class="chip">태그 ${k.tags || 0}</span>` +
    `<span class="chip">보고서 ${k.reports || 0}</span>`;
  const btn = $("#btn-synth");
  if (btn) btn.disabled = (k.atoms || 0) < 2;
}

async function doSynthesize() {
  const sel = ($("#synth-input") && $("#synth-input").value || "").trim();
  if (!sel) { setStatus("종합할 엔티티나 태그를 입력하세요 (예: carolingian-empire)", "err", 6000); return; }
  const btn = $("#btn-synth");
  btn.disabled = true;
  setStatus("종합 보고서 작성 중… (저장된 지식만 사용 — 새 검색 없음)", "", 0);
  let r;
  try {
    r = await api("/api/knowledge/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selector: sel, langs: selectedLangs }),
    });
  } catch (e) {
    setStatus("종합 실패: " + e.message, "err", 8000);
    btn.disabled = false; return;
  }
  btn.disabled = false;
  if (r.status !== "OK") { setStatus("종합 실패: " + (r.message || r.status), "err", 8000); return; }
  const reps = r.reports || [];
  orphanReports.unshift(...reps);
  setStatus(`종합 보고서 ${reps.length}건 — 원자 ${(r.atoms || []).length}개, 장소 ${(r.places || []).length}곳 재사용`, "ok", 9000);
  renderQueue();
}

// ── 보고서 목록 ────────────────────────────────────────────────
function renderReports() {
  $("#reports-card").hidden = reportsMade.length === 0;
  $("#reports-count").textContent = reportsMade.length ? `(${reportsMade.length})` : "";
  $("#reports-list").innerHTML = reportsMade.map((rp) => {
    const badge = rp.combined ? `<span class="rl-multi" title="한 파일에 여러 언어 + 상단 스위처">통합</span>` : "";
    return `<div class="report-row"><span class="rl-lang">${esc(rp.langName || rp.lang || "")}</span>${badge}` +
      `<a href="${esc(rp.url)}" target="_blank" rel="noopener" title="docs/report/${esc(rp.file)}">${esc(rp.file)} ↗</a></div>`;
  }).join("");
}

// 판별 사슬 레벨 라벨 (level 은 영어 enum 으로 고정 — 표시만 번역)
const LEVEL_KO = {
  continent: "대륙", country: "국가", macro_region: "광역권", cultural_sphere: "문화·민족권",
  admin_region: "행정구역", metro_area: "도시권", district: "지구",
};

function renderAnalysis(r) {
  const a = r.analysis;
  const g = a.best_guess || {};
  const conf = Math.round((g.confidence || 0) * 100);
  const cf = a.coarse_filters || {};
  const cues = a.cues || [];
  const langs = a.languages_detected || [];
  const alts = (a.alternatives || [])
    .map((x) => `${x.country} ${Math.round((x.confidence || 0) * 100)}%`).join(" · ");

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

  // 판별 사슬 — "왜 이 나라인가"가 아니라 "거기서 어떻게 더 좁혔는가"
  const steps = (a.narrowing || []).filter((s) => s && typeof s === "object");
  if (steps.length) {
    html += `<div class="h4">어떻게 좁혔는가</div><ol class="narrow">` + steps.map((s) => {
      const cands = (s.candidates || []).filter(Boolean).slice(0, 8);
      const out = (s.ruled_out || []).filter(Boolean).slice(0, 8);
      const conf = typeof s.confidence_after === "number"
        ? `<span class="nconf">${Math.round(s.confidence_after * 100)}%</span>` : "";
      return `<li class="nstep${out.length ? "" : " weak"}">
        <div class="nhead"><span class="nlvl l-${esc(s.level || "")}">${esc(LEVEL_KO[s.level] || s.level || "")}</span>
          <span class="nq">${esc(s.question || "")}</span>${conf}</div>
        ${cands.length ? `<div class="nrow"><span class="nk">후보</span><span class="nv">${
          cands.map((c) => `<span class="ncand">${esc(c)}</span>`).join("")}</span></div>` : ""}
        <div class="nrow"><span class="nk">관찰</span><span class="nv">${esc(s.observation || "")}</span></div>
        ${s.discriminator ? `<div class="nrow"><span class="nk">왜 갈리나</span><span class="nv em">${esc(s.discriminator)}</span></div>` : ""}
        ${out.length
          ? `<div class="nrow"><span class="nk">배제</span><span class="nv">${
              out.map((c) => `<span class="nout">${esc(c)}</span>`).join("")}</span></div>`
          : `<div class="nrow nocut"><span class="nk">배제</span><span class="nv">이 단계는 이미지만으로 가를 수 없었음</span></div>`}
        <div class="nrow in"><span class="nk">남음</span><span class="nv"><b>${esc(s.conclusion || "")}</b></span></div>
      </li>`;
    }).join("") + `</ol>`;
  }

  if (cues.length) {
    html += `<div class="h4">원 관찰</div><table class="atbl">
      <tr><th>범주</th><th>관찰</th><th>가중</th></tr>` +
      cues.map((c) => `<tr>
        <td>${esc(c.category || "")}</td>
        <td>${esc(c.observation || "")}${c.supports && c.supports.length ? ` <span class="muted">[${esc(c.supports.join(","))}]</span>` : ""}</td>
        <td class="w-${esc((c.weight || "").toLowerCase())}">${esc(c.weight || "")}</td>
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
  const nimg = (r.images || []).length;
  html += `<div class="cost">${nimg}장 · ${esc(r.model || "")} · ~$${esc(r.cost_usd)}</div>`;

  $("#analysis").innerHTML = html;
  $("#analysis-lang").textContent = langName(r.lang || primaryLang());
  $("#analysis-card").hidden = false;
  $("#btn-report").hidden = false;
  renderTabbar();
  updateActionButtons();
}

// ── 유틸 ───────────────────────────────────────────────────────
function fmt(x) {
  if (x == null) return "—";
  return typeof x === "number" ? Math.round(x * 1e5) / 1e5 : x;
}
function short(s) { return s && s.length > 16 ? s.slice(0, 8) + "…" + s.slice(-4) : s; }
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
/** ms=0 이면 자동으로 사라지지 않는다.
 *  예전에는 기본 3초라, 2~5분짜리 보고서 생성 중에도 3초 뒤엔 아무 표시가 없어
 *  "멈춘 것 같다"는 인상을 줬다. 긴 작업의 진행은 '작업 큐' 카드가 담당한다. */
function setStatus(msg, kind = "", ms = 3000) {
  const el = $("#status");
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
  el.hidden = false;
  clearTimeout(statusTimer);
  if (ms) statusTimer = setTimeout(() => (el.hidden = true), ms);
}

boot();
