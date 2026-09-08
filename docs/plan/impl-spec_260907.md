# 구현 명세 · 자동 정정 루프 + 원자 대화 + 관측소 (2026-09-07)

기획: `docs/plan/atom-dialogue_260906.html`(대화·제안→원자·프롬프트 주입) · `docs/plan/atom-observatory_260907.html`(관측소 5화면).
이론: `docs/theory/molecule-theory_260907.html`. 실측 배경: `docs/knowledge/baseline/FINDINGS_260907.md`.

## 0. 사용자가 바꾼 것 — 승인 관문 없음, 정정 루프

기획 260906 은 대화 제안이 "제안 → 인용 검사 → 검증 → **사람 승인**" 을 거쳐야 원자가 됐다. 사용자 결정(260907):

> 실측 파노 좌표를 가리고 → "이 이미지는 X 위치이다"라고 판단하고 → 실측 파노를 보고 "X 는 사실 X2 였다"의 수정 작업을 거친다.
> 특징 추출 → 우리가 축적한 atom 참조 → 다음번에 X 가 아닌 X2 가 추측되도록 유도한다.
> **승인은 사람이 할 필요가 없다.** 틀린 원자와 틀린 정보는 더 많은 정보를 흡수하면서 수정해 나가면 된다.

따라서:
- 사람 승인 관문 → **증거 채점**(hits/misses)으로 대체. 기계 산출은 곧바로 원자가 되되 `kind`·`origin`·`status` 표식을 달고, 회상돼 쓰인 뒤 정답/오답에 기여했는지가 채점되어 점수가 오르내린다. 오답만 뒷받침한 원자는 `retracted`(회상 제외, 삭제 아님).
- 학습 신호는 **정정 루프**가 만든다: blind 판단 X → 실측 X2 → "X 처럼 보이지만 X2" 판별자 원자(kind=discriminator, confusions=["X>X2"]) → 다음 blind 판단이 X 를 추측하면 회상이 그 원자를 끌어와 2패스에서 재판단.
- 뷰어(관측소)는 이 모든 것이 "저장되고 있다·쓰이고 있다" 를 보여준다.

## 1. 이미 구현됨 (본체, 다른 패키지가 그대로 쓴다 — 수정 금지)

### 1.1 `src/geoguesshelper/knowledge.py`
- `Atom` 새 필드: `kind: str="fact"`(fact|discriminator|claim) · `status: str="active"`(active|retracted) · `origin: str|None`(report|expansion|wiki|correction|dialogue|baseline) · `hits: int=0` · `misses: int=0` · `confusions: list[str]`(예 `"FI>NO"`). 구 원자는 파일에 없음 → 기본값. `index.json` 의 원자 메타에도 새 원자부터 실린다(구 항목엔 키가 없음 → 읽는 쪽이 기본값 처리).
- `recall(settings, *, lat, lng, place, tags, entities, limit, char_budget, kinds=None, exclude_tiers=("aided",), confusion_isos=None, ctx=None) -> list[Atom]`
  - retracted 제외 · tier∈exclude_tiers 제외 · kinds 필터 · `confusion_isos=["FI"]` 면 confusions 가 `FI>…` 인 판별자는 지리/엔티티 접점 없이도 후보(+2.0) · 점수에 `+0.8·log1p(hits) −1.0·log1p(misses)`.
  - 결과를 `docs/knowledge/recall_log.jsonl` 에 한 줄씩 남긴다: `{at, ctx, mode, place, cell, atom, score, rank, via}` (ctx=`ctx["job"]`, mode=`ctx["mode"]`).
- `record_evidence(settings, *, hits=[ids], misses=[ids], ctx={"job":…}, note="") -> {changed:[…], retracted:[…], restored:[…]}` — 철회 규칙: misses≥3 이고 (hits==0 또는 misses≥2·hits+3). `docs/knowledge/evidence_log.jsonl` 에 기록.
- `ingest_corrections(settings, *, discriminators: list[dict], truth: dict, images, image_panos, report_file, ctx) -> {atoms:[Atom], created, merged}` — LLM 없음. 항목: `{title, body, layer, category, scope(country|region|city|point), tags[], entities[], looks_like{country_iso,country?,region?}, actually{country_iso,country?,region?}, image_index|null, confidence}`. 좌표는 truth(lat/lng) 또는 point 면 그 image 의 pano. 태그에 `discriminator`, `looks-like-xx`, `actually-yy` 자동 추가. 중복은 `_find_near_duplicate` → `_touch`+hits+1.
- `ingest_claims(settings, *, proposals: list[dict], cite_pool: set[str], n_images=0, ctx, report_file="") -> {atoms:[ids], created, merged, links, disputes, rejected:[…]}` — 대화 제안. `type: atom|link|dispute`. atom 은 `cites`(문맥 원자 id 또는 `"image:0"`) ≥1 이어야 한다(기계 관문). link 는 양쪽 refs. dispute 는 `record_evidence(misses=[target])`.
- 기존: `store_for(settings).index()/load()/save()/all_atoms()` · `as_prompt_block(atoms)` · `geohash` · `haversine_km` · `slug` · `LAYERS/SCOPES/ALL_CATEGORIES/CUE_CATEGORIES`.

