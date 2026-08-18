# ALTAIYA — 지식 아틀라스 (프로토타입)

`docs/knowledge/`에 쌓인 지식 원자를 **지도 위의 행위자·관계 그래프**로 보여준다.
기획: [`docs/plan/knowledge-atlas_260816.html`](../docs/plan/knowledge-atlas_260816.html) (v2 — 거점·행위자·관계의 전략 지도).

## 실행 — 딸깍 한 번

```
altaiya\run.bat        ← 더블클릭 (또는 run.ps1)
```

- **backend** `http://localhost:8901` — FastAPI. 사이드카 + index.json → 행위자·관계 그래프 조립 (읽기 전용)
- **frontend** `http://localhost:8902` — 전략 지도 (정적, 빌드 도구 없음)
- 창 두 개가 뜨고 브라우저가 자동으로 열린다. 서버 창을 닫으면 종료.
- 이미 떠 있으면 재사용한다(중복 실행 안 함). 포트는 `ALTAIYA_API_PORT` / `ALTAIYA_WEB_PORT` 로 변경.

## 화면

| 조작 | 동작 |
|---|---|
| 휠 / ＋－ 버튼 | 줌 (커서 기준) |
| 드래그 | 팬 · `⤢` 전체 보기 |
| 행위자 호버 | 에고 네트워크 강조 + 툴팁 |
| 행위자 클릭 | 상세 패널: 테마 신호 · 관계(유형·방향·인용 원자 수) · 원자 원문 · 근거 보고서 링크 |
| 관계선 호버 | 관계 유형·방향·시기 · 출처 레이어 · 인용 원자 수 |
| 테마 칩 | 7테마 렌즈 — 판에 남는 행위자·관계가 바뀐다 (칩의 숫자 = 해당 렌즈의 행위자 수) |
| 검색 | 행위자 이름/슬러그 — 선택하면 날아가서 연다 |
| Esc / 빈 지도 클릭 | 패널 닫기 |

## 추출 — 패스A·B (기획서 1·2단계)

원자는 **읽기만** 한다. 추출 결과는 사이드카 두 개로 떨어지고, 조립은 그것을 정본으로 쓴다.

```
uv run --project E:\GeoguessHelper python altaiya\altaiya-backend\extract.py entities   # 패스A 대장
uv run --project E:\GeoguessHelper python altaiya\altaiya-backend\extract.py relations  # 패스B 관계
uv run --project E:\GeoguessHelper python altaiya\altaiya-backend\extract.py sample -n 20   # 검수 표본
uv run --project E:\GeoguessHelper python altaiya\altaiya-backend\extract.py stats
```

- 모델은 본체 설정의 `model_fact`(claude-sonnet-5), 호출은 `llm.py` 재사용 — 별도 키·클라이언트 없음.
- **증분**이다. 대장에 있는 슬러그·이미 처리한 원자는 건너뛴다. 전면 재추출은 `--redo`,
  파일럿은 `--limit N`, 입력만 보려면 `--dry-run`.
- 실측(2026-08-19): 대장 288행위자 $0.66 · 관계 183원자 → 189엣지 $0.71.

## 정직성 규칙

- **관계는 원자가 명시적으로 서술한 것만.** 함께 언급됐다는 이유로 잇지 않는다.
  동시 서술 쌍 413개 중 관계로 인정된 것은 189개이고, 나머지 230개는 그리지 않는다
  (`meta.comention_uncovered`). **공백은 "관계 없음"이 아니라 "아직 근거 없음"이다.**
- **전 엣지가 원자를 인용한다.** 추출 때 원자 본문에서 복사한 근거 구절을 받아
  본문에 실재하는지 대조하고, 없으면 폐기한다. 구절 자체는 그래프에 저장하지 않고
  `data/relations.audit.jsonl` 에 검수용으로만 남긴다(기획서 규칙).
- **타입으로 유형을 후검한다.** "회사가 도시를 운영한다" 같은 것은 대장 타입으로 걸러
  버리지 않고 `related` 로 강등한다(추출 손실 방지). 강등 내역은 `meta.last_demoted`.
- **분류 불가는 `related`.** 유형을 지어내는 것보다 인용이 붙은 약한 유형이 낫다.
- 사이드카가 없으면 조립은 `rules.py` 휴리스틱 + 동시 서술로 폴백한다. 어느 쪽인지는
  `GET /api/atlas` 의 `meta.source` 가 밝힌다.
- 지식이 늘면(새 보고서 → 원자) `extract.py` 를 다시 돌리고(증분) `GET /api/rebuild`.

## 구조

```
altaiya/
  run.ps1 · run.bat        원클릭 런처
  altaiya-backend/
    app.py                 FastAPI (8901): /api/atlas /api/actor/{slug} /api/atom/{id} /reports/{name}
    extract.py             패스A·B 추출기 (LLM) · 검수 표본 · 현황
    atlas.py               사이드카 + index.json → 그래프 조립 (읽기 전용)
    rules.py               테마 가중 행렬 · 타입 휴리스틱(폴백)
  altaiya-frontend/
    index.html · app.js · styles.css
    world.geo.json         세계 해안선 (johan/world.geo.json, 퍼블릭 데이터)
  data/
    entities.json          패스A 대장 — 슬러그 → 타입·한국어 표기·앵커·테마
    relations.json         패스B 관계 — 유형·방향·시기·인용 원자
    relations.audit.jsonl  근거 구절 로그 (검수 전용)
```

의존성은 본체 uv 프로젝트(fastapi·uvicorn·anthropic)를 그대로 쓴다 — 별도 설치 없음.
