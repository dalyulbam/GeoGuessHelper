/* ALTAIYA 프론트엔드 — 전략 지도.
 * 데이터: 백엔드(8901) /api/atlas — 행위자·관계(전 엣지 원자 인용).
 * 렌더: SVG 월드좌표 viewport 변환 + 노드 역스케일(줌해도 마커·글자 크기 유지),
 *       선은 vector-effect: non-scaling-stroke.
 */
"use strict";

const API = (new URLSearchParams(location.search).get("api"))
  || `http://${location.hostname || "localhost"}:8901`;

const K = 8;                                     // 월드 스케일: 1도 = 8단위
const W = 360 * K, H = 180 * K;
const px = lng => (lng + 180) * K;
const py = lat => (90 - lat) * K;

const TYPE_ICON = { place:"📍", company:"🏭", org:"🏛", person:"👤",
                    infra:"🌉", polity:"👑", culture:"🗣", nature:"🏞" };
const TYPE_KO   = { place:"거점", company:"기업", org:"조직·단체", person:"인물",
                    infra:"인프라", polity:"정치체", culture:"문화권", nature:"자연" };
const TYPE_COLOR= { place:"#3ad0ae", company:"#f0b429", org:"#b28df5", person:"#f585b5",
                    infra:"#63b3ed", polity:"#e8c547", culture:"#cf9df0", nature:"#90dd85" };
const LAYER_KO  = { economy:"경제", history:"역사", culture:"문화", geography:"지리",
                    language:"언어", architecture:"건축", nature:"자연" };
const LAYER_COLOR={ economy:"#34d3a6", history:"#e8b34b", culture:"#c983e8", geography:"#5fb7dd",
                    language:"#ee7fa9", architecture:"#9d92f2", nature:"#8ed877" };
const SCOPE_KO  = { point:"지점", city:"도시", region:"지역", country:"국가", polity:"광역", period:"시대", global:"전역" };
const THEME_KO  = { stock:"주식 투자", realty:"부동산", trade:"수출입", corporate:"법인사업",
                    talent:"교육·인재", travel:"여행·문화", heritage:"역사·기록" };

const $ = s => document.querySelector(s);
const svg = $("#map"), viewport = $("#viewport"), tooltip = $("#tooltip");

const state = {
  data: null, meta: null,
  actors: new Map(),        // slug → actor
  nodeEl: new Map(),        // slug → {g, inner, dot, label}
  edgeEl: [],               // {el, e}
  ringEl: new Map(),        // slug → circle
  adj: new Map(),           // slug → Set(slug)
  edgeOf: new Map(),        // slug → [edge el]
  theme: "all",
  selected: null,
  view: { x: 0, y: 0, k: 1 },   // translate(x,y) scale(k)
  fitK: 1,
  themeMin: { actor: 0.5, edge: 0.4 },
  lodBucket: -1,                // 줌 밀도 버킷 (0 원경 · 1 중경 · 2 근경)
  lodHidden: new Set(),         // 현재 줌에서 밀도상 숨긴 행위자
  spheres: new Map(),           // key → 사료권(시대 층위를 가진다)
  sphere: null,                 // 시대 독이 지금 보고 있는 사료권
  era: null,                    // 고른 시대 층 key (없으면 전 시대)
  panelTab: "city",             // 도시 · 외교 · 연표
  panelData: null,
};

/* ── 데이터 로드 ─────────────────────────────────────────────── */
async function boot() {
  $("#loading").hidden = false;
  $("#apierror").hidden = true;
  try {
    // 해안선은 실패해도 좌초하지 않는다 — file:// 로 직접 열면 로컬 fetch 가 막히는데,
    // 그때도 격자·그래프는 그려져야 한다 (정상 사용은 run.bat → http://localhost:8902)
    const worldP = fetch("world.geo.json").then(r => (r.ok ? r.json() : null)).catch(() => null);
    const atlasRes = await fetch(`${API}/api/atlas`);
    if (!atlasRes.ok) throw new Error(`API ${atlasRes.status}`);
    const data = await atlasRes.json();
    const world = await worldP;
    state.data = data;
    state.meta = data.meta;
    state.themeMin.actor = data.meta.actor_theme_min ?? 0.5;
    state.themeMin.edge = data.meta.edge_theme_min ?? 0.4;
    for (const a of data.actors) state.actors.set(a.slug, a);
    for (const sp of data.spheres || []) state.spheres.set(sp.key, sp);

    if (world) drawWorld(world);
    drawGraticule();
    drawGraph(data);
    fitView();
    buildUI();
    $("#loading").hidden = true;
  } catch (err) {
    console.error(err);
    $("#loading").hidden = true;
    $("#apierror").hidden = false;
  }
}