### 1.2 `src/geoguesshelper/analyze.py`
- `analyze_captures(image_paths, settings, lang="ko", *, known=None, mode=None) -> dict` — `known`(Atom 리스트)을 이미지 **뒤** 텍스트 블록으로 주입(판별자 먼저, "적용되는 것만 따르고 relied_on_atoms 에 적어라"). `mode="blind"` 면 "하단 지도 없음, 좌표 추정 필수" 지시. 결과에 `mode`, `known_offered:[ids]` 추가.
- 도구 `report_location` 스키마에 `relied_on_atoms: [ids]`, `revised_from: str|null` 추가(선택 필드).
- 결과 모양(기존): `{status:"OK", analysis:{best_guess{country,country_iso,confidence,region_or_state,city,coordinate_estimate{lat,lng,radius_km}}, alternatives[], narrowing[{level,question,candidates,observation,discriminator,ruled_out,conclusion,weight,confidence_after}], cues[{category,observation,weight,image_index,level,supports}], landmarks[], cultural_economic_read, overall_confidence, place_slug, relied_on_atoms?, revised_from?}, lang, images:[names], cost_usd, used_model, cache, mode, known_offered}`.

### 1.3 `src/geoguesshelper/config.py` — `Settings`
`correction_enabled=True`(env `GEOHELPER_CORRECTION_OFF=1` 로 끔) · `correction_effort="medium"` · `correction_timeout_s=150` · `correction_max_atoms=8` · `correction_recall_limit=10` · `dialogue_effort="medium"` · `dialogue_max_tokens=6000` · `dialogue_timeout_s=120` · `dialogue_max_context_atoms=24` · `dialogue_max_images=4`. 기존: `knowledge_dir=docs/knowledge`, `captures_dir=captures`, `jobs_dir=docs/jobs`, `reports_dir=docs/report`, `viewport_h=480`, `map_h=260`, 모델 역할 `model_vision(opus-5)/model_fact(sonnet-5)/model_reason(fable-5)/model_draft(haiku)/model_verify(opus-5)`.

### 1.4 `src/geoguesshelper/cli.py`
`geoguesshelper correct <action> [rest…]` → `runpy.run_module("geoguesshelper.correction", run_name="__main__")` (sys.argv = ["geoguesshelper correct", action, *rest]).
`geoguesshelper observe <action> [rest…]` → `geoguesshelper.observe` 같은 방식. 두 모듈은 `main(argv)` + `if __name__ == "__main__": main()` 을 가져야 한다(`molecule.py` 참고).

### 1.5 `altaiya/altaiya-backend/app.py`
`observe_api`, `dialogue_api` 모듈을 `__import__` 해 `app.include_router(<mod>.router)` 한다(실패해도 아틀라스는 뜬다). 두 모듈은 `router = fastapi.APIRouter()` 를 노출해야 한다. altaiya 백엔드는 `sys.path` 에 자기 폴더가 있고, 지식 경로는 `atlas.KNOW`(altaiya/knowledge 가 있으면 그것, 없으면 ../../docs/knowledge), 보고서 `atlas.find_report(name)`, 캡처 `atlas.find_capture(name)`, 원자 md 프론트매터 `atlas.atom_detail(id)`.

### 1.6 기존 재료
- `baseline.py`: `load_jobs(settings)`(image_panos 있는 잡 14건: `{job_id, label, report_file, images, image_panos, aided_prior(analysis dict), prior_lang}`), `_truth(job)`(pano 평균 lat/lng · 파일명 ISO), `blind_crop(src, dst_dir, settings)`(로드뷰 패널 크롭 → `captures/blind/`), `metrics(analysis, truth)`.
- `llm.py`: `call(settings, system=, messages=, tools=, tool_choice=, max_tokens=, effort=, deadline_s=, should_stop=, role=, model=)` · `tool_input(resp, name)` · `spend(resp)` · `used_model(resp)` · `gather(tasks, settings, limit=)` · 예외 `LLMUnavailable`(키 없음).
- 잡 로그 `docs/jobs/jobs.jsonl`(42건, `{id, kind:"report", label, status, result:{status, reports:[{file,…}], primaryAnalysis:{…analyze 결과 + image_panos}, research, knowledge:{recalled,…}, cost_usd}}`).
- 분자: `docs/knowledge/molecule/{index.json, snapshots/*.json, mol_*.md, links.jsonl, embeddings.npz(ids, vectors 1297×768 float32, atom_updated)}` — 스키마는 `docs/knowledge/molecule/README.md`.
- 기준선: `docs/knowledge/baseline/{tiers.json, runs/*.json, store_blind/, store_aided/}`.
- 체크포인트: `docs/knowledge/expansion.json{created:int, cost_usd}`, `wiki.json{created:int, cost_usd}`. 원자 출처 경로 판정: `reports` 비어 있지 않음 → report · 태그에 `x-` 접두 → expansion · 나머지(wikipedia 출처) → wiki · 새 원자는 `origin` 필드.

## 2. 작업 패키지와 파일 소유권 (겹치면 안 된다)

| WP | 소유 파일 | 요약 |
|---|---|---|
| **A2 정정 루프** | `src/geoguesshelper/correction.py`(신규) · `src/geoguesshelper/server.py`(정정 잡 훅만) · `docs/knowledge/corrections/`(산출) · `tests/correction_check.py` | §3 |
| **A3 대화** | `src/geoguesshelper/dialogue.py`(신규) · `altaiya/altaiya-backend/dialogue_api.py`(신규) · `docs/knowledge/dialogue/`(산출) · `tests/dialogue_check.py` | §4 |
| **B 관측 빌드** | `src/geoguesshelper/observe.py`(신규) · `docs/knowledge/observe/`(산출+README.md) · `snapshot-knowledge.ps1`(루트, 신규) · `altaiya/sync-knowledge.ps1`(제외 목록만) | §5 |
| **C 뷰어** | `altaiya/altaiya-backend/observe_api.py`(신규) · `altaiya/altaiya-frontend/observe.html`·`observe.js`·`observe.css`(신규) · `altaiya/altaiya-frontend/index.html`(탭 링크 1개만) · `altaiya/altaiya-frontend/app.js`(원자 패널에 "관측소에서" 링크 1줄만) | §6 |

