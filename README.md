# GeoGuessHelper

지오게서 라운드 후 받은 **구글맵 링크**를 붙여넣고 "추출"을 누르면 — 해당 좌표의 **구글 스트리트뷰(로드뷰)** 가 화면 위쪽에,
**지도**가 아래쪽에 **2분할**로 뜬다. 로드뷰 안에서 이동하며 기념물·도시·사람 장면을 **캡처**하고, 캡처를 Claude 비전으로 분석해
그 위치의 **지리·문화·경제적 특징**을 리포트로 뽑는다.

> 설계 문서: [`docs/plan/geoguesshelper-split-capture-plan_260708.html`](docs/plan/geoguesshelper-split-capture-plan_260708.html)

---

## 빠른 시작

```powershell
# 방법 A — 스크립트 (권장): 동기화 + 서버 실행 + 브라우저 자동 오픈
./run.ps1

# 방법 B — uv 직접
uv sync
uv run geoguesshelper-server        # → http://localhost:8799/ 자동 오픈 (사용 중이면 다음 빈 포트)
```

`uv` 만 있으면 된다(`winget install astral-sh.uv`). **키가 없어도 서버는 뜨고 링크 "추출"은 동작**한다.
인터랙티브 2분할 지도/로드뷰·캡처·분석을 쓰려면 아래 키를 `.env` 에 넣는다.

### 캡처·분석까지

`./run.ps1` 은 **기본으로 캡처(Pillow·Playwright)+분석(anthropic) 을 모두 설치**하고 헤드리스
Chromium 까지 받아둔다. 링크 추출만 쓸 거면 `-Minimal` 로 가볍게 띄운다.

```powershell
./run.ps1                 # 기본: 전체(map+capture+analyze) — 헤드리스 Chromium 자동 설치
./run.ps1 -Minimal        # 추출만(base) — Playwright/Pillow/anthropic 생략
# 수동 등가:
uv sync --extra all
uv run --extra all playwright install chromium
```

> `uv sync`/`uv run` 은 기본이 base 의존성이라, 그냥 `uv sync` 를 하면 capture/analyze extra 가
> **제거**된다. 그래서 launcher 는 sync·run 모두 `--extra all` 로 돈다.

---

## 키 설정 (`.env`)

`.env.example` 을 `.env` 로 복사하고 채운다.

| 변수 | 용도 | Cloud Console 설정 |
|---|---|---|
| `GOOGLE_MAPS_JS_API_KEY` | 브라우저의 2분할 로드뷰/지도 | **Maps JavaScript API** 활성화 · **HTTP 리퍼러** 제한 → `http://localhost:8799/*`, `http://127.0.0.1:8799/*` (실제 포트는 시작 배너 참조) |
| `GOOGLE_MAPS_STATIC_KEY` | 서버측 캡처(정적 이미지 합성) | **Street View Static API** + **Maps Static API** 활성화 · **IP** 제한(또는 개발 중 무제한). 비우면 JS 키로 폴백 |
| `ANTHROPIC_API_KEY` | Claude 비전 분석 | — |

> **왜 키가 두 개인가.** HTTP 리퍼러 제한은 브라우저 요청만 검사하므로, 리퍼러 제한 키는 서버측 Static/Metadata GET 에서
> 동작하지 않는다. 그래서 브라우저용(리퍼러 제한)·서버용(IP 제한) 키를 분리한다.
>
> **Static API 없이도 캡처된다.** Street View/Maps Static API 를 프로젝트에 활성화하지 않았어도,
> `GOOGLE_MAPS_JS_API_KEY` 만 있으면 **브라우저 렌더(헤드리스 Chromium)** 로 캡처한다. 기본 캡처 방식은
> **자동**: 정적(Static)을 먼저 시도하고, 키/프로젝트 설정으로 막히면(`REQUEST_DENIED` 등) 브라우저 렌더로 폴백한다.

---

## 사용 흐름

1. 링크 붙여넣기(📋 클립보드 버튼 또는 직접) → **추출** (Enter)
   - 링크마다 **폴더 탭**이 생긴다. 여러 링크를 동시에 열어두고 탭으로 전환하며, 각 탭은 자기만의 포즈·앨범·분석·보고서를 따로 보관한다. 탭 **×**로 닫는다.