/* ── 배경 지도 ───────────────────────────────────────────────── */
function drawWorld(geo) {
  const g = $("#g-world");
  const frag = document.createDocumentFragment();
  const ringPath = ring =>
    ring.map((c, i) => `${i ? "L" : "M"}${px(c[0]).toFixed(1)} ${py(c[1]).toFixed(1)}`).join("") + "Z";
  for (const f of geo.features) {
    const geom = f.geometry;
    if (!geom) continue;
    const polys = geom.type === "Polygon" ? [geom.coordinates]
                : geom.type === "MultiPolygon" ? geom.coordinates : [];
    let d = "";
    for (const poly of polys) for (const ring of poly) d += ringPath(ring);
    if (!d) continue;
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", d);
    p.setAttribute("class", "country");
    frag.appendChild(p);
  }
  g.appendChild(frag);
}

function drawGraticule() {
  const g = $("#g-grat");
  let d = "";
  for (let lng = -180; lng <= 180; lng += 30) d += `M${px(lng)} 0V${H}`;
  for (let lat = -60; lat <= 90; lat += 30) d += `M0 ${py(lat)}H${W}`;
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", d);
  p.setAttribute("class", "grat");
  g.appendChild(p);
}

/* ── 그래프 렌더 ─────────────────────────────────────────────── */
function drawGraph(data) {
  const NS = "http://www.w3.org/2000/svg";
  const gE = $("#g-edges"), gN = $("#g-nodes"), gI = $("#g-influence");

  for (const e of data.edges) {
    const a = state.actors.get(e.src), b = state.actors.get(e.dst);
    if (!a || !b) continue;
    const x1 = px(a.lng), y1 = py(a.lat), x2 = px(b.lng), y2 = py(b.lat);
    const dx = x2 - x1, dy = y2 - y1, dist = Math.hypot(dx, dy) || 1;
    const off = Math.min(dist * 0.16, 26);          // 항로 느낌의 곡률
    const mx = (x1 + x2) / 2 - (dy / dist) * off;
    const my = (y1 + y2) / 2 + (dx / dist) * off;
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", `M${x1.toFixed(1)} ${y1.toFixed(1)}Q${mx.toFixed(1)} ${my.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`);
    p.setAttribute("class", `edge ${e.layer}`);
    p.setAttribute("stroke-width", (0.8 + Math.min(e.strength, 4) * 0.5).toFixed(1));
    p.addEventListener("pointerenter", ev => edgeHover(e, p, ev));
    p.addEventListener("pointermove", ev => moveTooltip(ev));
    p.addEventListener("pointerleave", clearHover);
    gE.appendChild(p);
    state.edgeEl.push({ el: p, e });
    if (!state.adj.has(e.src)) state.adj.set(e.src, new Set());
    if (!state.adj.has(e.dst)) state.adj.set(e.dst, new Set());
    state.adj.get(e.src).add(e.dst);
    state.adj.get(e.dst).add(e.src);
    for (const s of [e.src, e.dst]) {
      if (!state.edgeOf.has(s)) state.edgeOf.set(s, []);
      state.edgeOf.get(s).push(p);
    }
  }

  for (const a of data.actors) {
    // 영향권 — 국가·광역 행위자만 (지역 이하는 마커로 충분)
    if ((a.scope === "country" || a.scope === "polity") && a.influence_km > 0) {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", px(a.lng));
      c.setAttribute("cy", py(a.lat));
      c.setAttribute("r", (a.influence_km / 111.32) * K * 0.5);   // 과장 방지로 절반만
      c.setAttribute("class", "ring");
      c.setAttribute("stroke", TYPE_COLOR[a.type] || "#3ad0ae");
      gI.appendChild(c);
      state.ringEl.set(a.slug, c);
    }

    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "node");
    g.setAttribute("transform", `translate(${px(a.lng)} ${py(a.lat)})`);
    const inner = document.createElementNS(NS, "g");
    const r = 5 + Math.sqrt(a.degree + 1) * 2.4;
    const dot = document.createElementNS(NS, "circle");
    dot.setAttribute("class", "dot");
    dot.setAttribute("r", r);
    dot.setAttribute("stroke", TYPE_COLOR[a.type] || "#3ad0ae");
    const ic = document.createElementNS(NS, "text");
    ic.setAttribute("class", "ic");
    ic.setAttribute("font-size", Math.max(9, r * 1.05));
    ic.textContent = TYPE_ICON[a.type] || "📍";
    const lb = document.createElementNS(NS, "text");
    lb.setAttribute("class", "lb");
    lb.setAttribute("x", r + 5);
    lb.setAttribute("y", 4);
    lb.textContent = a.label;
    inner.append(dot, ic, lb);
    g.appendChild(inner);
    g.addEventListener("pointerenter", ev => nodeHover(a, ev));
    g.addEventListener("pointermove", ev => moveTooltip(ev));
    g.addEventListener("pointerleave", clearHover);
    g.addEventListener("click", ev => { ev.stopPropagation(); select(a.slug, { fly: false }); });
    gN.appendChild(g);
    state.nodeEl.set(a.slug, { g, inner, dot, lb, a });
  }
}

/* ── 뷰(팬·줌) ───────────────────────────────────────────────── */
function viewSize() {
  const r = svg.getBoundingClientRect();
  return { w: r.width, h: r.height };
}