공통 규칙:
- `knowledge.py`·`analyze.py`·`config.py`·`cli.py`·`app.py` 는 **수정하지 않는다**(문제가 있으면 보고서에 적는다).
- 원자 파일(`docs/knowledge/atoms/*.md`)·분자 문서를 직접 편집하지 않는다. 쓰기는 knowledge API 로만.
- Windows 콘솔은 cp949 — 파이썬 실행 시 `PYTHONUTF8=1`. uv 는 `UV_NATIVE_TLS=1` 필요. 본체 코드는 `uv run python …` / `uv run geoguesshelper …`. numpy·torch 가 필요한 것(임베딩·투영)은 시스템 `python`(`PYTHONPATH=src`).
- 한국어 주석·문서. 기존 파일의 문체(왜/무엇을 적는 긴 주석) 를 따른다.
- git commit 하지 않는다.
- 비용: LLM 호출은 명시된 것만. 실행 전 `--dry-run`/소량(`--limit 2`)으로 확인 후 전체.

## 3. WP-A2 · 정정 루프 `correction.py`

### 3.1 흐름 (잡 1건)
```
입력: job = {job_id, report_file, label, images[], image_panos{name:{lat,lng,heading}}, aided_prior(analysis)}  ← baseline.load_jobs 또는 서버 잡 결과
truth  = baseline._truth(job) + aided_prior.best_guess 의 country/region_or_state/city  (source="aided_prior")
blinds = [baseline.blind_crop(captures_dir/name, captures_dir/"blind", settings) for name in images]  (≤ settings.max_analyze_images)

1-1 blind 1패스: r1 = analyze_captures(blinds, settings, lang, mode="blind")          # 회상 없음 — 순수 판단 X
    X = r1.analysis.best_guess (+alternatives)
1-2 회상: known = knowledge.recall(settings,
            lat=X.coordinate_estimate.lat, lng=…(있으면), place={country,region_or_state,city},
            tags=[cue categories], entities=[X.country, X.region, alternatives[].country],
            confusion_isos=[X.country_iso] + [alt.country_iso…], exclude_tiers=("aided",),
            limit=settings.correction_recall_limit, ctx={"job": job_id, "mode": "blind-p2"})
    known 이 비면 2패스 생략(비용 절약). 있으면
    r2 = analyze_captures(blinds, settings, lang, mode="blind", known=known)          # 같은 이미지 → 프롬프트 캐시 히트
    final = r2 if r2 OK else r1.  relied_on = final.analysis.relied_on_atoms ∩ known ids.  revised_from.
2  실측 공개: verdict = compare(final.best_guess, truth)
     country: iso 비교(hit/miss). region/city: 정규화 문자열 포함/유사도(difflib ≥0.6) → hit/miss/unknown(정답 쪽이 비어 있으면 unknown).
     error_km: haversine(truth, final.coordinate_estimate) · bucket <1 / <10 / <100 / ≥100 / na.
3  정정 호출(LLM, role="vision", model 기본 = settings.model_vision, effort=settings.correction_effort,
     deadline=correction_timeout_s, 이미지 = blinds(같은 블록·cache_control) + 텍스트):
     입력 텍스트: blind 최종 사슬(narrowing·cues·best_guess·alternatives) · 1패스와 다르면 1패스 best_guess 도 ·
       aided(정답 참고) 사슬의 **결론들만**(level: conclusion) — 지도 라벨 관찰은 넘기지 않는다 ·
       truth(iso/country/region/city/lat/lng) · verdict · known 원자 목록(id·title·body) 과 relied_on.
     도구 `correction`(tool_choice 강제):
       summary(EN 2-4문장 "X was actually X2 because…"; hit 이면 "X confirmed; the alternatives A,B were correctly excluded because…")
       misleading_cues[{observation, why_misleading, image_index|null}]
       decisive_cues[{observation, why_decisive, category, image_index|null}]      # 정답을 가리키는, blind 이미지에 **실제로 보이는** 것
       discriminators[{title, body, layer∈LAYERS, category∈ALL_CATEGORIES, scope∈{country,region,city,point},
                       tags[], entities[], looks_like{country_iso,country,region}, actually{country_iso,country,region},
                       image_index|null, confidence}]     # ≤ correction_max_atoms. 반사실적·이전 가능한 패턴만.
                       # miss: looks_like = X, actually = truth. hit: looks_like = 배제 못 한 alternative, actually = truth.
       misled_atoms[ids ⊂ known]  confirming_atoms[ids ⊂ known]  retract_candidates[{atom_id ⊂ known, reason}]
     규칙(프롬프트): 영어 · 지도 라벨/좌표에서 유추한 것을 "보였다"고 쓰지 말 것 · 얼굴/번호판 금지 ·
       판별자는 "X2 에서는 A 가 보이고 X 에서는 B 가 보인다" 꼴의 반사실 · 이 거리 하나에만 참인 것은 제외.
4  적재: ing = knowledge.ingest_corrections(settings, discriminators=…, truth={**truth, "country":…,"region":…,"city":…},
            images=[names], image_panos=…, report_file=report_file, ctx={"job": job_id})
   채점: ev = knowledge.record_evidence(settings, hits=confirming∩offered, misses=(misled ∪ retract_candidates)∩offered,
            ctx={"job": job_id}, note=summary[:200])
5  기록: docs/knowledge/corrections/corr_<job_id>.json (§3.2) + corrections.jsonl 한 줄 + README.md 재생성(§3.3)
```
- `ctx["mode"]` 를 "blind-p1"/"blind-p2" 로 구분해 recall 로그에 남긴다(1패스는 recall 을 안 부르므로 p2 만 남는다 — 그것이 맞다).
- 실패 처리: analyze 가 NO_KEY/API_ERROR 면 `status` 에 남기고 파일은 쓴다(재실행 가능). 정정 호출 도구 입력이 깨지면(sonnet 류 XML 누출 — `molecule.py` 의 `is_corrupt()` 참고) 강제 tool_choice 로 1회 재시도.
- 같은 잡 재실행(`--redo`)은 새 blind 판단을 만들고 `corr_<job_id>.json` 을 덮어쓴다; `corrections.jsonl` 에는 줄이 추가된다(시계열이므로).

