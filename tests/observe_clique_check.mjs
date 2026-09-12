// 미리보기 = 규칙(V7) — `node tests/observe_clique_check.mjs`
//
// 브라우저가 쓰는 클리크 코드(altaiya/altaiya-frontend/clique.js)를 그대로 node 로 읽어, neighbors.json 의
// 상호 kNN 엣지에서 스냅샷과 같은 파라미터(pct 0.5 · knn 10 · min 4 · absorb 0.5)로 분자를 만들고,
// 최신 분자 스냅샷(molecule/index.json.latest_snapshot)의 멤버 집합과 **id 단위로** 같은지 본다.
// 같아야 "브라우저 클리크 구현이 파이썬 구현과 같은 정의"라는 증명이 된다.
// neighbors.json 이 아직 없으면(WP-B 미완) SKIP 으로 끝낸다(exit 0, 메시지 출력).
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, "..");
const KNOW = process.env.OBS_KNOW ? path.resolve(process.env.OBS_KNOW) : path.join(ROOT, "docs", "knowledge");
const require = createRequire(import.meta.url);
const C = require(path.join(ROOT, "altaiya", "altaiya-frontend", "clique.js"));

const nbPath = path.join(KNOW, "observe", "neighbors.json");
if (!existsSync(nbPath)) {
  console.log(`SKIP — ${nbPath} 가 없다. WP-B 의 observe build(PYTHONUTF8=1 PYTHONPATH=src python -m geoguesshelper.observe build) 뒤 다시 돌려라.`);
  process.exit(0);
}
const nb = JSON.parse(readFileSync(nbPath, "utf8"));
const molIdx = JSON.parse(readFileSync(path.join(KNOW, "molecule", "index.json"), "utf8"));
const snapPath = path.join(KNOW, "molecule", molIdx.latest_snapshot);
const snap = JSON.parse(readFileSync(snapPath, "utf8"));
const P = snap.params || {};
const knn = P.knn ?? 10, minSize = P.min_size ?? 4, merge = P.merge ?? 0.5, pct = P.pct ?? 0.5;

console.log(`스냅샷 ${path.basename(snapPath)} · pct ${pct} · knn ${knn} · min ${minSize} · merge ${merge} ${P.merge_mode || ""} · 임계 ${snap.threshold} · 엣지 ${snap.n_edges} · 원시 클리크 ${snap.n_cliques_raw} · 분자 ${snap.n_molecules}`);
console.log(`neighbors.json · k=${nb.k} · ids ${nb.ids.length} · 엣지 ${nb.mutual_edges.length} · 분위표 ${nb.quantiles.length} · pct_thresholds ${JSON.stringify(nb.pct_thresholds || null)}`);

// 1) ε — 빌드가 float32 경로로 계산한 pct_thresholds 우선, 없으면 분위표 보간. 스냅샷 임계와 비교.
const eps = C.epsilonFor(nb.quantiles, pct, nb.pct_thresholds);
const epsQ = C.epsilonFor(nb.quantiles, pct);
console.log(`ε(pct ${pct}) = ${eps}  (분위표 보간 ${epsQ}) · 스냅샷 threshold ${snap.threshold} · 차 ${Math.abs(eps - snap.threshold).toExponential(2)}`);

// 2) 엣지 — 5-튜플이면 순위로 걸러 스냅샷 knn 의 상호 kNN 만
const edges = C.filterEdgesByRank(nb.mutual_edges, knn);
const kept = C.filterEdges(edges, eps);
console.log(`상호 kNN(k=${knn}) 엣지 ${edges.length} → d ≤ ε 엣지 ${kept.length} (스냅샷 n_edges ${snap.n_edges})`);

// 3) 클리크 + absorb
const t0 = performance.now();
const res = C.moleculesFromEdges({ ids: nb.ids, edges, eps, minSize, merge });
const ms = performance.now() - t0;
console.log(`브라우저 규칙: 원시 클리크 ${res.n_cliques_raw} → 분자 ${res.molecules.length} · ${ms.toFixed(0)}ms`);

// 4) id 단위 비교(내용 주소 = 정렬된 멤버 집합의 sha1)
const snapIds = new Map(snap.molecules.map(m => [m.id, m]));
const prevIds = new Map(res.molecules.map(m => [m.id, m]));
const missing = [...snapIds.keys()].filter(id => !prevIds.has(id));
const extra = [...prevIds.keys()].filter(id => !snapIds.has(id));
// 스냅샷 id 가 멤버 집합의 sha1 인지도 확인(sha1 구현 검증)
const badId = snap.molecules.filter(m => C.molId(m.atoms) !== m.id).length;

let periphDiff = 0, statDiff = 0;
for (const [id, m] of snapIds) {
  const p = prevIds.get(id); if (!p) continue;
  const a = [...(m.periphery || [])].sort().join(","), b = [...(p.periphery || [])].sort().join(",");
  if (a !== b) periphDiff++;
  if (Math.abs((m.cohesion ?? 0) - p.cohesion) > 5e-4 || Math.abs((m.diameter ?? 0) - p.diameter) > 5e-4) statDiff++;
}

console.log(`\n분자 id 집합: 스냅샷 ${snapIds.size} · 미리보기 ${prevIds.size} · 공통 ${snapIds.size - missing.length} · 스냅샷에만 ${missing.length} · 미리보기에만 ${extra.length}`);
console.log(`원시 클리크 수: 스냅샷 ${snap.n_cliques_raw} · 미리보기 ${res.n_cliques_raw} · 엣지 수: ${snap.n_edges} · ${kept.length}`);
console.log(`sha1 id 불일치 ${badId} · periphery 다른 분자 ${periphDiff} · cohesion/diameter 5e-4 이상 다른 분자 ${statDiff}`);
for (const id of missing.slice(0, 5)) console.log(`  스냅샷에만: ${id} ${JSON.stringify(snapIds.get(id).atoms)}`);
for (const id of extra.slice(0, 5)) console.log(`  미리보기에만: ${id} ${JSON.stringify(prevIds.get(id).members)}`);

// 선언한 검증 대상 전부가 실패 조건이다 — 예전엔 id 집합만 보고 엣지·원시 클리크 수·periphery·통계 차이는 출력만 했다(codex 2차 260908)
const edgeOk = kept.length === snap.n_edges, rawOk = res.n_cliques_raw === snap.n_cliques_raw;
const thrOk = Math.abs(eps - snap.threshold) < 1e-6;
const ok = missing.length === 0 && extra.length === 0 && badId === 0 && edgeOk && rawOk && periphDiff === 0 && statDiff === 0 && thrOk && !res.truncated;
if (!ok) console.log(`  조건: edges ${edgeOk} · raw ${rawOk} · periphery ${periphDiff === 0} · stats ${statDiff === 0} · threshold ${thrOk} · truncated ${!!res.truncated}`);
console.log(ok ? "\nPASS — 브라우저 미리보기(pct 0.5)가 스냅샷과 id·엣지·클리크 수·periphery·통계까지 같다" : "\nFAIL — 스냅샷과 다르다");
process.exit(ok ? 0 : 1);