function applyView() {
  const { x, y, k } = state.view;
  viewport.setAttribute("transform", `translate(${x} ${y}) scale(${k})`);
  const inv = 1 / k;
  const rel = k / state.fitK;
  const showAll = rel > 7, showMid = rel > 3;
  for (const { g, inner, a } of state.nodeEl.values()) {
    inner.setAttribute("transform", `scale(${inv})`);
    const show = a.degree >= 5 || showAll || (showMid && a.degree >= 2) || state.selected === a.slug;
    g.classList.toggle("show-label", show);
  }
  // 줌 밀도(LOD) — 원경에서 마이너 행위자를 걷어 아드리아 같은 밀집 지대가 뭉개지지 않게
  const bucket = rel > 6 ? 2 : rel > 2.2 ? 1 : 0;
  if (bucket !== state.lodBucket) { state.lodBucket = bucket; applyLod(); }
}

function applyLod() {
  const b = state.lodBucket;
  state.lodHidden.clear();
  for (const { g, a } of state.nodeEl.values()) {
    const hide = state.selected !== a.slug
      && ((b === 0 && a.degree < 2 && a.atom_count < 3)
        || (b === 1 && a.degree < 1 && a.atom_count < 2));
    if (hide) state.lodHidden.add(a.slug);
    g.classList.toggle("lod", hide);
    state.ringEl.get(a.slug)?.classList.toggle("lod", hide);
  }
  syncEdges();
}

function syncEdges() {
  for (const ent of state.edgeEl) {
    const on = ent.themeOn !== false
      && !state.lodHidden.has(ent.e.src) && !state.lodHidden.has(ent.e.dst);
    ent.el.classList.toggle("off", !on);
  }
}

function fitView() {
  const { w, h } = viewSize();
  let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  for (const a of state.actors.values()) {
    minX = Math.min(minX, px(a.lng)); maxX = Math.max(maxX, px(a.lng));
    minY = Math.min(minY, py(a.lat)); maxY = Math.max(maxY, py(a.lat));
  }
  const pad = 60;
  minX -= pad; minY -= pad; maxX += pad; maxY += pad;
  const k = Math.min(w / (maxX - minX), h / (maxY - minY));
  state.fitK = k;
  state.view = { k, x: (w - (minX + maxX) * k) / 2, y: (h - (minY + maxY) * k) / 2 };
  applyView();
}

function zoomAt(cx, cy, factor) {
  const v = state.view;
  const k2 = Math.max(state.fitK * 0.5, Math.min(600, v.k * factor));
  const f = k2 / v.k;
  state.view = { k: k2, x: cx - (cx - v.x) * f, y: cy - (cy - v.y) * f };
  applyView();
}

let flyReq = 0;
function flyTo(a, targetK) {
  const { w, h } = viewSize();
  const v0 = { ...state.view };
  const k1 = targetK ?? Math.max(v0.k, state.fitK * 9);
  const pw = Math.min(400, w * 0.92);         // 상세 패널이 가리는 폭 — 남는 영역 중앙에 놓는다
  const x1 = (w - pw) / 2 - px(a.lng) * k1;
  const y1 = h / 2 - py(a.lat) * k1;
  const t0 = performance.now(), dur = 450, my = ++flyReq;
  const ease = t => 1 - Math.pow(1 - t, 3);
  (function step(now) {
    if (my !== flyReq) return;
    const t = ease(Math.min(1, (now - t0) / dur));
    state.view = { k: v0.k + (k1 - v0.k) * t, x: v0.x + (x1 - v0.x) * t, y: v0.y + (y1 - v0.y) * t };
    applyView();
    if (t < 1) requestAnimationFrame(step);
  })(t0);
}

function bindMapControls() {
  svg.addEventListener("wheel", ev => {
    ev.preventDefault();
    const r = svg.getBoundingClientRect();
    zoomAt(ev.clientX - r.left, ev.clientY - r.top, Math.exp(-ev.deltaY * 0.0016));
  }, { passive: false });

  let drag = null;
  svg.addEventListener("pointerdown", ev => {
    if (ev.button !== 0) return;
    drag = { pid: ev.pointerId, x: ev.clientX, y: ev.clientY, vx: state.view.x, vy: state.view.y, moved: false };
    // capture 는 드래그로 판정된 뒤에 건다 — 즉시 걸면 이후 click 이 svg 로
    // 재타깃되어(공통 조상 규칙) 노드 클릭이 죽는다.
  });
  svg.addEventListener("pointermove", ev => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 4) {
      drag.moved = true;
      svg.classList.add("dragging");
      try { svg.setPointerCapture(drag.pid); } catch { /* 이미 뗀 포인터 */ }
    }
    if (drag.moved) {
      state.view.x = drag.vx + dx;
      state.view.y = drag.vy + dy;
      applyView();
    }
  });
  svg.addEventListener("pointerup", ev => {
    const wasDrag = drag && drag.moved;
    drag = null;
    svg.classList.remove("dragging");
    if (!wasDrag && (ev.target === svg || ev.target.classList.contains("country") || ev.target.classList.contains("grat")))
      deselect();
  });
  svg.addEventListener("pointercancel", () => { drag = null; svg.classList.remove("dragging"); });

  $("#z-in").addEventListener("click", () => { const { w, h } = viewSize(); zoomAt(w / 2, h / 2, 1.5); });
  $("#z-out").addEventListener("click", () => { const { w, h } = viewSize(); zoomAt(w / 2, h / 2, 1 / 1.5); });
  $("#z-fit").addEventListener("click", fitView);
  window.addEventListener("resize", applyView);
  window.addEventListener("keydown", ev => {
    if (ev.key !== "Escape") return;
    if (!$("#codex").hidden) { $("#codex").hidden = true; return; }
    deselect(); hideSearch();
  });
}