2. 위 **로드뷰** / 아래 **지도**(hybrid) 2분할 — 로드뷰에서 화살표/드래그로 이동하면 지도가 따라온다
3. **현재 장면 캡처** → 우측 앨범에 스틸이 쌓인다(로드뷰+지도 합성). 캡처 방식은 *자동/정적/브라우저 렌더* 선택 가능.
   캡처는 화면 로드뷰 패널과 **같은 종횡비·같은 시야**로 담긴다 — 화면에 보이는 캔버스 전체가 보고서 이미지가 된다.
   (Static API 는 수평 120° 상한이 있어, 그보다 넓은 화면은 자동 모드가 브라우저 렌더를 먼저 쓴다.)
4. 보고서까지 세 갈래:
   - **⚡ 바로 보고서 추출**(포즈 카드) — 지금 화면 캡처 → 분석 → 보고서 한 번에
   - **📄 선택 장면 보고서 생성**(앨범) — 캡처들을 분석+보고서
   - **분석** → **📄 보고서 생성**(분석 카드) — 이미 분석한 결과를 보고서로
   결과는 `docs/report/country/{국가ISO2}/*.html`(이미지 임베드 자기완결형) — 보고서가 60건을 넘어 국가 폴더로 나눴고, 국가 폴더는 `country/` 아래로 모았다(git 은 원자만 공유하고 `docs/report/*` 는 무시).
   지식 저장소·잡 로그·뷰어는 파일명(basename)만 기억하고 `config.find_report()` 가 폴더를 찾는다.

### 언어 · 웹 리서치 (헤더)
- **🌐 보고서 언어** — 한국어(기본)·English·日本語·中文·Español·Français·Deutsch. **여러 개 체크**하면
  **한 HTML 파일에 언어별 섹션이 모두 담기고 상단에 언어 스위처**가 붙는다(맨 위 = 기본 표시 언어).
  캡처 이미지는 파일에 한 번만 임베드(공유)돼 용량이 붙지 않는다. 분석 서술도 선택 언어로 나온다.
- **파일명** — `report_{국가ISO}_{도시 또는 더 유명한 랜드마크}_{시작위도}_{시작경도}_{날짜}_{시각}_{언어}.html`.
  도시/랜드마크 식별자는 Claude 가 ASCII 로 정하고(도시가 기본, 랜드마크가 더 유명하면 랜드마크), 시작 좌표는 추출 원점이다.
- **🔎 웹 리서치 심화**(기본 켬) — 식별한 도시를 **Claude 웹 검색**으로 조사해, 보고서에 *경제·산업·문화/역사·관광·주요 인물·인접 도시와의 비교/경쟁·장단점·주민 특징 + 출처*를 담은 **도시 프로파일**을 추가한다. (검색 호출이라 리포트당 수십 초·비용 추가 — 끄면 이미지 분석만.)
- **번역 사후 수리** — 번역 청크가 일시 오류(레이트리밋 등)로 죽어 언어 탭에 영어가 남은 보고서는
  `uv run geoguesshelper retranslate <파일|폴더> -l ko [--dry-run]` 으로 제자리 수리한다(원본 `.bak` 보존).

### 지식 저장소 · 아틀라스 (CLI)
보고서마다 **지식 원자**(`docs/knowledge/atoms/*.md`)가 쌓이고, 다음 보고서가 그것을 회상해 재조사를 줄인다.
쌓인 원자는 하위 프로젝트 [`altaiya/`](altaiya/README.md)(지식 아틀라스 — 행위자·관계·시대·강역 지도)가 읽는다.

| 명령 | 하는 일 |
|---|---|
| `uv run geoguesshelper ingest [--apply] [--redo]` | 보고서↔지식 노트 대사(doctor). 예산 초과로 적재가 생략된 보고서를 잡 로그의 분석 결과로 사후 적재. 기본은 모의 실행 |
| `uv run geoguesshelper atlas-report -t travel -r sphere:<key> -l ko -l en` | **어드바이저 보고서** — 아틀라스 슬라이스(테마 × 범위)를 원자만으로 서술 → `docs/atlas/`(git 공유). 웹 검색·지식 적재 없음(비순환). 서버는 `POST /api/jobs/atlas-report` |
| `uv run geoguesshelper expand [--dry-run]` | 원자 전방위 확장(worldwide/people/counterpart) — 보고서 유래 원자만 씨앗 |
| `uv run geoguesshelper wiki [--dry-run]` | 위키피디아 리스트(시대·왕조·제국·국가) → 원자 |
| `uv run geoguesshelper clean [--apply]` | 캡처·임시파일·로그 정리(기본 모의 실행) |

