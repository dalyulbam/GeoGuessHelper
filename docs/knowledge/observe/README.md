# 관측 사이드카 (observe/) — 빌드가 남기고 관측소가 읽는 것

`geoguesshelper observe build` (= `PYTHONUTF8=1 PYTHONPATH=src python -m geoguesshelper.observe build`) 가 만든다.
**LLM 호출 0 · 원자 무변경** — `atoms/`·`molecule/`·`index.json` 은 읽기만 하고, 쓰는 곳은 이 폴더뿐이다.
기획: `docs/plan/atom-observatory_260907.html` §데이터·§신호 · 명세: `docs/plan/impl-spec_260907.md` §5.

여기 있는 것은 전부 **파생**(최근접 거리·중복 후보·분자 소속·회상 횟수·2D 좌표)이지 사실이 아니다. 그래서 원자 md 에
쓰지 않고 따로 둔다 — 통째로 지워도 잃는 것이 없고, 다시 빌드하면 돌아온다(`series.jsonl` 만 추가 전용 시계열).

## 이번 빌드 — 2026-09-08 17:06:25

- 원자 **1373** (경로 {"expansion": 615, "report": 358, "wiki": 330, "correction": 69, "dialogue": 1} · kind {"fact": 1302, "discriminator": 70, "claim": 1} · status {"active": 1373})
- 범주 있음 129 (보고서 유래 59) · 좌표 581 · 출처 없음 746 · 시기 없음 1148 · 목록형 217 · 철회 0
- 분자 128 (명명 60 · 미명명 68 · 위키 전용 64, 명명 중 26) · 링크 19 · 빠진 원자 133 · 근거 없는 문서 60
- 스냅샷 `260908_170604_p0.5_k10_s4.json` · 임베딩 있는 원자 1373 / 없는 원자 0
- 근중복: 21쌍(18묶음, union-find) (τ=0.1) · 기준 {'0.10': 21, '0.12': 43, '0.15': 103}
- 링크 후보(공통 이웃, refs 자동 제안 재료): Adamic-Adar 상위 200건 · 다리(bridge, 멀지만 공통 이웃 ≥5·목록형 제외) 100건 · 점수 매긴 쌍 24,783
- 레이아웃: tsne · 1373점 · 이전 레이아웃에 프로크루스테스 정렬(disparity 0.013129)
- 이웃: k=15 · 상호 엣지 6,180 · 분위표 201개
- 계보: 스냅샷 5개 · 전이 4건 · sweep pct 0.25 → 77 · pct 0.5 → 128 · pct 1.0 → 193 · pct 2.0 → 240 · pct 3.0 → 251
- 정정 루프: 16건 · 국가 적중 1.0 · 지역 적중 1.0 · 오차 중앙값 1.15 km · 2패스로 바뀐 판단 0
- 비용(USD): {"expansion": 3.5269, "wiki": 4.3782, "molecule": 4.0034, "baseline": 8.9773, "corrections": 9.0284}
- 소요 5.99s

### 신호 — 켜진 것 5/10: `uncategorized`, `dup-candidates`, `unnamed-backlog`, `list-dominance`, `no-grounding`