/* ── 테마 필터 ───────────────────────────────────────────────── */
function actorVisible(a, th) {
  return th === "all" || (a.themes[th] ?? 0) >= state.themeMin.actor;
}

/* 시대 필터 — 그 층에 인용 원자가 실제로 앉아 있는 행위자·관계만 남긴다.
   층위는 사료권마다 다르므로, 다른 사료권의 행위자는 이 렌즈에서 빠진다. */
function eraOk(x) {
  if (!state.era) return true;
  return ((x.eras || {})[state.era] || 0) > 0;
}

function applyTheme(th) {
  state.theme = th;
  document.querySelectorAll(".theme-chip").forEach(b =>
    b.classList.toggle("active", b.dataset.theme === th));
  const vis = new Set();
  for (const a of state.actors.values()) if (actorVisible(a, th) && eraOk(a)) vis.add(a.slug);
  for (const { g, a } of state.nodeEl.values()) g.classList.toggle("theme-off", !vis.has(a.slug));
  for (const [slug, c] of state.ringEl) c.classList.toggle("theme-off", !vis.has(slug));
  for (const ent of state.edgeEl) {
    ent.themeOn = vis.has(ent.e.src) && vis.has(ent.e.dst) && eraOk(ent.e)
      && (th === "all" || (ent.e.themes[th] ?? 0) >= state.themeMin.edge);
  }
  syncEdges();
}

/* ── 호버 강조 + 툴팁 ────────────────────────────────────────── */
function litEgo(slug) {
  svg.classList.add("dimmed");
  const me = state.nodeEl.get(slug);
  if (me) me.g.classList.add("lit");
  for (const n of state.adj.get(slug) || []) state.nodeEl.get(n)?.g.classList.add("lit");
  for (const el of state.edgeOf.get(slug) || []) el.classList.add("lit");
  state.ringEl.get(slug)?.classList.add("lit");
}

function clearHover() {
  svg.classList.remove("dimmed");
  document.querySelectorAll(".lit").forEach(el => el.classList.remove("lit"));
  tooltip.hidden = true;
}

function nodeHover(a, ev) {
  litEgo(a.slug);
  const themes = Object.entries(a.themes).filter(([, v]) => v >= state.themeMin.actor)
    .sort((x, y) => y[1] - x[1]).slice(0, 3);
  tooltip.innerHTML =
    `<div class="tt-title">${TYPE_ICON[a.type]} ${esc(a.label)}</div>
     <div class="tt-sub">${TYPE_KO[a.type]} · ${SCOPE_KO[a.scope] || a.scope} · 원자 ${a.atom_count} · 연결 ${a.degree}</div>
     ${themes.length ? `<div class="tt-themes">${themes.map(([t]) => `<span>${THEME_KO[t]}</span>`).join("")}</div>` : ""}`;
  tooltip.hidden = false;
  moveTooltip(ev);
}

function edgeHover(e, el, ev) {
  svg.classList.add("dimmed");
  el.classList.add("lit");
  state.nodeEl.get(e.src)?.g.classList.add("lit");
  state.nodeEl.get(e.dst)?.g.classList.add("lit");
  const a = state.actors.get(e.src), b = state.actors.get(e.dst);
  const arrow = e.dir === "<->" ? "↔" : "→";
  const per = e.period && e.period[0] != null
    ? ` · ${e.period[0]}–${e.period[1] ?? ""}` : "";
  tooltip.innerHTML =
    `<div class="tt-title">${esc(a?.label || e.src)} ${arrow} ${esc(b?.label || e.dst)}</div>
     <div class="tt-sub"><b>${esc(e.rel_ko || "관련")}</b>${per} ·
     ${LAYER_KO[e.layer] || e.layer} 원자 ${e.strength}건이 인용 —
     행위자를 클릭해 원문 확인</div>`;
  tooltip.hidden = false;
  moveTooltip(ev);
}

function moveTooltip(ev) {
  if (tooltip.hidden) return;
  const r = $("#stage").getBoundingClientRect();
  const x = Math.min(ev.clientX - r.left + 16, r.width - 300);
  const y = Math.min(ev.clientY - r.top + 14, r.height - 120);
  tooltip.style.left = `${Math.max(6, x)}px`;
  tooltip.style.top = `${Math.max(6, y)}px`;
}