원자가 늘면 아틀라스 사이드카를 **`entities → relations → eras` 순서로** 증분 추출하고(`altaiya/README.md`), `altaiya/sync-knowledge.ps1` 로 배포 스냅샷을 갱신한다.
다음 기획: [원자 밀도와 지도 디테일 (260829)](docs/plan/atom-density-map-detail_260829.html) — 범주 체계·보고서당 원자 20+·줌 LOD·공개 DEM 지형.

### 자동 정정 루프 · 원자 대화 · 관측소 (260907)
사람 승인 관문 없이 저장소가 **실측으로 스스로 고친다**: 캡처의 실측 pano 좌표를 가리고(로드뷰 패널만) "이 이미지는 X" 를 판단 →
실측·지도 포함 분석으로 "X 는 사실 X2 였다" 를 정정 → **판별자 원자**(`kind=discriminator`, `confusions=["X>X2"]`) 적재 →
다음 blind 판단이 X 를 추측하면 회상이 그 원자를 끌어와 2패스에서 재판단한다. 회상돼 쓰인 원자는 정답/오답 기여로 `hits/misses` 가 채점되고,
오답만 뒷받침한 원자는 `status=retracted`(회상 제외, 삭제 아님) 로 내려간다. 보고서 잡이 끝나면 정정 잡이 자동으로 뒤따른다(`GEOHELPER_CORRECTION_OFF=1` 로 끔).
결정 배경: [원자 대화 기획 (260906)](docs/plan/atom-dialogue_260906.html) §제안→원자의 "변경 · 2026-09-07" · 명세 [`impl-spec_260907.md`](docs/plan/impl-spec_260907.md).

| 명령 | 하는 일 |
|---|---|
| `uv run geoguesshelper correct run [--limit N] [--job ID] [--redo] [--dry-run]` | 정정 루프 — image_panos 가 있는 잡을 blind 2패스로 다시 판단·정정·판별자 적재 → `docs/knowledge/corrections/`(`corr_*.json` · `corrections.jsonl` · README 학습 곡선). `report` 는 README 재생성 |
| `uv run python -m geoguesshelper.dialogue ask --atom atm_… [--molecule mol_…] [--image …] [--effort medium] "질문"` | 원자·분자·이미지를 문맥으로 닫힌 세계 대화. 제안은 인용 ≥1 이면 곧바로 `kind=claim` 원자(사람 승인 없음). 서버는 `POST /api/dialogue`(본체·altaiya 둘 다) |
| `PYTHONUTF8=1 PYTHONPATH=src python -m geoguesshelper.observe build [--sweep]` | 관측 사이드카(`docs/knowledge/observe/`) — 원자별 파생 사실·시계열·2D 투영·이웃·근중복·분자 계보·신호 10개. LLM 0. 시스템 Python(numpy) |
| `powershell -File snapshot-knowledge.ps1 [-SkipEmbed] [-SkipSync]` | 적재가 원자를 남긴 날 한 번: `molecule embed → build → observe build → altaiya\sync-knowledge.ps1`. 원자를 갱신하지 않고 분자·관측·배포 **세 겹의 스냅샷**을 고정한다 |
| `uv run geoguesshelper baseline run\|report` · `molecule embed\|build\|name\|reify` | 평가 기준선(blind vs aided, `docs/knowledge/baseline/`) · 분자(임베딩 극대 클리크, `docs/knowledge/molecule/`) |

**관측소(뷰어)** — altaiya 서버의 두 번째 화면 `http://localhost:8901/observe.html` (`altaiya/run.bat`). 계기판(신호등·성장·정정 루프 카드) · 원자 대장(1,300여 행, 패싯·서랍·이웃·회상 이력) · 임베딩 지도(스냅샷 헐 + 임계 슬라이더 미리보기 — 브라우저 클리크가 스냅샷 120/120 일치) · 분자 서가(계보·지속성·near-miss·빠진 원자 감시) · 작업대(근중복·미명명·미분류·정정 원장·비용). 서랍의 "대화" 버튼이 대화 API 로 이어진다. 기획: [관측소 (260907)](docs/plan/atom-observatory_260907.html).

## 아키텍처

