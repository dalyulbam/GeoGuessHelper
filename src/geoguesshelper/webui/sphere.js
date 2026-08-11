/* 등장방형(equirectangular) 사진구체 뷰어 — 구글 지도처럼 마우스로 돌린다.
 *
 * 왜 필요한가
 *   일부 사진구체 파노(CIHM…)는 Maps JS API 가 아예 제공하지 않는다
 *   (ID 조회·좌표 조회 모두 ZERO_RESULTS). 그래서 돌릴 수 있는 파노 객체 자체가 없다.
 *   예전에는 서버가 잘라 준 평면 이미지를 <img> 로 덮었는데, 그건 **그림이지 뷰어가 아니라서**
 *   사용자가 잡아 끌 수 없었다. 여기서는 등장방형 원본을 텍스처로 받아 직접 투영한다.
 *
 * 어떻게
 *   전체화면 사각형 하나를 그리고, 프래그먼트 셰이더가 픽셀마다 시선 벡터를 만들어
 *   구면좌표로 바꾼 뒤 등장방형 텍스처를 샘플링한다. 구를 만들 필요가 없고,
 *   화면 픽셀당 정확히 한 번만 계산하므로 저사양에서도 가볍다.
 *
 * 각도 규약 (실측으로 확정한 것)
 *   · heading  0..360, 시계방향. 링크의 `-ya` 와 같은 값.
 *   · pitch    -85..85, **위가 양수**. 링크의 `-pi` 와 같은 값.
 *   · fov      이 뷰어는 **수직** FOV 를 들고 다닌다(구글맵 URL 의 `75y` 와 같은 규약).
 *              lh3 의 `-fo` 는 **수평** FOV 다 — 실측: 수직 75°·종횡비 1.5 → 수평 98°,
 *              그 링크의 fo 값이 100 이었다. 그래서 서버로 넘길 때는 반드시 변환한다.
 *              변환은 hFovFor() 가 담당한다.
 */
"use strict";

/** 수직 FOV(도) + 종횡비 → 수평 FOV(도). lh3 `-fo` 로 넘길 때 쓴다. */
function hFovFor(vFovDeg, aspect) {
  const v = (Math.max(1, Math.min(170, vFovDeg)) * Math.PI) / 180;
  const h = 2 * Math.atan(Math.max(0.01, aspect) * Math.tan(v / 2));
  return Math.max(1, Math.min(170, (h * 180) / Math.PI));
}

const _VERT = `
attribute vec2 a_pos;
varying vec2 v_ndc;
void main() {
  v_ndc = a_pos;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

/* 픽셀마다: 시선 벡터 → pitch 회전 → yaw 회전 → 경위도 → 등장방형 uv.
 * u_lonOffset 은 "텍스처의 어느 열이 heading 0 인가"를 담는다. 추측하지 않고
 * 서버가 잘라 준 이미지와 대조해 실측으로 정한 값을 넣는다. */
const _FRAG = `
precision highp float;
varying vec2 v_ndc;
uniform sampler2D u_tex;
uniform float u_yaw;        // 라디안
uniform float u_pitch;      // 라디안
uniform float u_tanHalfV;   // tan(수직FOV / 2)
uniform float u_aspect;     // 폭 / 높이
uniform float u_lonOffset;  // 텍스처 경도 오프셋(0..1)

const float PI = 3.141592653589793;