/* ── 선택 + 상세 패널 ────────────────────────────────────────── */
async function select(slug, { fly = true } = {}) {
  const a = state.actors.get(slug);
  if (!a) return;
  if (state.selected) state.nodeEl.get(state.selected)?.g.classList.remove("selected");
  state.selected = slug;
  state.nodeEl.get(slug)?.g.classList.add("selected");
  if (fly) flyTo(a);
  else ensureVisible(a);
  applyView();
  applyLod();                    // 선택된 행위자는 밀도 컷에서 제외

  if (a.sphere && a.sphere !== state.sphere) setSphere(a.sphere, { keepEra: false });

  const body = $("#panel-body");
  body.innerHTML = `<div class="p-head"><div class="p-ic">${TYPE_ICON[a.type]}</div>
    <div><div class="p-title">${esc(a.label)}</div></div></div>
    <div class="p-empty">불러오는 중…</div>`;
  $("#panel").classList.add("open");
  document.body.classList.add("panel-open");

  let d;
  try {
    const res = await fetch(`${API}/api/actor/${encodeURIComponent(slug)}`);
    if (!res.ok) throw new Error(res.status);
    d = await res.json();
  } catch {
    body.innerHTML += `<div class="p-empty">상세 정보를 불러오지 못했습니다.</div>`;
    return;
  }
  if (state.selected !== slug) return;    // 그새 다른 걸 골랐다
  renderPanel(d);
}

function deselect() {
  document.body.classList.remove("panel-open");
  if (state.selected) state.nodeEl.get(state.selected)?.g.classList.remove("selected");
  state.selected = null;
  $("#panel").classList.remove("open");
  applyView();
  applyLod();
}

function ensureVisible(a) {
  // 패널이 가리거나 화면 밖이면 현재 줌 그대로 시야만 옮긴다
  const { w, h } = viewSize();
  const pw = Math.min(400, w * 0.92);
  const sx = px(a.lng) * state.view.k + state.view.x;
  const sy = py(a.lat) * state.view.k + state.view.y;
  if (sx < 70 || sx > w - pw - 70 || sy < 70 || sy > h - 70) flyTo(a, state.view.k);
}

function renderPanel(d) {
  state.panelData = d;
  drawPanel();
}

/* 패널은 세 화면이다 — 도시(거점 살림) · 외교(관계) · 연표(시대 층위).
   시대는 사료권마다 눈금이 달라서, 연표 탭은 그 행위자가 속한 사료권의 층위를 쓴다. */
function drawPanel() {
  const d = state.panelData;
  if (!d) return;
  const { actor: a, relations, atoms, sphere } = d;
  const tab = state.panelTab;
  const period = a.period ? `${a.period[0] ?? "?"} – ${a.period[1] ?? "현재"}` : null;

  const body =
    tab === "diplo" ? diploView(a, relations)
    : tab === "time" ? timelineView(a, atoms, sphere)
    : cityView(a, relations, atoms);

  $("#panel-body").innerHTML = `
    <div class="p-head"><div class="p-ic">${TYPE_ICON[a.type]}</div>
      <div><div class="p-title">${esc(a.label)}</div>
      ${a.label_en && a.label_en !== a.label ? `<div class="p-sub">${esc(a.label_en)}</div>` : ""}</div></div>
    <div class="p-meta">
      <span class="p-chip hl">${TYPE_KO[a.type]}</span>
      <span class="p-chip">${SCOPE_KO[a.scope] || a.scope}</span>
      ${period ? `<span class="p-chip">${period}</span>` : ""}
      <span class="p-chip">원자 ${a.atom_count}</span>
      <span class="p-chip">연결 ${a.degree}</span>
      ${sphere ? `<span class="p-chip">${esc(sphere.label_ko)}</span>` : ""}
    </div>
    <div class="p-tabs">
      <button class="p-tab${tab === "city" ? " on" : ""}" data-tab="city">도시</button>
      <button class="p-tab${tab === "diplo" ? " on" : ""}" data-tab="diplo">외교 ${relations.length}</button>
      <button class="p-tab${tab === "time" ? " on" : ""}" data-tab="time">연표</button>
    </div>
    ${body}`;

  document.querySelectorAll("#panel .p-tab").forEach(b =>
    b.addEventListener("click", () => { state.panelTab = b.dataset.tab; drawPanel(); }));
  document.querySelectorAll("#panel .rel-row").forEach(row =>
    row.addEventListener("click", () => select(row.dataset.slug)));
  document.querySelectorAll("#panel .atom-head").forEach(h =>
    h.addEventListener("click", () => h.parentElement.classList.toggle("open")));
  document.querySelectorAll("#panel .tl-atom").forEach(el =>
    el.addEventListener("click", () => { state.panelTab = "city"; drawPanel();
      const t = document.querySelector(`#panel .atom-item[data-id="${el.dataset.id}"]`);
      if (t) { t.classList.add("open"); t.scrollIntoView({ block: "center", behavior: "smooth" }); } }));
}