### 3.2 `corr_<job_id>.json`
```json
{"job_id","report_file","label","created","lang","images":[…],"image_panos":{…},
 "truth":{"lat","lng","iso","country","region","city","source":"aided_prior"},
 "blind":{"phase1":{"status","analysis":{…},"cost_usd","metrics":{…baseline.metrics}},
          "phase2":{"status","analysis":{…},"known_offered":[ids],"relied_on":[ids],"revised_from":null|"…","cost_usd","metrics":{…}} | null,
          "final":{"country_iso","country","region","city","lat","lng","error_km","reached_level","confidence","from":"phase1|phase2"}},
 "verdict":{"country":"hit|miss","region":"hit|miss|unknown","city":"hit|miss|unknown","error_km":1.2|null,"bucket":"<1|<10|<100|>=100|na"},
 "correction":{…도구 출력 그대로…,"cost_usd","model","retries"},
 "atoms":{"created":[ids],"merged":[ids]},
 "evidence":{"hits":[ids],"misses":[ids],"retracted":[ids],"restored":[ids]},
 "cost_usd":0.0,"status":"OK|NO_IMAGES|API_ERROR|NO_KEY"}
```
`corrections.jsonl` 한 줄: `{"at","job_id","report_file","truth_iso","blind_iso","blind_country","truth_country","country_hit","region_hit","city_hit","error_km","bucket","phase2_used","n_offered","n_relied","revised","n_discriminators","atoms_created","atoms_merged","hits","misses","retracted","cost_usd","status"}`.

### 3.3 `docs/knowledge/corrections/README.md` (자동 재생성)
표: 잡별 (날짜 · 보고서 · 정답 · blind 판단 · 국가/지역/도시 hit · 오차 km · 2패스 사용/의존 원자 수 · 판별자 수 · 비용). 집계: 국가/지역 적중률, 오차 중앙값, 2패스로 판단이 바뀐 건수(revised), 누적 판별자 원자 수, 철회된 원자 수, 총비용. 마지막에 "학습 곡선" — 시간순 country_hit·error_km 열.

### 3.4 CLI (`main(argv)`)
- `run [--limit N] [--job ID …] [--redo] [--lang en] [--no-phase2] [--dry-run]` — dry-run 은 대상 잡과 비용 추정만.
- `report` — README 재생성.
- `one --job ID` = run --job.

### 3.5 서버 훅 (`server.py`)
- `_make_handlers` 에 `correct_job(job)` 추가: payload `{source_job_id, result}` → `correction.run_for_result(settings, source_job_id=…, result=…, label=…, log=job.emit)` (result = report 잡의 반환 dict; `primaryAnalysis.image_panos`·`images` 가 재료). `_QUEUE.register("correct", correct_job)` 를 기존 register 자리(lifespan/시작부)에 추가.
- `report_job` 성공 직후: `settings.correction_enabled` 이고 `primaryAnalysis.image_panos` 가 있으면 `await _QUEUE.submit("correct", {...}, label=f"정정 · {label}", client_key=…)` — 실패는 삼키고 로그만. 보고서 잡 자체는 그대로 끝난다(예산 밖 후속 잡).
- `_build_reports_sync` 의 1) 분석 단계: `start_lat/lng` 가 있으면 분석 **전에** `pre_known = knowledge.recall(settings, lat=start_lat, lng=start_lng, kinds=None, exclude_tiers=("aided",), limit=settings.knowledge_recall_limit, ctx={"job": job.id, "mode":"aided"})` 를 구해 `analyze_captures(paths, settings, base, known=pre_known)` 로 넘긴다(기획 260906 "analyze_captures 에 회상 주입"). 결과 `knowledge` 필드에 `pre_recalled: [ids]`, `relied_on: analysis.relied_on_atoms` 추가.
- `/api/knowledge/atoms` 는 건드리지 않는다.

### 3.6 검증 (`tests/correction_check.py`, `uv run python tests/correction_check.py`)
1. `--dry-run` 이 14건을 나열한다.
2. `--limit 1` 실행 → `corr_*.json` 스키마 키 전부 존재 · `corrections.jsonl` 1줄 · README 생성 · 판별자 원자가 `docs/knowledge/atoms/` 에 kind=discriminator·origin=correction 으로 생겼고 `index.json` 에 실렸다 · `recall_log.jsonl` 에 mode=blind-p2 줄(2패스가 돌았으면).
3. **유도 검증**: 같은 잡을 `--redo` 로 한 번 더 돌리면 2패스 `known_offered` 에 직전에 만든 판별자가 들어오고(confusion 또는 entity 경로), `relied_on` 이 비지 않거나 최소한 offered 에 포함됨을 확인. 결과를 README 에 "유도 확인" 절로 남긴다.
4. 전체 14건 실행 후 README 집계와 `corrections.jsonl` 줄 수 일치.
5. 본체 서버 import 스모크: `uv run python -c "from geoguesshelper.server import build_app; from geoguesshelper.config import load_settings; build_app(load_settings())"`.