void main() {
  // 카메라 공간: x 오른쪽, y 위, z 앞
  vec3 dir = normalize(vec3(v_ndc.x * u_tanHalfV * u_aspect,
                            v_ndc.y * u_tanHalfV,
                            1.0));
  float cp = cos(u_pitch), sp = sin(u_pitch);
  vec3 d1 = vec3(dir.x, dir.y * cp - dir.z * sp, dir.y * sp + dir.z * cp);
  float cy = cos(u_yaw), sy = sin(u_yaw);
  vec3 d2 = vec3(d1.x * cy + d1.z * sy, d1.y, -d1.x * sy + d1.z * cy);

  float lon = atan(d2.x, d2.z);                       // -PI..PI
  float lat = asin(clamp(d2.y, -1.0, 1.0));           // -PI/2..PI/2
  vec2 uv = vec2(fract(lon / (2.0 * PI) + u_lonOffset), 0.5 - lat / PI);
  gl_FragColor = texture2D(u_tex, uv);
}`;

/** 사진구체 뷰어를 만든다. host 안에 캔버스를 넣고 포인터 입력을 받는다. */
function createSphereViewer(host, opts) {
  opts = opts || {};
  const canvas = document.createElement("canvas");
  canvas.className = "sphere-canvas";
  host.appendChild(canvas);

  const gl = canvas.getContext("webgl", { alpha: false, antialias: false,
                                          preserveDrawingBuffer: true })
          || canvas.getContext("experimental-webgl", { alpha: false, preserveDrawingBuffer: true });
  if (!gl) {
    canvas.remove();
    return null;                                   // 호출부가 <img> 폴백으로 물러선다
  }

  // preserveDrawingBuffer 를 켜 두는 이유: 이 앱은 캔버스를 읽어 상태를 판정하는 코드가 있고,
  // 끄면 합성 후 읽기가 늘 검게 나온다(실측: 같은 화면이 헤디드에서 mean 0).

  let view = {
    heading: Number(opts.heading) || 0,
    pitch: Number(opts.pitch) || 0,
    fov: Math.max(20, Math.min(120, Number(opts.fov) || 75)),   // 수직
  };
  let lonOffset = opts.lonOffset != null ? Number(opts.lonOffset) : 0.5;
  let tex = null, ready = false, destroyed = false, raf = 0;
  let onChange = opts.onChange || null;
  let onReady = opts.onReady || null;

  // ── 셰이더 ──────────────────────────────────────────────────
  function compile(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      console.error("sphere shader:", gl.getShaderInfoLog(s));
      return null;
    }
    return s;
  }
  const vs = compile(gl.VERTEX_SHADER, _VERT);
  const fs = compile(gl.FRAGMENT_SHADER, _FRAG);
  if (!vs || !fs) { canvas.remove(); return null; }
  const prog = gl.createProgram();
  gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.error("sphere link:", gl.getProgramInfoLog(prog));
    canvas.remove(); return null;
  }
  gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(prog, "a_pos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

  const U = {
    yaw: gl.getUniformLocation(prog, "u_yaw"),
    pitch: gl.getUniformLocation(prog, "u_pitch"),
    tanHalfV: gl.getUniformLocation(prog, "u_tanHalfV"),
    aspect: gl.getUniformLocation(prog, "u_aspect"),
    lonOffset: gl.getUniformLocation(prog, "u_lonOffset"),
    tex: gl.getUniformLocation(prog, "u_tex"),
  };

  // ── 크기 ────────────────────────────────────────────────────
  function resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round((host.clientWidth || 640) * dpr));
    const h = Math.max(1, Math.round((host.clientHeight || 360) * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w; canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
  }
  let ro = null;
  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(() => { resize(); draw(); });
    ro.observe(host);
  }

  // ── 그리기 ──────────────────────────────────────────────────
  function draw() {
    if (destroyed || !ready) return;
    resize();
    gl.useProgram(prog);
    gl.uniform1f(U.yaw, (view.heading * Math.PI) / 180);
    gl.uniform1f(U.pitch, (view.pitch * Math.PI) / 180);
    gl.uniform1f(U.tanHalfV, Math.tan((view.fov * Math.PI) / 360));
    gl.uniform1f(U.aspect, canvas.width / canvas.height);
    gl.uniform1f(U.lonOffset, lonOffset);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.uniform1i(U.tex, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
  function schedule() {
    if (raf || destroyed) return;
    raf = requestAnimationFrame(() => { raf = 0; draw(); });
  }

  // ── 텍스처 ──────────────────────────────────────────────────
  const maxTex = gl.getParameter(gl.MAX_TEXTURE_SIZE) || 2048;
  function setTexture(url) {
    return new Promise((resolve, reject) => {
      const im = new Image();
      im.crossOrigin = "anonymous";
      im.onload = () => {
        if (destroyed) return resolve(false);
        if (im.naturalWidth > maxTex) {
          return reject(new Error(`텍스처가 GPU 한계(${maxTex})를 넘습니다: ${im.naturalWidth}`));
        }
        const t = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, im);
        // 밉맵은 쓰지 않는다 — 경도 이음매에서 밉 레벨이 튀어 세로줄이 생긴다.
        // 4096 폭이면 화면 대비 충분히 촘촘해서 계단현상도 눈에 띄지 않는다.
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);      // 가로는 이어진다
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        const old = tex;
        tex = t; ready = true;
        if (old) gl.deleteTexture(old);
        draw();
        if (onReady) { onReady(); onReady = null; }
        resolve(true);
      };
      im.onerror = () => reject(new Error("텍스처를 받지 못했습니다"));
      im.src = url;
    });
  }

  // ── 입력 ────────────────────────────────────────────────────
  const pts = new Map();
  let last = null, pinch = 0, moved = false;

  function emit() { if (onChange) onChange(getPov()); }

  function onDown(e) {
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 1) { last = { x: e.clientX, y: e.clientY }; moved = false; }
    if (pts.size === 2) pinch = spread();
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* 무시 */ }
    canvas.classList.add("grabbing");
    e.preventDefault();
  }
  function spread() {
    const a = [...pts.values()];
    return Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y) || 1;
  }
  function onMove(e) {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size >= 2) {
      const s = spread();
      if (pinch) setFov(view.fov * (pinch / s));
      pinch = s;
      return;
    }
    if (!last) return;
    // 화면에서 끈 거리를 각도로 바꾼다. 현재 FOV 에 비례시켜야 확대 상태에서도
    // "잡아 끄는" 느낌이 유지된다(줌인했는데 확 돌아가면 조작이 안 된다).
    const perPx = view.fov / Math.max(1, host.clientHeight || 360);
    setPov({
      heading: view.heading - (e.clientX - last.x) * perPx,
      pitch: view.pitch + (e.clientY - last.y) * perPx,
    });
    last = { x: e.clientX, y: e.clientY };
    moved = true;
    e.preventDefault();
  }
  function onUp(e) {
    pts.delete(e.pointerId);
    if (pts.size < 2) pinch = 0;
    if (pts.size === 0) { last = null; canvas.classList.remove("grabbing"); if (moved) emit(); }
  }
  function onWheel(e) {
    e.preventDefault();
    setFov(view.fov * (e.deltaY > 0 ? 1.12 : 1 / 1.12));
    emit();
  }

  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("wheel", onWheel, { passive: false });

  // ── 공개 API ────────────────────────────────────────────────
  function norm(h) { return ((h % 360) + 360) % 360; }
  function getPov() {
    return { heading: norm(view.heading), pitch: view.pitch, fov: view.fov };
  }
  function setPov(p) {
    if (!p) return;
    if (p.heading != null) view.heading = norm(Number(p.heading));
    if (p.pitch != null) view.pitch = Math.max(-85, Math.min(85, Number(p.pitch)));
    if (p.fov != null) setFov(p.fov);
    schedule();
  }
  function setFov(f) {
    view.fov = Math.max(20, Math.min(120, Number(f) || view.fov));
    schedule();
  }
  function destroy() {
    destroyed = true;
    if (raf) cancelAnimationFrame(raf);
    if (ro) ro.disconnect();
    canvas.removeEventListener("pointerdown", onDown);
    canvas.removeEventListener("pointermove", onMove);
    canvas.removeEventListener("pointerup", onUp);
    canvas.removeEventListener("pointercancel", onUp);
    canvas.removeEventListener("wheel", onWheel);
    if (tex) gl.deleteTexture(tex);
    gl.deleteProgram(prog); gl.deleteShader(vs); gl.deleteShader(fs);
    gl.deleteBuffer(buf);
    // WebGL 컨텍스트는 명시적으로 반납한다 — 탭을 여러 번 열고 닫으면
    // 브라우저의 동시 컨텍스트 한도(보통 16개)에 걸려 조용히 렌더가 멎는다.
    const lose = gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
    canvas.remove();
  }

  resize();
  return {
    el: canvas,
    setTexture, setPov, getPov, setFov,
    getFov: () => view.fov,
    setLonOffset: (v) => { lonOffset = Number(v) || 0; schedule(); },
    isReady: () => ready,
    destroy,
  };
}

window.createSphereViewer = createSphereViewer;
window.hFovFor = hFovFor;