/* 도시 화면 — 테마 신호 · 원자 원문 · 근거 보고서 */
function cityView(a, relations, atoms) {
  const maxTheme = Math.max(1, ...Object.values(a.themes));
  const themeBars = Object.entries(a.themes).sort((x, y) => y[1] - x[1])
    .map(([t, v]) => `<div class="tbar"><span>${THEME_KO[t]}</span>
      <div class="bar"><i style="width:${Math.min(100, (v / maxTheme) * 100)}%"></i></div>
      <span class="v">${v.toFixed(1)}</span></div>`).join("");

  const atomItems = atoms.map(at => {
    const reps = (at.reports || []).map(n =>
      `<a href="${API}/reports/${encodeURIComponent(n)}" target="_blank" rel="noopener">보고서 열기 ↗</a>`).join("");
    const per = at.period && at.period[0] != null ? ` · ${at.period[0]}${at.period[1] ? `–${at.period[1]}` : "–"}` : "";
    const era = at.era && at.era.era !== "atemporal" ? `<span class="atom-era">${esc(at.era.label)}</span>` : "";
    return `<div class="atom-item" data-id="${esc(at.id)}">
      <div class="atom-head"><span class="lay" style="background:${LAYER_COLOR[at.layer] || "#888"}"></span>
        <span class="t">${esc(at.title || at.id)}</span>${era}<span class="chev">▾</span></div>
      <div class="atom-body">${esc(at.body || "")}
        <div class="atom-links">${reps}</div>
        <div class="atom-src">${LAYER_KO[at.layer] || at.layer}${per} · 출처 ${at.sources.length}곳 · <code>${at.id}</code></div>
      </div></div>`;
  }).join("");

  const reports = (a.reports || []).map(n =>
    `<a class="rep-link" href="${API}/reports/${encodeURIComponent(n)}" target="_blank" rel="noopener">📄 ${esc(n)}</a>`).join("");

  return `<div class="p-sec"><h4>테마 신호</h4>${themeBars}</div>
    <div class="p-sec"><h4>지식 원자 (${atoms.length})</h4>${atomItems}</div>
    ${reports ? `<div class="p-sec"><h4>근거 보고서</h4>${reports}</div>` : ""}`;
}

/* 외교 화면 — 관계를 유형별로 묶는다. 전 관계가 인용 원자를 갖는다. */
function diploView(a, relations) {
  if (!relations.length)
    return `<div class="p-empty">아직 기록된 관계가 없다 — 이 행위자를 다룬 보고서가 늘면 선이 생긴다.</div>`;
  const groups = new Map();
  for (const r of relations) {
    if (!groups.has(r.rel)) groups.set(r.rel, []);
    groups.get(r.rel).push(r);
  }
  const order = ["ruled", "opposed", "founded", "succeeded", "operates", "supplies",
                 "produces", "connects", "part-of", "related", "comention"];
  const html = [...groups.entries()]
    .sort((x, y) => order.indexOf(x[0]) - order.indexOf(y[0]))
    .map(([rel, rows]) => `<div class="rel-group">
      <div class="rel-gh">${esc(rows[0].rel_ko || rel)} · ${rows.length}</div>
      ${rows.map(r => relRow(a, r)).join("")}</div>`).join("");
  return `<div class="p-sec">${html}
    <div class="lg-note" style="margin-top:6px">관계는 원자 본문이 명시적으로 서술한 것만이다.
      선이 없다고 관계가 없는 것은 아니다 — 아직 근거가 없을 뿐이다.</div></div>`;
}

function relRow(a, r) {
  const arrow = r.dir === "<->" ? "↔" : (r.src === a.slug ? "→" : "←");
  const per = r.period && r.period[0] != null
    ? `<span class="rel-per">${r.period[0]}–${r.period[1] ?? ""}</span>` : "";
  return `<div class="rel-row" data-slug="${esc(r.other)}">
     <span class="rel-dot" style="background:${LAYER_COLOR[r.layer] || "#3ad0ae"}"></span>
     <span class="rel-kind">${esc(r.rel_ko || "관련")}${arrow}</span>
     <span class="rel-name">${TYPE_ICON[r.other_type] || ""} ${esc(r.other_label)}</span>
     ${per}<span class="rel-cite">원자 ${r.atoms.length}</span></div>`;
}

/* 연표 화면 — 이 행위자의 원자를 제 사료권의 층위에 얹는다 */
function timelineView(a, atoms, sphere) {
  if (!sphere)
    return `<div class="p-empty">이 행위자의 원자는 아직 어느 사료권에도 배정되지 않았다.</div>`;
  const byEra = new Map();
  for (const at of atoms) {
    const k = at.era ? at.era.era : "unassigned";
    if (!byEra.has(k)) byEra.set(k, []);
    byEra.get(k).push(at);
  }
  const rows = sphere.strata.map(st => {
    const mine = byEra.get(st.key) || [];
    if (!mine.length) return "";
    const alt = (st.also_called || []).length
      ? `<span class="eb-alt">병기: ${esc((st.also_called || []).join(" · "))}</span>` : "";
    return `<div class="tl-era">
      <div class="tl-head"><span class="tl-span">${st.start}–${st.end ?? ""}</span>
        <span class="tl-name">${esc(st.label_ko)}</span></div>
      <div class="tl-basis">${esc(st.basis)}${alt}</div>
      ${mine.map(at => `<div class="tl-atom" data-id="${esc(at.id)}">${esc(at.title || at.id)}</div>`).join("")}
    </div>`;
  }).join("");
  const rest = (byEra.get("atemporal") || []).concat(byEra.get("unassigned") || []);
  return `<div class="p-sec">
    <h4>${esc(sphere.label_ko)}</h4>
    <div class="tl-basis"><b>기준</b> ${esc(sphere.frame)}<br><b>한계</b> ${esc(sphere.caution)}</div>
    ${rows || `<div class="p-empty">이 사료권 층위에 얹힌 원자가 없다.</div>`}
    ${rest.length ? `<div class="tl-era"><div class="tl-head"><span class="tl-name">시대에 매이지 않음</span></div>
      ${rest.map(at => `<div class="tl-atom" data-id="${esc(at.id)}">${esc(at.title || at.id)}</div>`).join("")}</div>` : ""}
  </div>`;
}

