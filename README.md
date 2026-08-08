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
3. **현재 장면 캡처** → 우측 앨범에 스틸이 쌓인다(로드뷰+지도 합성). 캡처 방식은 *자동/정적/브라우저 렌더* 선택 가능
4. 보고서까지 세 갈래:
   - **⚡ 바로 보고서 추출**(포즈 카드) — 지금 화면 캡처 → 분석 → 보고서 한 번에
   - **📄 선택 장면 보고서 생성**(앨범) — 캡처들을 분석+보고서
   - **분석** → **📄 보고서 생성**(분석 카드) — 이미 분석한 결과를 보고서로
   결과는 `docs/report/*.html`(이미지 임베드 자기완결형).

### 언어 · 웹 리서치 (헤더)
- **🌐 보고서 언어** — 한국어(기본)·English·日本語·中文·Español·Français·Deutsch. **여러 개 체크**하면
  **한 HTML 파일에 언어별 섹션이 모두 담기고 상단에 언어 스위처**가 붙는다(맨 위 = 기본 표시 언어).
  캡처 이미지는 파일에 한 번만 임베드(공유)돼 용량이 붙지 않는다. 분석 서술도 선택 언어로 나온다.
- **파일명** — `report_{국가ISO}_{도시 또는 더 유명한 랜드마크}_{시작위도}_{시작경도}_{날짜}_{시각}_{언어}.html`.
  도시/랜드마크 식별자는 Claude 가 ASCII 로 정하고(도시가 기본, 랜드마크가 더 유명하면 랜드마크), 시작 좌표는 추출 원점이다.
- **🔎 웹 리서치 심화**(기본 켬) — 식별한 도시를 **Claude 웹 검색**으로 조사해, 보고서에 *경제·산업·문화/역사·관광·주요 인물·인접 도시와의 비교/경쟁·장단점·주민 특징 + 출처*를 담은 **도시 프로파일**을 추가한다. (검색 호출이라 리포트당 수십 초·비용 추가 — 끄면 이미지 분석만.)

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
  i18n.py         지원 언어 레지스트리 + 리포트 라벨/enum 현지화
  config.py       .env(python-dotenv) + Settings
  webui/          index.html · app.js · styles.css (2분할 SPA)
captures/         캡처 스틸(로컬·임시)      docs/plan · docs/report
```

설계·기술 근거(구글 API 표면, URL 파싱, ToS)는 [설계 문서](docs/plan/geoguesshelper-split-capture-plan_260708.html)에 정리돼 있다.

## 주의 (Google Maps Platform ToS · 프라이버시)

- **복기(study) 도구** — 라이브 지오게서 라운드 중 사용은 부정행위이며 계정 정지 사유.
- 스트리트뷰 **이미지는 캐시/저장/대량다운로드 불가**. 저장 가능한 식별자는 `pano_id`(불안정)·`place_id` 뿐 —
  캡처는 **로컬·임시·비재배포**로만, durable key 는 lat/lng. 이미지의 Google 로고/귀속은 크롭하지 않는다.
- 얼굴·번호판은 Google 이 블러 처리 — 분석은 장면·지리에 한정하고 **개인을 식별하지 않는다**.