## 4. WP-A3 · 원자 대화 `dialogue.py` + `dialogue_api.py`

### 4.1 `dialogue.chat(settings, *, atom_ids=[], molecule_id=None, images=[], messages=[{role,content}], effort=None, lang="ko", session=None) -> dict`
- 문맥 조립: 원자(≤ dialogue_max_context_atoms; molecule_id 가 있으면 그 분자의 멤버·주변 원자와 `mol_*.md` 본문을 문맥에 추가 — `docs/knowledge/molecule/index.json` 의 `molecules[id]` 와 문서) · 이미지(≤ dialogue_max_images, `captures_dir/<name>` 존재하는 것만, base64 블록 + 마지막에 cache_control) · 대화 이력(messages 마지막이 user).
- 시스템 프롬프트(닫힌 세계, 기획 260906 §대화 계약): 문맥 원자·이미지만 인용(`[[atm_id]]`, `image:N`); 문맥 밖 지식은 "문맥에 없다"고 표시하되 일반 지식으로 답할 수 있다(단 인용 없이 제안은 못 한다); 모든 제안(proposal)은 인용 ≥1; 얼굴/번호판 금지; 반사실 판별("이 경우 노르웨이인 이유는 …에서 …가 보이기 때문") 을 우선.
- 도구 `dialogue_turn`(강제): `{answer(lang 으로), cited_atoms[ids], cited_images[int], proposals[{type: atom|link|dispute, title, body, layer, category, scope, tags[], entities[], cites[], atom_a?, atom_b?, target_atom?, reason?}]}`.
- 모델: role="reason"(fable-5) 기본; 이미지가 있으면 role="vision"(opus-5). effort = 인자 or settings.dialogue_effort.
- 후처리: `cited_atoms ⊂ 문맥`(밖 것은 제거하고 `dropped_citations`) → `knowledge.ingest_claims(settings, proposals=…, cite_pool=set(문맥 원자 id), n_images=len(images), ctx={"job": session}, report_file="")` → 반환 `{answer, cited_atoms, cited_images, proposals, ingested, dropped_citations, cost_usd, model, effort, session, context:{atoms:[ids], molecule, images}}`.
- 로그: `docs/knowledge/dialogue/<session>.jsonl` 한 줄/턴 (`{at, effort, model, cost_usd, user, answer, cited_atoms, proposals_n, ingested}`) — session 은 인자 또는 `dlg_<yymmdd_hhmmss>_<4hex>`.

### 4.2 `altaiya/altaiya-backend/dialogue_api.py`
- `router = APIRouter()`; `POST /api/dialogue` body `{atoms:[…], molecule:null|id, images:[…], messages:[…], effort:null|"low"|"medium"|"high", lang:"ko", session:null|str}`. 본체 모듈은 지연 import(`from geoguesshelper import dialogue`; 실패 또는 `LLMUnavailable`/키 없음 → 503 `{"error": "…"}`). 본체 settings 는 `geoguesshelper.config.load_settings()`; 단 지식 경로는 `atlas.KNOW` 로 맞춘다(`dataclasses.replace(settings, knowledge_dir=atlas.KNOW)`) — 배포본에서도 스냅샷을 읽도록. 동기 함수라 `fastapi.concurrency.run_in_threadpool` 로 감싼다.
- `GET /api/dialogue/health` → `{available: bool, reason}`.
- CORS 는 app.py 가 이미 열어 둔다.

### 4.3 검증 (`tests/dialogue_check.py`)
1. 키 있는 환경에서 원자 2개(같은 엔티티) 문맥으로 "이 둘은 같은 얘긴가?" 1턴 → answer 비어 있지 않고 cited_atoms ⊂ 문맥.
2. 제안이 나오면 `ingest_claims` 결과의 created/merged/rejected 가 로그와 일치; `docs/knowledge/atoms/` 에 kind=claim 원자 확인. 제안이 없으면 프롬프트로 "관련 원자를 하나 제안해 달라"고 한 2턴째로 확인.
3. `dialogue_api` 를 TestClient(`fastapi.testclient`)로 health·503 경로(키 없는 settings)·정상 경로 확인.

## 5. WP-B · 관측 빌드 `observe.py` (LLM 0)

### 5.1 `main(argv)`: `build [--no-layout] [--sweep] [--tau-dup 0.10] [--k 15] [--knowledge PATH]` · `help`
시스템 파이썬(numpy·sklearn 1.8 있음, umap 없음)에서 돈다: `PYTHONUTF8=1 PYTHONPATH=src python -m geoguesshelper.observe build`. 본체 uv 환경에서 numpy 없으면 layout/neighbors/dups 를 건너뛰고 나머지(atom_meta·series·growth·lineage)는 만든다(단계적 저하).

