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
    current: { lat: pose.lat, lng: pose.lng, pano: pose.pano,
      heading: pose.heading, pitch: pose.pitch, fov: pose.fov,
      // 링크에 들어 있던 직접 이미지 URL(!6s). 사용자 기여 파노는 이 길로만
      // 안정적으로 받아진다(JS API 는 lh3 CDN 429 로 검게 나올 수 있다).
      photo_url: pose.photo_url || null },
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
    pano = new google.maps.StreetViewPanorama($("#pano"), {
      position: center, pov: { heading: pose.heading || 0, pitch: pose.pitch || 0 },
      zoom: 1, motionTracking: false, linksControl: true, addressControl: true,
      fullscreenControl: false,
    });
    map.setStreetView(pano);
    marker = new google.maps.Marker({ position: center, map });
    pano.addListener("position_changed", syncFromPano);
    pano.addListener("pov_changed", syncFromPano);
    svc = new google.maps.StreetViewService();
  } else if (p) {
    map.setCenter(p);
    marker.setPosition(p);
    pano.setPov({ heading: pose.heading || 0, pitch: pose.pitch || 0 });
  }

  if (pose.pano) {
    armWhenLoaded(myToken, pose.pano, tabId);
    pano.setPano(pose.pano);
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
    return true;
  };
  if (tryArm()) return;
  const lis = google.maps.event.addListener(pano, "pano_changed", () => {
    if (tryArm() || myToken !== loadToken) google.maps.event.removeListener(lis);
  });
  // 안전망: pano_changed 가 오지 않는 경우에도 "실제로 그 파노인지"는 반드시 확인한다.
  setTimeout(() => { if (myToken === loadToken && !syncArmed) tryArm(); }, 3000);
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
  const next = {
    lat: pos.lat(), lng: pos.lng(), pano: nextPano,
    heading: pov.heading, pitch: pov.pitch,
    fov: currentPose ? currentPose.fov : 90, zoom: pano.getZoom(),
    // 같은 파노 안에서 시점만 돌린 것이면 이미지 URL 이 그대로 유효하다
    // (시점은 URL 파라미터로 다시 겨눈다). 파노가 바뀌면 버린다.
    photo_url: (prev.pano && prev.pano === nextPano) ? (prev.photo_url || null) : null,
  };
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
