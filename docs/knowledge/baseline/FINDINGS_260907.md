# 평가 기준선 실험 — 해석 노트 (2026-09-07)

집계·잡별 문서는 `README.md`(자동 생성)에, 계층 판정 원장은 `tiers.json`에, 원문 분석은 `runs/<job>.json`에 있다.
이 노트는 숫자를 읽는 법과 그 함의를 사람이 적은 것이다. 기획 배경: `docs/plan/atom-dialogue_260906.html` §8.1, 이론: `docs/theory/molecule-theory_260907.html` §4.

## 무엇을 했나

`image_panos`(캡처별 실측 pano 좌표)가 있는 잡 14건의 캡처(각 5–6장)를 두 번 분석했다.

| 모드 | 입력 | 뜻 |
|---|---|---|
| **blind** (1-1) | 합성 이미지의 위쪽 로드뷰 패널만(`h·480/740`, 3200×1480 → 3200×960) | 지도 없이 무엇이 보이고 어디까지 추론되는가 |
| **aided** (1-2) | 원본 합성 이미지(하단 지도 = zoom 15 + 정답 빨간 마커 포함) | 보조 지식이 있을 때 추론이 어떻게 달라지는가 |
| aided_prior | 원 보고서 잡의 분석(다른 언어·과거 프롬프트) | 참고용 대조 |

같은 프롬프트(`analyze.py report_location`)·같은 모델(opus-5)·같은 언어(en). 두 결과를 각각 샌드박스 저장소 `store_blind/`, `store_aided/`에 P1(지점 단서)·P2(장소 사실)로 적재하고 — **본 저장소 `docs/knowledge/atoms/`는 건드리지 않았다** — 원자를 제목·본문·태그·범주 유사도로 1:1 대조해 계층을 매겼다. 마지막에 sonnet이 두 추론 사슬을 나란히 읽고 `logic_delta`(무엇이 달라졈나, 지도 없이는 불가능했던 단계, 판정)를 썼다. 총비용 $8.95.

## 숫자

| 지표 | blind | aided |
|---|---|---|
| 국가 적중 | **14/14** | **14/14** |
| 좌표 오차 중앙값 | 1.54 km | 1.47 km |
| 좌표 오차 평균 | 15.07 km | 9.57 km |
| 오차 > 10 km | 3건 (ES 46, JP 40, KZ 102) | 2건 (CZ 12, KZ 102) |
| 도달 level = district | 13/14 | 14/14 |
| ruled_out 있는 단계 평균 | 5.29 | 5.29 |
| 감사 판정 | — | map-leaked-answer **8** · map-helped 4 · blind-sufficient 2 |

원자 계층(두 저장소 대조, `tiers.json`):

| 계층 | 정의 | 개수 |
|---|---|---|
| **unaided** | blind 에서도 나온 사실 — 로드뷰만으로 추론되는 일반 추론 | **191** |
| **aided** | aided 에서만 나온 사실 — 지도(보조 지식)가 있어야만 나온 것 | **22** (9%) |
| **blind-only** | blind 에서만 나온 관찰 — 지도가 있으면 사라진 관찰 | **24** |

## 읽는 법 — 세 가지 결론

### 1. 국가·지역은 로드뷰만으로 이미 끝난다
14건 모두 blind 에서 국가를 맞혔고 13건이 district 까지 내려갔다. 이유는 로드뷰 패널이 **지명을 직접 준다**는 데 있다 — 'HÓTEL HVAMMSTANGI' 간판, 'BUCO UITENHAGE' 상호, 'Heilbrigðisstofnun Vesturlands' 기관 브랜딩, 041 전화 국번. 즉 지금 캡처 코퍼스에서 "지도가 정답을 알려준다"는 오염은 **국가·지역 수준의 판별력을 부풀리지 않는다**. 판별 사슬의 앞 4단계(continent→country→macro_region→admin_region)는 blind 와 aided 가 거의 같은 관찰·같은 배제로 닫힌다(잡별 문서의 두 사슬 표 참조).

### 2. 지도는 "마지막 1 km"와 "실패의 구제"에만 기여한다
오차 중앙값은 1.54 vs 1.47 km 로 사실상 같다. 차이는 꼬리다: 스페인(푸에르테벤투라) 46 km → 0.5 km, 일본(교토 북부) 40 km → 7 km, 독일(뮌헨) 4.9 km → 0.07 km. 반대로 카자흐스탄(Beyneu)은 지도가 있어도 102 km — 지도 라벨이 있어도 모델이 좌표로 옮기지 못했다. 체코는 지도가 있는 쪽이 더 나빴다(7.7 → 11.9 km). **지도의 이득은 평균을 끌어내리는 소수 사례에 집중되고, 대부분의 사례에서는 결론을 바꾸지 않는다.**