### 5.2 산출 `docs/knowledge/observe/`
- `atom_meta.json` = `{"built":ts,"index_mtime":ts,"n":1297,"atoms":{id:{origin, layer, scope, category, kind, status, tier, has_coords, n_sources, n_refs, n_reports, list_like, nn1:{id,d}|null, dup_of:id|null, molecules:[mol ids(멤버)], periphery_of:[mol ids], hits, misses, recalls:{n,last,mean_rank}|null, correction:{job_id}|null, created, updated}}}`
  - origin: 필드가 있으면 그것, 없으면 §1.6 규칙. list_like: origin==wiki 이고 제목이 `\b(list|lists|dynasties|kingdoms|states|empires|rulers|monarchs|polities|chiefdoms|republics|duchies|regimes|principalities)\b` 매치(1차 추정 217개).
  - recalls: `recall_log.jsonl` 집계. correction: `corrections/corr_*.json` 의 atoms.created/merged 역색인.
- `growth.json` = `{"days":[{"day":"2026-08-25","report":n,"expansion":n,"wiki":n,"correction":n,"dialogue":n}]}` (created 소급).
- `series.jsonl` 추가 전용 한 줄/빌드: `{at, index_mtime, snapshot(latest molecule snapshot name|null), atoms, by_layer{}, by_scope{}, by_origin{}, by_kind{}, by_status{}, categorized, categorized_report_origin, with_coords, no_sources, no_period, dup_pairs, list_like, molecules, named, unnamed, links, wiki_only_molecules, no_grounding_docs, spent:{expansion,wiki,molecule,baseline,corrections}, corrections:{n, country_hit_rate, region_hit_rate, median_error_km, revised}, tiers:{unaided,aided,blind-only}(baseline/tiers.json summary), retracted, never_recalled_share|null, signals:[{key, level:"info|warn|alert", value, threshold, on:bool, hint}]}`
  - 신호 10개(기획 §8): stale-snapshot(index_mtime > snapshot created) · stale-observe(빌드 시점엔 항상 off) · uncategorized(전체 >20% 또는 보고서 유래 >10%) · dup-candidates(τ 아래 쌍 >0) · unnamed-backlog(>20%) · list-dominance(명명 분자 중 위키 원자만으로 된 것 >40%) · no-grounding(search_findings 빈 문서 >50%) · aided-in-p1(본 저장소 point 원자 중 tier=aided >10%; 0건이면 baseline tiers 로 "샌드박스" 표기) · merge-rate(최근 10개 잡 merged/created >25%, jobs.jsonl result.knowledge) · never-recalled(로그 30일 이상 · 0회 >80%; 로그 짧으면 off, hint 에 이유).
- `layout.json` = `{"method":"umap|tsne|pca","fitted_at","n","points":[[id,x,y]]}` (x,y ∈[0,1]). umap 있으면 UMAP(15, 0.1); 없으면 sklearn TSNE(init="pca", random_state=0, perplexity 30); 둘 다 없으면 PCA. 이전 layout.json 이 있으면 공통 점으로 프로크루스테스(회전·반전·스케일) 정렬해 튀지 않게 한다. 좌표는 `molecule/embeddings.npz` 에서.
- `neighbors.json` = `{"model","built","k":15,"ids":[…],"quantiles":[q0..q200](전 쌍 코사인 거리의 0..100% 200등분),"knn":{id:[[id,d,mutual],…]},"mutual_edges":[[i,j,d],…]}` (i,j = ids 인덱스). 상호 kNN 만 edges.
- `dups.json` = `{"tau":0.10,"pairs":[{a,b,d,title_a,title_b,origin_a,origin_b,layer_a,layer_b,same_report}]}` 거리 오름차순.
- `lineage.json` = `{"snapshots":[names 시간순],"molecules":{mol:{first_seen,last_seen,status:"born|persist|continue|dissolved",prev,next,jaccard,name_ko?}},"events":[{from,to,born,dissolved,persist,continue,merged,split}],"sweep":{"0.25":[ids],"0.5":[ids],"1.0":[ids],"2.0":[ids],"3.0":[ids]}|null}`. 스냅샷 1개면 전부 born, events 빈 배열. `--sweep` 이면 `molecule.py` 의 빌드 함수를 import 해 저장 없이 pct 별 분자 id 집합을 구한다(`molecule.cmd_build` 에 저장 안 하는 경로가 없으면 그 내부 함수를 재사용; 없으면 sweep 은 null 로 두고 README 에 적는다).
- `README.md`: 파일 설명·스키마·재실행법·이번 빌드 요약(신호 켜진 것).

### 5.3 스크립트
- 루트 `snapshot-knowledge.ps1`(**BOM 있는 UTF-8**, run.ps1 참고): `$env:PYTHONUTF8=1; $env:HF_HUB_OFFLINE=1; $env:PYTHONPATH="src"; python -m geoguesshelper.molecule embed; python -m geoguesshelper.molecule build --pct 0.5; python -m geoguesshelper.observe build; pwsh altaiya\sync-knowledge.ps1` — 각 단계 실패 시 중단·메시지. 머리 주석에 왜 `snapshot-` 인지(원자를 갱신하지 않는다 — 분자·관측·배포 세 겹의 스냅샷을 고정한다).
- `altaiya/sync-knowledge.ps1`: robocopy 제외에 `/XF … "embeddings.npz"` 와 `/XD "store_blind" "store_aided"` 추가(BOM 유지).

### 5.4 검증
독립 스크립트로 index.json 직접 집계 ↔ series 마지막 줄 비교(원자 1,297 · by_origin 351/617/330(+정정 원자) · categorized 53+ · with_coords · 분자 120/62 · dup 19@0.10 · list_like 217). 빌드 전후 `atoms/`·`mol_*.md`·`index.json` 해시 동일. 신호 5개(uncategorized·dup-candidates·unnamed-backlog·list-dominance·no-grounding) on.