/* ── 시대 독 — 사료권마다 제 눈금 ─────────────────────────────
   전 세계 공통 연표를 하나 놓고 자르지 않는다. 각 지역의 역사서술이 실제로 쓰는
   구획(통치체 교체·교역 구조·물질 조건)을 그 사료권의 층위로 싣고, 지도는 그 층위로만
   잘린다. 대표 라벨은 중립 서술이고, 한쪽의 명칭은 '병기'로 내린다. */
function buildEraDock() {
  if (!state.spheres.size) return;
  const bar = $("#erabar"), sel = $("#eb-sphere");
  bar.hidden = false;
  document.body.classList.add("has-dock");
  sel.innerHTML = [...state.spheres.values()]
    .sort((a, b) => b.atom_count - a.atom_count)
    .map(sp => `<option value="${esc(sp.key)}">${esc(sp.label_ko)} · 원자 ${sp.atom_count}</option>`).join("");
  sel.addEventListener("change", () => setSphere(sel.value));
  $("#eb-all").addEventListener("click", () => setEra(null));
  $("#eb-note").addEventListener("click", () => {
    const f = $("#eb-frame");
    f.hidden = !f.hidden;
    $("#eb-note").classList.toggle("on", !f.hidden);
  });
  setSphere([...state.spheres.values()].sort((a, b) => b.atom_count - a.atom_count)[0].key);
}

function setSphere(key, { keepEra = false } = {}) {
  if (!state.spheres.has(key)) return;
  state.sphere = key;
  if (!keepEra) state.era = null;
  $("#eb-sphere").value = key;
  const sp = state.spheres.get(key);
  $("#eb-frame").innerHTML =
    `<b>기준</b> ${esc(sp.frame)}<br><b>한계</b> ${esc(sp.caution)}<br>
     <b>주의</b> 이 층위는 이 사료권에서만 쓰는 눈금이다. 다른 사료권과 연도를 나란히 놓아도
     같은 시대를 뜻하지 않는다.`;
  renderEraStrip();
  applyTheme(state.theme);
}

function renderEraStrip() {
  const sp = state.spheres.get(state.sphere);
  const strip = $("#eb-strip");
  if (!sp) { strip.innerHTML = ""; return; }
  const count = era => {
    let n = 0;
    for (const a of state.actors.values()) n += (a.eras || {})[era] || 0;
    return n;
  };
  strip.innerHTML = sp.strata.map(st => {
    const n = count(st.key);
    const alt = (st.also_called || []).length
      ? `<span class="eb-alt">병기 ${esc(st.also_called.join(" · "))}</span>` : "";
    return `<button class="eb-era${state.era === st.key ? " on" : ""}${n ? "" : " empty"}"
      data-era="${esc(st.key)}" title="${esc(st.basis)}">
      <span class="eb-cnt">${n}</span>
      <span class="eb-span">${st.start}–${st.end ?? ""}</span>
      <span class="eb-name">${esc(st.label_ko)}</span>${alt}</button>`;
  }).join("");
  strip.querySelectorAll(".eb-era").forEach(b =>
    b.addEventListener("click", () => setEra(state.era === b.dataset.era ? null : b.dataset.era)));
  $("#eb-all").classList.toggle("on", !state.era);
}

function setEra(era) {
  state.era = era;
  renderEraStrip();
  applyTheme(state.theme);
}

/* ── 시빌로피디아 — 사료권 → 행위자 색인 ─────────────────────
   지식 저장소의 엔티티 노트(.md)는 knowledge.py 가 보고서마다 통째로 다시 쓰므로
   거기에 관계 절을 끼워 넣으면 다음 실행에 지워진다. 그래서 백과는 파일을 건드리지 않고
   여기서 조립한다 — 원자·관계·보고서는 모두 API 에서 온다. */
function buildCodex() {
  const box = $("#codex"), q = $("#cx-q");
  $("#codex-open").addEventListener("click", () => { box.hidden = false; q.value = ""; renderCodex(""); q.focus(); });
  $("#cx-close").addEventListener("click", () => { box.hidden = true; });
  box.addEventListener("click", ev => { if (ev.target === box) box.hidden = true; });
  q.addEventListener("input", () => renderCodex(q.value.trim().toLowerCase()));
}

