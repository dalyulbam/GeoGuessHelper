# 분자(molecule) — 원자 무리가 가리키는 개념

## 개념: atom → molecule

원자(`docs/knowledge/atoms/atm_*.md`)는 **사실 하나**다. 그런데 사실 여러 개가 임베딩 공간에서
서로 가까이 모여 있으면, 그 모임 자체가 어느 원자에도 적혀 있지 않은 **개념 하나**를 가리킬 때가
있다 — 예: [프랑수아 1세 · 성채 설계 · 이탈리아 예술가 초빙] 은 '다빈치'를 가리킨다.
그 개념을 **분자(molecule)** 라 부른다. 분자는 원자의 교집합/연관개념으로 설명되는 단일 개념이며,
분자 문서는 그 개념의 이름·특징·관련 개념·(웹 검색) 근거·아직 없는 중심 원자를 적는다.

파이프라인(`src/geoguesshelper/molecule.py`):

1. **embed** — 원자마다 텍스트(`[layer/category] title — body #tags @entities`)를
   `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`(768d, L2 정규화, GPU)로 임베딩.
   `embeddings.npz` = {ids, vectors, model, created, atom_updated}. 원자의 `updated` 가 같으면 재사용(증분).
2. **build** — 코사인 거리 D = 1 − cos. "가깝다"의 임계 = 전 쌍 거리의 하위 `--pct` 퍼센타일.
   후보 쌍은 각 노드 상위 `--knn` 이웃(기본 상호 kNN)으로 제한한 뒤 임계 그래프를 만든다.
   분자 = 이 그래프의 **최대 클리크**(크기 ≥ `--min-size`). 모든 쌍이 서로 가까운 집합이라,
   A–B·B–C·C–D·A–C 가 가까워도 A–D 가 멀면 D 는 빠지고 {A,B,C} 만 분자가 된다(사용자 정의 그대로).
   겹치는 클리크(자카드 ≥ `--merge`)는 `--merge-mode` 로 처리한다.
3. **name** — 분자마다 LLM(role=fact, sonnet-5, effort=medium, web_search ≤ 2회)이 `molecule_doc` 도구로
   이름(한/영)·슬러그·한 줄·요약(멤버 원자를 `[[atm_id]]` 로 인용)·왜 결합하는가·특징·관련 개념·
   검색 근거(url)·빠진 원자·신뢰도를 낸다 → `mol_<hash>.md`.
4. **reify** — 이름 붙은 분자의 슬러그(또는 name_en 슬러그)가 지식 index 의 entities/tags 키나 원자 제목
   슬러그와 일치하면 그 원자를 `atom_links` 에 넣고 `links.jsonl` 에 기록한다. `missing_atoms` 의 슬러그가
   엔티티로 이미 있으면(=다빈치가 나중에 원자로 발견된 경우) `missing-atom-match` 로 기록한다.
   원자 .md 는 절대 수정하지 않는다 — 원자→분자 백링크는 `index.json` 의 `atom_to_molecules` 맵으로만.

## 파일

| 경로 | 내용 |
|---|---|
| `embeddings.npz` | 원자 임베딩(증분 갱신 근거 `atom_updated` 포함) |
| `snapshots/<YYMMDD_HHMMSS>_p<pct>_k<knn>_s<min>.json` | 시점별 분자 스냅샷: params, n_atoms, n_edges, threshold, distance_percentiles(1/2/5/10/25/50), size_dist, molecules[] |
| `index.json` | `latest_snapshot`, `params`, `molecules{id: …named, name_en/ko, slug, doc, cost_usd, atom_links, in_latest}`, `atom_to_molecules{atm→[mol]}`, `spent_usd` (분자별 `cost_usd` 는 재생성 시 **누적** — `--force` 로 다시 만들면 이전 호출 비용이 더해진다) |
| `mol_<hash12>.md` | 분자 문서(JSON 프론트매터 + 본문). `status` 는 항상 `hypothesis` |
| `links.jsonl` | `{molecule, atom, how: slug-match|title-match|missing-atom-match, key, at}` 한 줄씩 (`key` = 걸린 entities/tags/제목 슬러그) |