## 6. WP-C · 뷰어 `observe.html` + `observe_api.py`

### 6.1 백엔드 라우트(읽기 전용, 사이드카·index·분자·정정·회상 로그를 읽는다; 없으면 빈 구조 + `available:false`)
| 라우트 | 반환 |
|---|---|
| `GET /api/observe/summary` | `{times:{index_mtime, snapshot, observe_built}, counts:{atoms, molecules, named, with_coords, categorized, links, corrections}, by_layer, by_scope, by_origin, by_kind, by_status, gaps:{no_category,no_coords,no_sources,no_period,no_reports}, tiers, corrections:{n,country_hit_rate,region_hit_rate,median_error_km,revised, recent:[…5]}, signals:[…], available:{observe,layout,neighbors,dups,lineage,corrections,recall_log}}` — series 마지막 줄이 있으면 그것을, 없으면 index.json 에서 즉석 집계(신호는 계산 가능한 것만). |
| `GET /api/observe/series` | `{rows:[series 줄들], growth:{days:[…]}}` (growth 는 growth.json, 없으면 index created 로 즉석) |
| `GET /api/observe/atoms` | `{atoms:[{id,title,layer,scope,category,origin,kind,status,tier,has_coords,lat,lng,n_sources,n_reports,created,updated,uses,hits,misses,nn1,dup_of,molecules,periphery_of,recalls,list_like,confusions,tags(≤6),entities(≤4)}]}` — 전부 한 번에(gzip 은 서버 설정 없이 그대로) |
| `GET /api/observe/atom/{id}` | `atlas.atom_detail(id)` + body(md 본문) + `neighbors:[{id,title,d,mutual,same_molecule}]`(≤10) + `molecules:[{id,name_ko,name_en,role:"member|periphery"}]` + `recalls:[{at,ctx,mode,place,rank,score}]`(최근 20) + `evidence:[{at,event,hits,misses,status,note}]` + `corrections:[{job_id,report_file,truth_country,blind_country}]` + `report_links:[…]` + `capture:"/captures/<name>"|null` |
| `GET /api/observe/layout` | layout.json 그대로 (없으면 404 `{error}`) |
| `GET /api/observe/graph` | `{ids, quantiles, mutual_edges, molecules:[{id,atoms,name_ko,cohesion,diameter}] (최신 스냅샷)}` |
| `GET /api/observe/molecules` | `{molecules:[{id,named,name_ko,name_en,slug,n,cohesion,diameter,layers,confidence,cost_usd,web_searches,parse_rescued,wiki_only,in_latest,lineage:{status,first_seen,last_seen},persistence:[pcts],missing_atoms_n,links_n}], snapshot, params}` |
| `GET /api/observe/molecule/{id}` | index 항목 + `doc_md`(mol_*.md 본문) + `members:[{id,title,layer,scope,origin}]` + `periphery:[{id,title, max_d_to_member, far_member}]` + `distance_matrix:[[…]]`(멤버 n×n, neighbors 없으면 null) + `missing_atoms:[{text, matched:{atom,how,key}|null}]`(links.jsonl 대조) + `lineage` |
| `GET /api/observe/corrections` | `{rows:[corrections.jsonl…], readme_md, learning:[{at,country_hit,error_km,revised}]}` + `GET /api/observe/correction/{job_id}` = corr_<id>.json |
| `GET /api/observe/queues` | `{dups:{tau,pairs:[…]}, unnamed:[{id,n,cohesion,cmd}], uncategorized:{n, by_origin, cmd}, list_like:{n, sample:[…20]}, missing_watch:[{molecule,name_ko,text,matched}], retracted:[{id,title,hits,misses}], costs:[{pipeline,usd,budget|null}], stale:{snapshot:bool,observe:bool}}` |
| `GET /api/observe/recall_log?limit=200` | 최근 회상 로그 줄 |

경로: `KNOW = atlas.KNOW`; observe 사이드카 `KNOW/observe/`; 분자 `KNOW/molecule/`; 정정 `KNOW/corrections/`; 로그 `KNOW/recall_log.jsonl`, `KNOW/evidence_log.jsonl`. 파일 mtime 캐시(변경 시 재로딩).