### 3. 그런데도 감사자는 14건 중 8건을 "지도가 답을 흘렸다"로 판정했다
모순처럼 보이지만 아니다. 감사자(`logic_delta`)는 **결론**이 아니라 **정당화**를 본다. aided 사슬의 metro_area/district 단계가 "map inset shows road 711", "map overlay names FV-2", "map labels QL37 near Thuong Dinh" 같은 **지도 라벨을 관찰로 인용**하면, 결론이 같아도 그 단계의 판별자는 로드뷰 증거가 아니다. 이것이 기획서 §8.1이 말한 "정답을 본 뒤의 사후 서술"의 실체다 — 답이 틀려지는 문제가 아니라 **판별자가 학습 재료로서 오염되는** 문제다.

그 오염은 원자로 굳는다. aided 22건 중 5건이 문자 그대로 지도 라벨이다:

- `Map inset showing Route 711 through town` (IS)
- `Map overlay naming FV-2 road and local landmarks` (ES)
- `Map labels for QL37 corridor near Thuong Dinh` (VN)
- `Map panel with Munich district place names` (DE)
- `Map label for Beyneu settlement` (KZ)

이 다섯은 P1 지점 단서 원자로 저장됐다 — 회상되면 다음 분석 프롬프트에 "이 셀에는 Route 711 지도 라벨이 있다"가 **확립된 사실**로 실린다. 본 저장소에는 아직 없지만(P1 이 본 저장소에서 실행된 적이 없다), 실행하면 그대로 들어간다.

나머지 aided 17건은 다른 성격이다: 'Turku as historically Swedish-influenced', 'Kitayama forestry landscape north of Kyoto', 'Canary Islands road-code prefixes identify individual islands', 'Bavarian mansard roofscape'. 이것들은 **관찰이 아니라 회상**이다 — 지도가 장소를 알려준 뒤 모델이 기억에서 꺼낸 장소 지식. 사실로서는 대체로 맞지만 "이 캡처에서 봤다"는 P1 원자의 전제(`knowledge.py` P1 규칙: 관찰의 재구조화, 재분석 금지)와 어긋난다. 따라서 aided 층은 지각 층이 아니라 **지식 인출 층**이고, 이미지 좌표(point 스코프)에 앵커되면 안 된다.

## blind-only 가 말하는 것
지도가 없을 때만 나온 24건은 두 부류다. (a) 세밀한 물리 관찰 — 'Faded white markings on older asphalt', 'White-poled lantern streetlights at a Y-junction', 'Nordic-style blue pedestrian crossing sign on grey aluminium post': 지도가 있으면 모델이 **덜 본다**(주의가 라벨로 간다). (b) blind 가 지명을 맞힌 뒤 꺼낸 회상 — 'Thai Nguyen Province industrial growth linked to Samsung': aided 층과 같은 성격이나 로드뷰 간판으로 도달했다는 점이 다르다. (a) 는 GeoGuessr 판별자로서 정확히 우리가 원하는 종류이고, blind 모드가 그것을 더 많이 낸다.

## 함의 — 파이프라인에 바로 적용할 것

1. **cue 에 `panel` 필드**(`roadview | map`)를 두고, `map` 유래 cue 는 P1 재료에서 제외한다(기획서 §8.9 0단계). 위 5건이 재발하지 않게 하는 최소 조치.
2. **분석 입력을 blind 로 바꾸는 것은 아직 이르다** — 꼬리 3건의 구제와 도달 level 14/14 는 지도의 실제 기여다. 대신 **2패스**: blind 로 사슬을 만들고, 지도(또는 좌표)는 사후 검증·좌표 스냅에만 쓴다. 그러면 판별자는 로드뷰 증거만 인용하고, 좌표는 정답에서 온다 — "정답은 좌표가 안다, 대화는 왜인가를 캔다"의 파이프라인판.
3. **"프롬프트 강화가 효과 있었다"의 정의**를 blind 모드의 `오차 > 10 km 건수`와 `district 도달률`로 고정한다. 중앙값은 이미 1.5 km 라 움직일 여지가 없다.
4. **회상 원자에 `tier` 를 싣는다** — P1·P2 적재 시 unaided/aided 를 매길 수 있으면(blind 패스가 있으면 자동), `recall()` 은 aided 원자를 장면 분석 프롬프트에서 제외한다.

## 한계
- 14건, 12개국, 캡처당 최대 6장 — 표본이 작고 앨범 보고서 경로에 편향돼 있다.
- 계층 매칭은 텍스트 유사도(임계 0.40, 첫 건으로 보정)다. 같은 사실이 다른 어휘로 쓰이면 unaided 가 aided/blind-only 로 새고, 반대로 다른 사실이 같은 어휘로 쓰이면 합쳐진다. `tiers.json` 의 `sim` 값으로 경계 사례를 확인할 수 있다.
- `logic_delta` 는 sonnet 한 번의 판정이다. 남아프리카 1건은 sonnet 이 두 번 모두 첫 필드만 반환해 opus(verify 역할)로 다시 받았다(판정 blind-sufficient) — 도구 입력이 잘리는 결함은 molecule 명명에서도 21% 로 재현됐다(`docs/knowledge/molecule/README.md`).
- blind 크롭은 합성 비율(480:260)에 의존한다. 다른 뷰포트로 찍은 캡처가 섞이면 경계가 어긋날 수 있다(현재 코퍼스는 전부 같은 비율).