```
src/geoguesshelper/
  server.py       FastAPI 로컬 웹서버 (/, /api/extract·capture·analyze·report·scene-report)
  linkresolver.py 단축링크 해제 + /@·api=1 파싱 → {lat,lng,pano,heading,pitch,fov}
  streetview.py   Street View Static + Static Maps + 무료 Metadata 커버리지 확인
  capture.py      캡처 라우터 — auto(정적→브라우저 폴백)/static/playwright
  render_google.py 헤드리스 Chromium 으로 StreetViewPanorama+지도 렌더 → 스크린샷(JS 키만 필요)
  analyze.py      Claude 비전(tool_use 구조화 출력) — 언어 지정, 개인 식별 금지 가드
  research.py     Claude 웹 검색(server tool)으로 도시 프로파일 조사 + 출처
  report.py       분석+리서치 → 다국어 자기완결형 HTML 리포트(이미지 base64 임베드)
  script.py       보고서 스크립트(초안 haiku → 검증 opus) — HTML 옆 .script.json
  translate.py    문자열 번역 파이프라인(harvest → translate → splice) · retranslate.py 사후 수리
  jobs.py         작업 큐(동시 실행 상한·SSE 진행·취소·시간 예산) · docs/jobs/jobs.jsonl
  knowledge.py    지식 저장소 — 원자 추출(ingest P1·P2·P3)·회상(recall, 회상 로그)·증거 채점(record_evidence)·
                  정정/대화 적재(ingest_corrections·ingest_claims)·엔티티/장소/보고서 노트·종합
  correction.py   자동 정정 루프 — blind 2패스 판단 → 실측 정정 → 판별자 원자 · docs/knowledge/corrections/
  dialogue.py     원자·분자·이미지 문맥의 닫힌 세계 대화 — 제안은 인용 관문만 거쳐 claim 원자로
  baseline.py     평가 기준선(blind vs aided) · molecule.py 임베딩→극대 클리크 분자 · observe.py 관측 사이드카
  expand.py       패스D 원자 전방위 확장 · wiki.py 패스W 위키피디아 인제스트 · categorize.py 범주 소급
  atlas_report.py 어드바이저 보고서 — altaiya 조립을 슬라이스해 원자만으로 서술
  llm.py          모델 라우팅(vision/fact/reason/draft)·스트리밍·예산 · tls.py 프록시 TLS
  cleanup.py      캡처·임시파일·로그 정리(모의 실행 기본, docs/knowledge 보호)
  cli.py          serve · extract · clean · retranslate · expand · categorize · wiki · ingest · atlas-report ·
                  baseline · molecule · correct · observe
  i18n.py         지원 언어 레지스트리 + 리포트 라벨/enum 현지화
  config.py       .env(python-dotenv) + Settings · report_subdir/find_report
  webui/          index.html · app.js · sphere.js · styles.css (2분할 SPA)
captures/         캡처 스틸(로컬·임시)
docs/report/country/{iso2}/  지점 보고서(국가 폴더, git 무시) · docs/atlas/ 어드바이저 보고서 · docs/civilization/ UI 리서치 · docs/research/ 시장 조사 · docs/theory/ 이론
docs/knowledge/   atoms·entities·places·reports 노트 · baseline/ 기준선 · molecule/ 분자 · corrections/ 정정 원장 ·
                  dialogue/ 대화 로그 · observe/ 관측 사이드카 · recall_log.jsonl · evidence_log.jsonl
docs/plan · docs/error · docs/implement   기획 · 오진 기록 · 구현 노트
snapshot-knowledge.ps1  분자·관측·배포 스냅샷 한 번에(임베딩은 시스템 Python·GPU)
altaiya/          지식 아틀라스 서브모듈(dalyulbam/altaiya) — 뷰어(index.html 지도 · observe.html 관측소)·추출기·사이드카·배포 스냅샷
```

설계·기술 근거(구글 API 표면, URL 파싱, ToS)는 [설계 문서](docs/plan/geoguesshelper-split-capture-plan_260708.html)에 정리돼 있다.

## 주의 (Google Maps Platform ToS · 프라이버시)

- **복기(study) 도구** — 라이브 지오게서 라운드 중 사용은 부정행위이며 계정 정지 사유.
- 스트리트뷰 **이미지는 캐시/저장/대량다운로드 불가**. 저장 가능한 식별자는 `pano_id`(불안정)·`place_id` 뿐 —
  캡처는 **로컬·임시·비재배포**로만, durable key 는 lat/lng. 이미지의 Google 로고/귀속은 크롭하지 않는다.
- 얼굴·번호판은 Google 이 블러 처리 — 분석은 장면·지리에 한정하고 **개인을 식별하지 않는다**.