### 6.2 프론트 `observe.html`(+`observe.js`, `observe.css`) — 빌드 도구 없음, 외부 라이브러리 없음(canvas·SVG 직접)
기획 §7 의 다섯 탭 그대로. 상단 띠: 세 시각 + 신호등(켜진 것만 색) + 탭 + "아틀라스 ↔" 링크. 다크·골드 톤은 아틀라스 `styles.css` 와 어울리게(폰트 Pretendard/Jost 링크 재사용), 그러나 파일은 별도.
1. **계기판**: KPI 8칸 · 성장 띠(일별 막대, 경로별 색, 소급/관측 경계 점선) · 층×분자화율 막대 · 결손 칩 · **정정 루프 카드**(국가/지역 적중률 · 오차 중앙값 · 2패스로 바뀐 건수 · 최근 5건 "X → X2" 줄 · 학습 곡선 미니 차트) · 신호등 10개(클릭 → 작업대 해당 탭).
2. **원자 대장**: 가상 스크롤 표(행 높이 고정, 보이는 것만 렌더) · 패싯(층·스코프·경로·범주 유무·좌표 유무·tier·kind·status·분자 소속·중복후보·목록형·회상 0/1+) · 정렬(생성·갱신·회상 수·nn1·hits·misses·제목) · 검색 · 행 클릭 → 우측 서랍(프론트매터·본문·출처·보고서 링크 `/reports/<name>`·캡처 썸네일(있으면)·이웃 10 거리 막대(같은 분자 실선/주변 점선/중복 빨강)·분자 배지·tier·hits/misses/status·회상 이력·정정 이력) · 버튼 "지도에서"(`index.html#atom=<id>`) · "**이 원자를 두고 대화**"(§6.3).
3. **임베딩 지도**: canvas 산점(1,297점) · 색 전환(층·경로·범주 유무·tier·kind·분자 소속) · 스냅샷 헐(실선, 멤버 convex hull) · 임계 슬라이더(pct 0.25–3.0, quantiles 로 ε 표시) → 브라우저에서 mutual_edges 중 d≤ε 그래프의 **극대 클리크(≥4)** 를 Bron–Kerbosch(피벗)로 구해 점선 헐 · "이 pct 로 build 명령 복사" · 호버 툴팁 · 클릭 → 서랍 · 올가미(shift+드래그) → 대장 필터 · 헐 클릭 → 분자 서가 상세 · 헐 diameter 배지 · 범례에 "2D 는 안내도" 문장. layout 없으면 "빌드 필요" 안내 + 명령.
4. **분자 서가**: 카드 격자(정렬 응집/크기/신뢰도/비용/계보) · 필터(명명/미명명 · 층 · 위키 전용 제외 · 링크 有) · 카드 점선 테두리(hypothesis) · 상세 패널: md 렌더(간단 마크다운 → HTML: 제목·불릿·`[[atm_id]]` → 원자 링크) · 멤버 n×n 거리 히트 · 주변 near-miss(가장 먼 멤버·거리) · 빠진 원자 감시(matched ✓) · 계보 띠 · 지속성 막대 · 배지(모델·web_search·parse_rescued·cost) · "임베딩 지도에서" · "**이 분자를 두고 대화**".
5. **작업대**: 탭 — 근중복(τ, 쌍, 둘 다 열기, CSV 복사, 병합 검토 프롬프트 복사) · 미명명(`molecule name --only <id>` 복사, 예상 비용) · 미분류(`geoguesshelper categorize` 복사) · 목록형 · 빠진 원자 감시 · 철회된 원자 · **정정 원장**(corrections 표 + README) · 비용 원장 · stale 안내(`snapshot-knowledge.ps1`).
- 해시 라우팅: `#tab=atoms&atom=atm_…`, `#tab=molecules&mol=mol_…`, `#atom=atm_…`(대장 열고 서랍).
- 성능: 5,000행 가정. 표는 가상 스크롤, 산점은 requestAnimationFrame + 오프스크린 캐시.

### 6.3 대화 서랍(공용 컴포넌트)
원자 서랍·분자 상세의 "대화" 버튼 → 우측 하단 대화 패널: 문맥 칩(원자 ids · 분자 · 이미지) · effort 선택(low/medium/high) · 메시지 리스트 · 입력 · 전송 → `POST /api/dialogue`(§4.2) → 답변(인용 `[[atm_id]]` 를 링크로) · 제안 카드(적재 결과: created/merged/rejected 배지 — "사람 승인 없음 · claim 등급" 문구) · 비용 표시. 503 이면 "이 배포본에는 모델 키가 없다 — 로컬에서 실행" 안내. 첫 메시지 예시 칩: "이 지붕 모양이면 핀란드 아닌가?" · "이 두 원자는 같은 얘긴가?" · "이 분자에 빠진 원자는 무엇인가?".

### 6.4 `index.html`·`app.js` 최소 변경
- `index.html` 상단바에 `<a class="topbtn" href="observe.html">🔭 관측소</a>` 하나.
- `app.js` `renderAtomPanel` 끝에 "🔭 관측소에서" 링크(`observe.html#atom=<id>`) 한 줄. 그 외 수정 금지.
- `observe.html` 이 `#atom=` 해시를 받으면 대장 탭을 열고 서랍을 띄운다.

### 6.5 검증
- 백엔드: `uv run python -c` 로 TestClient 호출 — 10개 라우트 200(사이드카가 없을 때도 500 이 아닌 빈 구조/404 JSON). `docs/knowledge/observe/` 가 있으면 summary.counts.atoms == index 원자 수.
- 프론트: `node --check observe.js` 문법 · 서버 띄워(`uv run python altaiya/altaiya-backend/app.py` 또는 `altaiya/run.ps1`) `curl` 로 `/observe.html` 200 · 다섯 탭 DOM 존재 · 콘솔 에러 0(가능하면 headless chromium 은 본체 `capture.py` 가 쓰는 playwright/pyppeteer 재사용해 스크린샷 5장 → `docs/knowledge/observe/screens/`).
- 미리보기 = 규칙: pct 0.5 에서 브라우저 클리크(≥4, mutual kNN) 결과가 스냅샷 분자 id 집합과 같아야 한다 — `observe.js` 의 클리크 함수를 node 로 떼어 실행하는 스크립트(`tests/observe_clique_check.mjs`)로 검증.

## 7. 통합·순서
1. A2·A3·B·C 병렬. C 는 B 산출이 없을 때도 index.json 만으로 ①②가 뜨게 만들고, B 가 끝나면 사이드카를 읽는다.
2. 통합 담당(주 세션): 본체 서버 `/api/dialogue` 라우트(A3 의 `dialogue.chat` 호출, server.py 는 A2 소유라 A2 끝난 뒤) · 전체 스모크 · README(루트) 갱신 · 기획서 두 편에 "구현 상태" 절 추가.