분자 id = `"mol_" + sha1(정렬된 멤버 원자 id)[:12]` — **내용 주소**. 같은 구성이면 시점이 달라도 같은 id 이므로
스냅샷 사이에서 "같은 분자가 살아 있는가"를 id 로 셀 수 있다.

## 파라미터의 의미와 채택 근거

| 파라미터 | 기본 | 의미 |
|---|---|---|
| `--pct` | **0.5** | 전 쌍 거리 하위 pct 퍼센타일을 "가깝다" 임계로. 원자 수가 바뀌면 임계도 바뀐다(시점성) |
| `--knn` | 10 | 후보 쌍을 각 노드의 상위 k 이웃으로 제한(밀도 폭주 방지). 기본 **상호(mutual)** kNN — `--no-mutual` 이면 한쪽만 이웃이어도 후보 |
| `--min-size` | **4** | 분자 최소 크기 |
| `--merge` / `--merge-mode` | 0.5 / **absorb** | 자카드 ≥ merge 로 겹치는 클리크 처리. `absorb` = 응집 강한 클리크부터 채택하고 겹치는 것은 새 분자로 세우지 않음(흡수된 클리크에만 있던 원자는 `periphery` 로 남김) → 모든 분자가 **엄격한 클리크**(diameter ≤ 임계). `union` = 합집합(먼 쌍이 섞여 diameter > 임계가 될 수 있고, 낮은 merge 에서 연쇄 병합으로 거대 분자가 생김) |

260907 실측(원자 1,297개, 쌍 840,456개; 거리 mean 0.739, p1 0.380 · p2 0.432 · p5 0.506 · p10 0.569 · p25 0.665 · p50 0.754):

- 기획 초안 값(pct 2.0 · knn 10 한쪽 kNN · min 3 · union 0.75)은 분자 **2,530개**(pct 1.0 → 1,653, 3.0 → 2,973).
  최대 클리크는 서로 한 원자씩만 다른 3-클리크가 수천 개 나오기 때문에, pct 를 1~3 사이에서 움직여도 30~150 에 들어오지 않는다.
- union 병합의 merge 를 0.34 로 낮추면 연쇄 병합으로 686~1,211 개짜리 거대 분자 하나가 생긴다(부적합).
- 상호 kNN + min 4 + absorb 0.5 에서: pct 0.5 → **120개**(원자 343개 포함, 크기 4:89·5:25·6:6, diameter>임계 0개),
  pct 1.0 → 172, pct 2.0 → 220, pct 3.0 → 234. **30~150 구간에 들어오면서 사용자의 정의(먼 쌍 없음)를 그대로 지키는 pct 0.5 를 채택.**
- 참고: union 0.5(상호 kNN, min 4)는 pct 1.0 에서 104개지만 42개가 diameter>임계(먼 쌍 포함, 최대 20원자).

`build --sweep 0.5,1.0,2.0,3.0 [옵션]` 으로 언제든 같은 표를 다시 뽑을 수 있다(저장 안 함).

## 스냅샷의 시점성

분자는 **그 시점의 원자 집합에 대한 함수**다. 원자가 늘면 거리 퍼센타일(임계)과 kNN 이웃이 모두 바뀌므로
같은 파라미터로도 분자가 달라진다. 그래서 `build` 는 항상 새 스냅샷 파일을 만들고, `index.json` 은 최신
스냅샷의 분자만 `in_latest=true` 로 표시한다. 예전 스냅샷의 분자 문서는 지우지 않는다(내용 주소이므로 같은
구성이 다시 나타나면 같은 id 로 이어진다).

## 등급: hypothesis