| 신호 | 등급 | 상태 | 값 | 임계 | 설명 |
|---|---|---|---|---|---|
| `stale-snapshot` | warn | 꺼짐 | -88748 | index_mtime > snapshot.created | 스냅샷이 index 보다 24.7시간 새롭다 · 임베딩 없는 원자 0개 |
| `stale-observe` | warn | 꺼짐 | 0 | index_mtime > observe.built | 빌드 직후라 꺼짐 — 뷰어가 index.json mtime > series.at 이면 켠다 (observe build) |
| `uncategorized` | alert | **켜짐** | 0.906 | 전체 > 0.20 또는 보고서 유래 > 0.10 | 범주 없음 1244/1373 (91%) · 보고서 유래 299/358 (84%) → uv run geoguesshelper categorize |
| `dup-candidates` | warn | **켜짐** | 21 | d < 0.1 쌍 > 0 | 근중복 후보 21쌍 (τ=0.1) · 최근접 0.043 “Wangchuck dynasty (Bhutan)” ↔ “Wangchuck dynasty of Bhutan (1907–presen” · 판정은 사람 — 작업대 근중복 탭 |
| `unnamed-backlog` | info | **켜짐** | 0.5312 | > 0.20 | 미명명 68/128 · 예상 ≈ $3.40 → molecule name --order cohesion |
| `list-dominance` | warn | **켜짐** | 0.4333 | > 0.40 | 명명 분자 중 위키 원자만으로 된 것 26/60 (전체 64/128) · 목록형 원자 217개 → 분자 빌드 --exclude list_like 검토 |
| `no-grounding` | warn | **켜짐** | 1.0 | > 0.50 | search_findings 빈 문서 60/60 — 이름이 멤버 원자만으로 지어졌다(가설 등급 확인). 명명 프롬프트에 검색 1회 필수 검토 |
| `aided-in-p1` | alert | 꺼짐 | 0.0928 | > 0.10 | 샌드박스(baseline/tiers.json) aided 22/237 (9.3%) — 본 저장소 point 원자엔 tier 가 없어 샌드박스 값으로만 계산 |
| `merge-rate` | info | 꺼짐 | 0.1786 | > 0.25 | 최근 10개 잡 merged 5 / created 28 — 같은 장소 반복 조사 또는 병합 임계 완화 신호 |
| `never-recalled` | info | 꺼짐 | 0.9126 | 로그 ≥ 30일 · 0회 > 0.80 | 회상 0회 원자 91% · 로그 210줄 · 0.06일 — 로그가 30일 미만이라 판정 보류 |

`stale-observe` 는 빌드 시점엔 정의상 꺼져 있다 — 뷰어가 `index.json` mtime 과 `series` 마지막 줄 `at` 을 비교해 켠다.

## 파일

| 파일 | 내용 | 갱신 |
|---|---|---|
| `atom_meta.json` | `{built, index_mtime, n, tau_dup, k, embedded, unembedded, atoms:{id: {origin, layer, scope, category, kind, status, tier, has_coords, n_sources, n_refs, n_reports, list_like, nn1:{id,d}\|null, dup_of:id\|null, molecules:[mol…], periphery_of:[mol…], hits, misses, recalls:{n,last,mean_rank}\|null, correction:{job_id,n}\|null, created, updated}}}` | 매 빌드 |
| `growth.json` | `{built, days:[{day:"YYYY-MM-DD", report, expansion, wiki, correction, dialogue, baseline, total}]}` — `created` 를 **로컬 날짜**로 소급. 원자가 있는 날만 | 매 빌드 |
| `series.jsonl` | 빌드마다 한 줄 **추가**: `{at, at_iso, index_mtime, snapshot, snapshot_created, atoms, by_layer, by_scope, by_origin, by_kind, by_status, categorized, categorized_report_origin, with_coords, no_sources, no_period, dup_pairs, tau_dup, dup_counts_at, list_like, molecules, named, unnamed, links, wiki_only_molecules, wiki_only_named, no_grounding_docs, missing_atoms, spent:{expansion,wiki,molecule,baseline,corrections}, corrections:{n,country_hit_rate,region_hit_rate,median_error_km,revised,cost_usd}, tiers:{unaided,aided,blind-only}\|null, retracted, never_recalled_share\|null, recall_log, embedded, unembedded, k, layout, sweep, signals:[{key, level:info\|warn\|alert, value, threshold, on, hint}], built_s}` | 매 빌드 추가 |
| `layout.json` | `{method:"umap\|tsne\|pca", fitted_at, n, ref_layout, aligned, disparity, points:[[id,x,y]]}` — x,y ∈ [0,1]. umap 없으면 sklearn TSNE(init=pca, random_state=0, perplexity 30), 그것도 없으면 PCA. 이전 파일이 있으면 공통 원자로 프로크루스테스(회전·반전·스케일) 정렬 뒤 min-max 정규화 — 배치는 유지되지만 수치가 완전히 같지는 않다. **2D 는 안내도** — 판정은 거리 행렬로 | npz 갱신 시 |
| `neighbors.json` | `{model, built, embeddings_created, k, n, ids:[…], quantiles:[q0..q200], pct_thresholds:{"0.25":ε,…}, snapshot_params:{knn,min_size,merge,merge_mode,mutual}, snapshot_threshold, knn:{id:[[id,d,mutual],…]}, mutual_edges:[[i,j,d,ri,rj],…]}` — `quantiles[i]` = 전 쌍 코사인 거리의 i×0.5 퍼센타일(201개, 0~100%). `knn[id]` 는 거리 오름차순(= 순위). `mutual_edges` 의 i,j 는 **`ids` 배열의 인덱스**(i<j), 상호 kNN 쌍만; `ri`/`rj` 는 j 가 i 의 몇 번째 이웃인가(0 시작)와 그 반대. **스냅샷(knn=10) 재현** = `max(ri,rj) < snapshot_params.knn` 이고 `d ≤ ε` 인 엣지만 남긴 그래프의 극대 클리크(≥ min_size) — pct 0.5 에서 엣지 2,193 · 분자 120 이 나와야 한다. pct → ε 는 `quantiles[round(pct/0.5)]`(0.25 같은 중간값은 `pct_thresholds` 또는 선형 보간) | npz 갱신 시 |
| `dups.json` | `{tau, built, n_pairs, counts_at:{"0.10":n,"0.12":n,"0.15":n}, pairs:[{a,b,d,title_a,title_b,origin_a,origin_b,layer_a,layer_b,same_report}], groups:[{atoms:[…],n,direct_pairs,transitive_only,d_min,d_max}]}` pairs 는 거리 오름차순. **판정은 사람** — 이 파일은 지우지 않는다. `groups`(260908)는 pairs 를 union-find 로 묶은 전이적 검토 묶음(보조 뷰, 확정 아님) — `transitive_only` 는 그룹 안 모든 쌍이 직접 임계 이내는 아니라는 뜻 | npz 갱신 시 |
| `link_candidates.json` | `{built, method:"adamic_adar", n, n_bridges, n_pairs_scored, params, candidates:[{a,b,score,common_neighbors,d,below_eps,list_like,title_a,title_b,layer_a,layer_b}], bridges:[{…같음, bridge}]}` (260908) — 상호 kNN 그래프에서 직접 안 이어진 쌍 중 공통 이웃 가중합(Adamic-Adar, Zhou·Lü·Zhang 2009) 상위 200 = `refs` 자동 후보(실측: 대부분 "거의 이웃" — 상호 kNN 컷에 걸린 가까운 쌍). `bridges` 는 목록형 제외 · d ≥ pct1.0 임계 · 공통 이웃 ≥5 인 **먼** 쌍을 score×d 로 순위 — molecule-theory_260907 §3.2 "허브형 분자"(부재 노드) 후보 | npz 갱신 시 |
| `lineage.json` | `{built, snapshots:[names 시간순], latest, molecules:{mol:{first_seen,last_seen,status:born\|persist\|continue\|dissolved,prev,next,jaccard,seen,name_ko?,persistence?:[pcts]}}, events:[{from,to,born,dissolved,persist,continue,merged,split, ids:{born:[…],dissolved:[…],persist:[…],continue:[[prev,new,j]],merged:[[new,[prev…]]],split:[[prev,[new…]]]}}], sweep:{"0.25":[ids],"0.5":[…],"1.0":[…],"2.0":[…],"3.0":[…]}\|null, sweep_thresholds, sweep_params, sweep_note}` — 규칙: 같은 id = persist · 자카드 ≥ 0.5 = continue · 한쪽만 = born/dissolved · 절반 이상 겹침의 다대일/일대다 = merged/split. `continue` 는 자카드 ≥0.5 후보에 대한 **전역 최적 1:1 매칭**(헝가리안, scipy, 260908 — 이전 그리디는 두 new 가 같은 old 를 놓고 경쟁하면 하나가 next 를 잃었다). scipy 없으면 이전 그리디로 저하. 스냅샷 1개면 전부 born, events [] | 스냅샷 추가 시 |
| `README.md` | 이 문서(빌드마다 재생성) | 매 빌드 |

`.tmp` 로 쓰고 교체(원자적)한다. 뷰어는 반쯤 쓰인 파일을 보지 않는다.

## 집계 규칙(재현 가능해야 한다 — `tests/observe_check.py` 가 index.json·npz 에서 독립 재계산해 대조)

- **origin**: 원자에 `origin` 필드가 있으면 그것. 없으면(구 원자) `reports` 비어 있지 않음 → `report` · 태그에 `x-` 접두 → `expansion` · 나머지 → `wiki`.
  reports 와 x- 태그를 둘 다 가진 원자 1개가 report 로 가므로 expansion 은 체크포인트 `expansion.json.created`(617)보다 1 적다(616).
- **list_like**: origin==wiki 이고 제목이 `\b(list|lists|dynasties|kingdoms|states|empires|rulers|monarchs|polities|chiefdoms|republics|duchies|regimes|principalities)\b`(대소문자 무시)에 걸림.
- **categorized**: `category` 가 빈 문자열/None 이 아님. `categorized_report_origin` 은 그중 origin==report.
- **dup_pairs**: 임베딩 전 쌍 중 `1 − cos < τ`(기본 0.10). **wiki_only_molecules**: 멤버 전원이 origin==wiki 인 최신 스냅샷 분자. **no_grounding_docs**: 명명 분자 문서의 `## 검색 근거` 절에 실제 불릿이 없음.
- **corrections**: `corrections/corrections.jsonl`(없으면 `corr_*.json`) — country/region hit 은 bool 또는 hit/miss/unknown, 오차 중앙값은 null 제외.
- **recalls**: `recall_log.jsonl` 원자별 {n, last, mean_rank}; `never_recalled_share` = 로그가 있을 때 회상 0회 원자 비율(로그 30일 미만이면 신호는 보류).
- **sweep**: `molecule.py` 의 `_distances → _graph_for → _molecules_from` 을 저장 없이 재사용(파라미터는 `molecule/index.json` 의 `params`). pct 0.5 의 id 집합은 최신 스냅샷과 같아야 한다(검증 260907: 120/120).

## 재실행

```powershell
$env:PYTHONUTF8=1; $env:PYTHONPATH="src"
python -m geoguesshelper.observe build --sweep            # 전부(시스템 Python: numpy·scikit-learn·networkx)
python -m geoguesshelper.observe build --no-layout        # t-SNE 생략(수 초 절약)
python -m geoguesshelper.observe build --tau-dup 0.12 --k 20
uv run geoguesshelper observe build                       # uv 환경 — numpy 없으면 layout/neighbors/dups 건너뜀(단계적 저하)
.\snapshot-knowledge.ps1 [-SkipEmbed] [-SkipSync]         # embed → molecule build --pct 0.5 → observe build → altaiya 동기화
```

원자를 새로 쌓은 날은 `snapshot-knowledge.ps1` 을 한 번 돈다(기획 결정 260907 — 스케줄러 없음). 빠뜨리면 `stale-snapshot` 이 알려준다.
