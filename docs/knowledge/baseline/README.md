# 평가 기준선 — blind(로드뷰만) vs aided(하단 지도 포함)

생성 2026-09-07 09:47 · 잡 14건 · 총비용 $8.977 · 기획: docs/plan/atom-dialogue_260906.html §8.1 (평가 기준선 오염)

캡처 하단 지도는 zoom 15 + 정답 좌표 빨간 마커다. 같은 캡처를 (1-1) 로드뷰 패널만 잘라서, (1-2) 원본(지도 포함)으로 두 번 분석하고 추론 사슬·단서·원자를 대조했다. 두 모드의 원자는 각각 `store_blind/`, `store_aided/` 샌드박스에 적재됐고(본 저장소 무변경), 계층은 `tiers.json` 에 있다.

## 집계

| 지표 | blind | aided | aided_prior(원 보고서) |
|---|---|---|---|
| 국가 적중률 | 1.0 | 1.0 | 1.0 |
| 좌표 오차 km 중앙값 | 1.54 | 1.47 | 1.65 |
| 좌표 오차 km 평균 | 15.07 | 9.57 | 9.59 |
| 도달 level 분포 | {'district': 13, 'admin_region': 1} | {'district': 14} | {'district': 14} |
| ruled_out 있는 단계 평균 | 5.29 | 5.29 | 5.36 |
| 단서 수 평균 | 10.5 | 10.14 | 10.0 |

감사 판정 분포(logic_delta.verdict): {'map-leaked-answer': 8, 'blind-sufficient': 2, 'map-helped': 4}

## 추론 계층 (원자)

| 계층 | 정의 | 개수 |
|---|---|---|
| **unaided** | blind 에서도 나온 사실 — 로드뷰만으로 추론되는 일반 추론 | 191 |
| **aided** | aided 에서만 나온 사실 — 보조 지식(지도)이 있어야만 추론 | 22 |
| **blind-only** | blind 에서만 나온 관찰 — 지도가 있으면 사라지는 관찰 | 24 |

레이어별: {"economy": {"unaided": 41, "aided": 3, "blind-only": 5}, "geography": {"unaided": 23, "aided": 4, "blind-only": 5}, "architecture": {"unaided": 76, "aided": 11, "blind-only": 8}, "culture": {"unaided": 27, "aided": 3, "blind-only": 3}, "nature": {"unaided": 16, "blind-only": 3}, "language": {"unaided": 6}, "history": {"unaided": 2, "aided": 1}}

## 잡별 문서

- [선택 장면 보고서 · lJp_ScSR…EhbQ](job_1a2be2d9d7ff.md) — blind Spain/district/46.07km · aided Spain/district/0.51km · `map-leaked-answer`
- [선택 장면 보고서 · EsDBrGtU…4acg](job_41090529fe0b.md) — blind Bolivia/district/1.08km · aided Bolivia/district/1.47km · `map-leaked-answer`
- [선택 장면 보고서 · zkbMo2Ub…nUhw](job_5f8644e78b2c.md) — blind Japan/district/40.36km · aided Japan/district/7.11km · `map-leaked-answer`
- [선택 장면 보고서 · ZexpLPkD…L-2g](job_72caf482d49b.md) — blind South Africa/district/0.98km · aided South Africa/district/0.64km · `blind-sufficient`
- [선택 장면 보고서 · BXnBEWDj…uzig](job_773111a6092d.md) — blind Kazakhstan/admin_region/102.12km · aided Kazakhstan/district/101.74km · `map-leaked-answer`
- [선택 장면 보고서 · teQ0fXUd…Lt9w](job_81dd4684ad63.md) — blind Czechia/district/7.66km · aided Czechia/district/11.93km · `map-leaked-answer`
- [선택 장면 보고서 · JbcjjIxd…-t9g](job_8740554c450d.md) — blind Germany/district/4.94km · aided Germany/district/0.07km · `map-leaked-answer`
- [선택 장면 보고서 · UjotVXSN…G4lw](job_950227aac52b.md) — blind Italy/district/0.11km · aided Italy/district/0.17km · `blind-sufficient`
- [선택 장면 보고서 · v7Bz2r-j…2NJA](job_959e71524c52.md) — blind Iceland/district/0.42km · aided Iceland/district/0.44km · `map-helped`
- [선택 장면 보고서 · l3Fnkxim…sN-g](job_b2b72f38b424.md) — blind Botswana/district/1.24km · aided Botswana/district/2.51km · `map-leaked-answer`
- [선택 장면 보고서 · 2lCOoIZN…srCA](job_cab0458cd29c.md) — blind Finland/district/0.25km · aided Finland/district/0.3km · `map-helped`
- [선택 장면 보고서 · 9e4BujI9…O-1A](job_da4b902eaf1e.md) — blind Jordan/district/1.54km · aided Jordan/district/2.5km · `map-helped`
- [선택 장면 보고서 · VlJB8vGX…jXEA](job_e1df2b6f7aeb.md) — blind Vietnam/district/3.46km · aided Vietnam/district/3.71km · `map-helped`
- [선택 장면 보고서 · AnMykD_h…iIKg](job_f374e57f1b56.md) — blind United Arab Emirates/district/0.81km · aided United Arab Emirates/district/0.89km · `map-leaked-answer`

## 재실행

```
uv run geoguesshelper baseline run            # image_panos 있는 잡 전부(이미 있는 결과는 건너뜀)
uv run geoguesshelper baseline run --limit 2  # 소규모
uv run geoguesshelper baseline report         # 문서·tiers 재생성(LLM 호출 없음)
```