분자와 그 문서는 **기계 산출물**이다. 기획(`docs/plan/atom-dialogue_260906.html` §5·§8)의 규칙대로 기계 출력은
사람 서명 '주장'보다도 낮은 등급이며, 원자(사실)와 섞이지 않는다. 그래서 프론트매터 `status` 는 항상
`"hypothesis"` 이고, 원자 파일에는 아무것도 쓰지 않는다. 문서의 `search_findings` 만 웹 근거(url)를 가지며,
`summary` 는 멤버 원자 인용(`[[atm_id]]`)으로만 쓴다. 멤버가 아닌 원자를 인용하면 `foreign_citations` 에 따로
표시된다(검토용). `missing_atoms` 는 "이 개념을 완성하려면 있어야 할 원자" — 가설이다.

## 이름 붙이기 순서와 예산

`name` 은 cohesion(평균 내부 거리)이 낮은=응집 강한 순으로 `--limit`(기본 60)개까지, 이번 실행 총비용이
`--budget`(기본 $8) 을 넘으면 중단한다(남은 분자는 `named=false`). 기본 `--order diverse` 는 같은 순서로 훑되
이미 이름 붙은 분자와 원자를 `--overlap`(기본 2)개 이상 공유하는 분자는 건너뛴다 — 같은 원자 무리('청 왕조
목록' 6종)에 예산을 겹쳐 쓰지 않기 위함. 건너뛴 분자는 `skip_reason` 으로 남고 `--order cohesion` 으로 나중에
이름 붙일 수 있다. 호출마다 `cost_usd`·`model`·`web_searches`·`retries` 가 프론트매터에 기록된다.
tool_choice 는 web_search 와 함께 `auto` 로 두고, 응답에 `molecule_doc` 이 없으면 web_search 없이 강제
tool_choice 로 1회 재시도한다.

## 260907 첫 실행 결과(스냅샷 `260907_091951_p0.5_k10_s4`)

- 분자 120개(원자 343/1,297 포함, 크기 4:89 · 5:25 · 6:6, 모두 엄격한 클리크). 최대 클리크 218개 중 98개가 흡수(periphery 로 보존).
- 이름 붙인 분자 62개(diverse 순서로 60 + 시험 2). 건너뜀 44(겹침), 한도 밖 14. 실패 0. 총비용 **$3.78**(호출 79회 — 본 실행 62 + 깨진 배열 필드 재생성 13 + 재시도 4; 분자당 평균 $0.05, 모델 claude-sonnet-5). 검증 단계의 재생성 4건(아래) 후 누적 **$4.00**(index `spent_usd` 4.0034 = 문서 `cost_usd` 합 4.0033, 상한 $8 안).
- **web_search 사용 0회** — tool_choice auto 에서 모델이 검색을 한 번도 고르지 않았다. 모든 문서의 `search_findings` 가 빈 배열이고 근거는 멤버 원자만이다. 검색을 강제하려면 프롬프트에 "반드시 1회 검색" 규칙을 넣거나 2단계 호출(검색 → 문서)로 바꿔야 한다(미구현).
- 알려진 모델 결함: sonnet-5 (thinking off, tool_choice auto) 가 배열 필드를 `<value>…</value>` 문자열로 내고 그 뒤 필드들이 딸려 오는 경우가 62건 중 13건(21%). 지금은 `is_corrupt()` 가 감지해 강제 tool_choice 로 재시도하고, 그래도 깨지면 `_rescue_leaked()` 로 복구해 `parse_rescued: true` 로 표시한다(재생성 13건 중 재시도 4, 최종 복구 표시 1).
- 이름 붙인 62개 중 35개가 history 층 — 대부분 위키 유래 "왕조/정치체 목록" 원자군이다(모델도 이름에 'Wikipedia list' 라고 적었다). 응집이 가장 강한 무리가 곧 가장 정보량 낮은 목록 원자라는 뜻이므로, 원자 저장소 쪽에서 목록형 원자를 따로 표시하거나 분자 빌드에서 제외하는 옵션이 다음 과제다.
- `reify`: 링크 22건 · 분자 5개, 전부 `missing-atom-match`(slug/title 직접 일치는 0 — 분자 슬러그는 다어절 개념명이라 단일 엔티티 슬러그와 겹치지 않는다). 15건은 `dalmatia` 엔티티에 걸린 것(missing_atoms 의 한국어 설명이 slug() 에서 라틴 토큰만 남아 넓게 걸림 — `key` 로 확인 가능). 검증 재생성 뒤 재실행에서는 **19건 · 분자 4개**(mol_f68849a7690a 의 missing_atoms 가 바뀌어 holy-roman-empire/english-dynasties 3건이 사라짐).

## 260907 검증(별도 에이전트) 결과와 수정

- 통과: index/스냅샷 스키마 · 62개 프론트매터 JSON 파싱 · 필수 키 · `id = "mol_" + sha1(",".join(sorted(atoms)))[:12]` 62/62 및 스냅샷 120/120 · 멤버·주변 원자 전부 디스크에 존재 · LLM 작성 절(요약·왜 결합하는가)의 멤버 밖 인용 0 · `embeddings.npz` 로 임계 0.328076 재계산 일치, 120개 전부 diameter ≤ 임계, 멤버 쌍의 상호 kNN10 위반 0, 엣지 2,193 일치 · 개인 식별/민감 속성 grep 0건 · 비용 상한 안.
  (`원자 연결` 절의 [[atm]] 은 reify 가 넣는 **비멤버** 링크라 멤버 밖 인용으로 세지 않는다 — 코드 `cmd_reify.add()` 가 멤버를 걸러낸다.)
- 결함 4건 → `name --force --only` 로 재생성($0.22, 실패 0): ① mol_e288ddd2351f · mol_612e78651f92 — 시험 2건(초기 코드)의 `특징` 절이 `<![CDATA[]]>`/`<item>1919-1` 문자열을 글자 단위로 쪼갠 불릿이었다. ② mol_8950e24b3511 · mol_f68849a7690a — `summary` 끝에 `<parameter name="why_these_cohere">…` 가 딸려 오고 `왜 결합하는가` 절이 비어 있었다(기존 `is_corrupt()` 가 `<parameter` 를 몰라 통과).
- 코드 수정(`molecule.py`): `_CORRUPT_RE` 에 `<parameter` 추가 · `_rescue_leaked()` 가 `<parameter name="k">` 로 딸려 온 문자열/배열 필드를 되살리고 원본 필드의 꼬리를 잘라냄 · `_as_list()` 가 글자 단위로 쪼개진 리스트를 다시 이어 붙여 정화 · `_doc_from_md()` 의 특징도 `_as_list()` 경유(옛 문서는 reify 때 비워짐) · **`--pct` 기본값 2.0 → 0.5**(이 README 의 채택값과 코드가 어긋나 있었다).

## 재실행

시스템 Python(numpy·networkx·sentence_transformers·torch 가 있는 쪽)으로 실행한다. uv 가상환경에는 없다.

```bash
cd D:/26y/GeoGuessHelper
HF_HUB_OFFLINE=1 PYTHONPATH=src python -m geoguesshelper.molecule embed          # 증분(updated 같은 원자 재사용)
PYTHONPATH=src python -m geoguesshelper.molecule build --sweep 0.5,1.0,2.0,3.0  # 비교표만
PYTHONPATH=src python -m geoguesshelper.molecule build --pct 0.5               # 스냅샷 + index
PYTHONPATH=src python -m geoguesshelper.molecule name --limit 60 --budget 8    # LLM 이름·문서
PYTHONPATH=src python -m geoguesshelper.molecule reify                          # atom–molecule 커넥션 재계산
```

PowerShell 은 `$env:HF_HUB_OFFLINE=1; $env:PYTHONPATH="src"; python -m geoguesshelper.molecule …`.
`embed` 는 HF 캐시만 쓴다(`HF_HUB_OFFLINE` 을 코드에서도 기본 1로 둔다). 모델이 캐시에 없으면 한 번은
`HF_HUB_OFFLINE=0` 으로 받아야 한다(260907 에는 캐시에 pooling 설정 190B 만 있어 safetensors 1.1GB 를
받았다 — huggingface.co 는 이 망에서 TLS 검증이 통과했다).