function renderCodex(query) {
  const bySphere = new Map();
  const loose = [];
  for (const a of state.actors.values()) {
    if (query && !(a.slug.includes(query) || a.label.toLowerCase().includes(query)
      || (a.label_en || "").toLowerCase().includes(query))) continue;
    if (a.sphere && state.spheres.has(a.sphere)) {
      if (!bySphere.has(a.sphere)) bySphere.set(a.sphere, []);
      bySphere.get(a.sphere).push(a);
    } else loose.push(a);
  }
  const card = a => `<div class="cx-a" data-slug="${esc(a.slug)}">
      <span>${TYPE_ICON[a.type]}</span><span class="lb">${esc(a.label)}</span>
      <span class="mt">${a.atom_count}·${a.degree}</span></div>`;
  const blocks = [...bySphere.entries()]
    .sort((x, y) => y[1].length - x[1].length)
    .map(([key, list]) => {
      const sp = state.spheres.get(key);
      const strata = sp.strata.map(st => `${st.start}–${st.end ?? ""} ${st.label_ko}`).join("  ·  ");
      return `<div class="cx-sph">
        <div class="cx-sh"><b>${esc(sp.label_ko)}</b><span>행위자 ${list.length} · 원자 ${sp.atom_count}</span></div>
        <div class="cx-strata">${esc(strata)}</div>
        <div class="cx-grid">${list.sort((a, b) => b.degree - a.degree).map(card).join("")}</div></div>`;
    }).join("");
  const rest = loose.length ? `<div class="cx-sph">
      <div class="cx-sh"><b>사료권 미배정</b><span>행위자 ${loose.length}</span></div>
      <div class="cx-grid">${loose.sort((a, b) => b.degree - a.degree).map(card).join("")}</div></div>` : "";
  $("#cx-body").innerHTML = (blocks + rest) || `<div class="p-empty">일치하는 행위자가 없다.</div>`;
  $("#cx-body").querySelectorAll(".cx-a").forEach(el =>
    el.addEventListener("click", () => { $("#codex").hidden = true; select(el.dataset.slug); }));
}

/* ── UI(테마 칩·검색·통계) ───────────────────────────────────── */
function buildUI() {
  const m = state.meta;
  $("#stats").innerHTML =
    `<span class="st">행위자 <b>${m.actor_total}</b></span>
     <span class="st">관계 <b>${m.edge_total}</b></span>
     <span class="st">원자 <b>${m.atom_total}</b></span>`;

  document.querySelectorAll(".theme-chip").forEach(b => {
    const th = b.dataset.theme;
    if (th !== "all") {
      let n = 0;
      for (const a of state.actors.values()) if (actorVisible(a, th)) n++;
      b.insertAdjacentHTML("beforeend", `<span class="cnt">${n}</span>`);
    }
    b.addEventListener("click", () => applyTheme(th));
  });

  const input = $("#search"), drop = $("#search-drop");
  const all = [...state.actors.values()].sort((a, b) => b.degree - a.degree);
  let hot = -1, items = [];
  function renderDrop(q) {
    items = q
      ? all.filter(a => a.slug.includes(q) || a.label.toLowerCase().includes(q)).slice(0, 12)
      : all.slice(0, 12);
    hot = items.length ? 0 : -1;
    drop.innerHTML = items.map((a, i) =>
      `<div class="sd-item${i === 0 ? " hot" : ""}" data-slug="${esc(a.slug)}">
         <span class="ic">${TYPE_ICON[a.type]}</span><span class="lb">${esc(a.label)}</span>
         <span class="mt">${TYPE_KO[a.type]} · 연결 ${a.degree}</span></div>`).join("")
      || `<div class="sd-item"><span class="lb" style="color:var(--muted)">일치하는 행위자 없음</span></div>`;
    drop.hidden = false;
    drop.querySelectorAll(".sd-item[data-slug]").forEach(el =>
      el.addEventListener("pointerdown", ev => { ev.preventDefault(); pick(el.dataset.slug); }));
  }
  function pick(slug) { hideSearch(); input.blur(); input.value = ""; select(slug); }
  function markHot() {
    drop.querySelectorAll(".sd-item").forEach((el, i) => el.classList.toggle("hot", i === hot));
    drop.children[hot]?.scrollIntoView({ block: "nearest" });
  }
  input.addEventListener("input", () => renderDrop(input.value.trim().toLowerCase()));
  input.addEventListener("focus", () => renderDrop(input.value.trim().toLowerCase()));
  input.addEventListener("blur", () => setTimeout(hideSearch, 150));
  input.addEventListener("keydown", ev => {
    if (drop.hidden) return;
    if (ev.key === "ArrowDown") { ev.preventDefault(); hot = Math.min(items.length - 1, hot + 1); markHot(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); hot = Math.max(0, hot - 1); markHot(); }
    else if (ev.key === "Enter" && hot >= 0 && items[hot]) pick(items[hot].slug);
  });

  buildEraDock();
  buildCodex();
  $("#legend-toggle").addEventListener("click", () => $("#legend").classList.toggle("closed"));
  $("#panel-close").addEventListener("click", deselect);
  $("#retry").addEventListener("click", boot);
}

function hideSearch() { $("#search-drop").hidden = true; }

function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ── 시작 ────────────────────────────────────────────────────── */
bindMapControls();
boot();
