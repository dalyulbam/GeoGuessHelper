# ALTAIYA — 지식 아틀라스 (프로토타입)

`docs/knowledge/`에 쌓인 지식 원자를 **지도 위의 행위자·관계 그래프**로 보여준다.
기획: [`docs/plan/knowledge-atlas_260816.html`](../docs/plan/knowledge-atlas_260816.html) (v2 — 거점·행위자·관계의 전략 지도).

## 실행 — 딸깍 한 번

```
altaiya\run.bat        ← 더블클릭 (또는 run.ps1)
```

- **backend** `http://localhost:8901` — FastAPI. index.json → 행위자·관계 그래프 조립 (읽기 전용)
- **frontend** `http://localhost:8902` — 전략 지도 (정적, 빌드 도구 없음)
- 창 두 개가 뜨고 브라우저가 자동으로 열린다. 서버 창을 닫으면 종료.
- 이미 떠 있으면 재사용한다(중복 실행 안 함). 포트는 `ALTAIYA_API_PORT` / `ALTAIYA_WEB_PORT` 로 변경.

## 화면

| 조작 | 동작 |
|---|---|
| 휠 / ＋－ 버튼 | 줌 (커서 기준) |
| 드래그 | 팬 · `⤢` 전체 보기 |
| 행위자 호버 | 에고 네트워크 강조 + 툴팁 |
| 행위자 클릭 | 상세 패널: 테마 신호 · 관계(인용 원자 수) · 원자 원문 · 근거 보고서 링크 |
| 관계선 호버 | 두 행위자와 출처 레이어 · 동시 서술 원자 수 |
| 테마 칩 | 7테마 렌즈 — 판에 남는 행위자·관계가 바뀐다 (칩의 숫자 = 해당 렌즈의 행위자 수) |
| 검색 | 행위자 이름/슬러그 — 선택하면 날아가서 연다 |
| Esc / 빈 지도 클릭 | 패널 닫기 |

## 정직성 규칙

- **관계선 = 원자 동시 서술.** 유형(공급·통치 등)을 지어내지 않는다 — 색은 출처 원자의 레이어이고,
  모든 선은 인용 원자를 갖는다(패널에서 원문 확인).
- **타입·테마는 휴리스틱 근사.** `altaiya-backend/rules.py`의 큐레이션+키워드 규칙과 레이어→테마 행렬.
  LLM 정밀 추출(기획서 패스 A·B)은 후속 단계.
- 지식이 늘면(새 보고서 → 원자) `GET /api/rebuild` 또는 백엔드 재시작으로 반영.

## 구조

```
altaiya/
  run.ps1 · run.bat        원클릭 런처
  altaiya-backend/
    app.py                 FastAPI (8901): /api/atlas /api/actor/{slug} /api/atom/{id} /reports/{name}
    atlas.py               index.json → 그래프 조립 (읽기 전용)
    rules.py               타입 분류·테마 가중 (큐레이션 지점)
  altaiya-frontend/
    index.html · app.js · styles.css
    world.geo.json         세계 해안선 (johan/world.geo.json, 퍼블릭 데이터)
```

의존성은 본체 uv 프로젝트(fastapi·uvicorn)를 그대로 쓴다 — 별도 설치 없음.
