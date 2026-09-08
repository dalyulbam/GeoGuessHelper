---
{"job": "job_cab0458cd29c", "report": "report_fi_turku_60.4575_22.2881_260906_205820_ko-en-fr.html", "truth": {"lat": 60.454397169950376, "lng": 22.282570579498596, "iso": "FI", "file_lat": 60.4575, "file_lng": 22.2881, "n_panos": 11}, "lang": "en", "cost_usd": 0.6473, "created": 1788741190.640669}
---

# 선택 장면 보고서 · 2lCOoIZN…srCA — blind vs aided

보고서 `report_fi_turku_60.4575_22.2881_260906_205820_ko-en-fr.html` · 캡처 6장 · 정답(pano 평균) 60.454397169950376, 22.282570579498596 · ISO FI

## 지표

| | blind (로드뷰만) | aided (지도 포함) | aided_prior (원 보고서) |
|---|---|---|---|
| 국가 | Finland (FI) | Finland (FI) | Finland |
| 국가 적중 | True | True | True |
| 지역 / 도시 | Southwest Finland (Varsinais-Suomi) / Turku | Southwest Finland (Varsinais-Suomi) — Turku University / Kasarmialue quarter / Turku | Southwest Finland (Varsinais-Suomi) / Turku |
| 좌표 오차 km | **0.25** | **0.3** | 0.24 |
| 도달 level | district | district | district |
| 단계 수 (ruled_out 있는 단계) | 5 (5) | 5 (5) | 5 (5) |
| 단서 수 | 10 | 10 | 10 |
| 확신 | 0.66 | 0.93 | 0.93 |

## 로직 변화 (감사 서술)

**판정**: `map-helped`

**blind 의 추론**: The blind chain relies purely on street-level cues: forest and bedrock morphology to reach Fennoscandia, then Finnish company suffixes and bilingual signage to reach Finland, then the Swedish-line-present sign to argue for a bilingual coastal municipality, then the Unica branding and Aalto-style campus architecture to pin Turku, and finally streetscape morphology (bedrock ridge, wooden houses, campus slab) to localize to the university/hospital hill east of the Aura river.

**aided 의 추론**: The aided chain follows an almost identical evidentiary path — same warning-sign shape, same 'AL-Liikenne Oy' and bilingual parking sign, same Unica branding — reaching the same conclusions at continent, country, and cultural-sphere levels through matching discriminators. At the admin_region and district levels it becomes noticeably more specific and confident (naming Kasarmialue, Henrikinkatu-Vesilinnantie block, Yliopistonmäki/Vartiovuori) and raises confidence from 0.66 to 0.93, despite the described visual evidence at these stages being the same generic campus/wooden-house/rock-cutting imagery as the blind run.

**달라진 것**: Both chains use nearly identical discriminators for continent, country, and macro/cultural-sphere levels — the map added no new falsifiable evidence there. The divergence appears at admin_region/district: the aided chain names a specific quarter (Kasarmialue/Henrikinkatu-Vesilinnantie) and sharply increases confidence, without citing any street-level cue not already present in the blind chain. This suggests the fine-grained naming and confidence boost were anchored to the map marker rather than derived independently from the imagery.

**지도가 있어야만 가능했던 단계**:
- admin_region: 'Turku, Southwest Finland' with confident named campus
- district: precise naming of 'Kasarmialue / Henrikinkatu-Vesilinnantie block' and 'Yliopistonmäki/Vartiovuori terrain' as exact quarter

## blind 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | None | Birch-and-aspen mixed forest, glacially scoured mossy granite outcrops at street level, low sun, painted timber housing, right-hand traffic. | Central Europe would show plastered terraced streetfronts and no exposed glaciated bedrock; North America would show wide setbacks, wooden utility poles with transformers and different sign shapes. | Central Europe — no bedrock outcrops in urban street cuts, different housing stock, North America — European sign geometry and narrow street sections | **Northern Europe, Fennoscandian/Baltic sphere** |
| `country` | None | 'OY' company suffix, Finnish '0400' mobile prefix, and a bilingual Finnish-over-Swedish parking sign ('AUTOMAATTI / AUTOMAT'). | Sweden would use 'AB' for companies and 'Biljettautomat' monolingually; Estonia would use 'OÜ/AS' and no Swedish; Norway uses 'AS' and white-fill warning signs. | Sweden — Swedish would be the top/only line and 'AB' would replace 'Oy', Estonia — no Swedish co-official signage, different company suffix, Norway — white-fill warning triangles, 'AS' suffix | **Finland** |
| `macro_region` | None | Municipal parking sign carries a Swedish second line, i.e. the municipality is officially bilingual. | In Tampere, Jyväskylä or Kuopio the parking sign would be Finnish-only ('MAKSUAUTOMAATTI'); Swedish appears on municipal signs only where Swedish is co-official. | Interior monolingual Finnish cities — would not post Swedish on municipal parking signs | **A bilingual coastal municipality on the south/southwest shore** |
| `metro_area` | None | 'unica' branded window on a 1970s faculty block; adjacent white Aalto-idiom campus building on a lawn ridge; steep bedrock-cut street dropping into a low stone-and-render town centre. | In Helsinki the student-restaurant operator would be UniCafe/HYY, in Vaasa it would be a small local operator; Unica operates only for Turun yliopisto / Åbo Akademi. Vaasa/Porvoo also lack a hillside multi-faculty campus of this scale, and Helsinki's Kumpula/Viikki campuses do not sit on this kind of steep bedrock ridge with wooden quarters immediately below. | Helsinki — student catering would be branded UniCafe, Vaasa — no comparable hill campus, different operator, Porvoo/Kotka — no university campus of this type | **Turku (Åbo)** |
| `district` | None | Campus frames sit on a wooded ridge with a large institutional slab block behind; the wooden-house street slopes toward a distant dome/tower; the silo frame is an outlying yard. | A pure suburb would lack both the campus block and the dense wooden two-storey street wall; a pure historic quarter would not show the concrete faculty tower and Unica canteen. The mix of Aalto-school campus, adjacent hospital-scale slab block, and a wooden street sloping toward a domed silhouette fits the Yliopistonmäki–Kerttuli–Martti belt. | Purely peripheral suburb — campus and dense wooden streetscape both present, Old town core west of the river — buildings here are timber, not the stone empire-style core | **Turku, on and just below the university/hospital hill east of the Aura river, with the wooden-house and depot frames in the same city** |

## aided 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | None | Yellow-ground triangular warning sign, birch-dominated forest, exposed glaciated granite, right-hand traffic, drab modernist concrete. | Central Europe would show white-ground warning triangles and stucco terraces; North America would show wooden utility poles with crossarms, yellow diamond signs and no bilingual plates. | Central Europe — warning triangles there are white-ground with red border, North America — sign shapes, guardrail and vegetation mismatch | **Fennoscandian / Baltic Sea region** |
| `country` | None | 'AL-Liikenne Oy' on the silo; bilingual 'AUTOMAATTI / AUTOMAT' parking sign. | Sweden would show 'AB' company suffixes and monolingual Swedish signs; Estonia would use 'OÜ'/'AS' and no Swedish second line; Norway would show 'AS' and Bokmål text. | Sweden — company suffix would be AB and Finnish would not appear at all, Estonia — no Swedish co-official signage, different company suffix, Norway — no Finnish-Swedish bilingual pairs | **Finland** |
| `cultural_sphere` | None | Finnish is printed above Swedish on the official parking sign, and the Finnish line is the primary one. | In Åland or Swedish-majority Ostrobothnia the sign would read 'AUTOMAT / AUTOMAATTI' (Swedish first) or Swedish only; in monolingual interior Finland there would be no Swedish line at all. | Åland and Swedish-majority Ostrobothnia — language order would be reversed, Interior/northern Finland — signs would be monolingual Finnish | **Bilingual coastal municipality with Finnish as majority language** |
| `admin_region` | None | Campus building carries the 'unica' student-restaurant logo, and a modernist campus complex plus a heavily bilingual downtown fabric surrounds it. | Helsinki campuses are served by 'UniCafe' (HYY) and Tampere by 'Juvenes'; the 'unica' brand exists only for the Turku higher-education institutions. Vaasa would additionally push Swedish signage forward. | Helsinki — student catering would be branded UniCafe, Tampere — Juvenes branding, and Tampere is not bilingual, Vaasa — much smaller campus, Swedish-first signage | **Turku, Southwest Finland** |
| `district` | None | Sequence mixes a construction silo in birch woods, a lawned campus with white modernist block, a beige 1970s campus building with Unica, a rocky lane with guardrail, wooden houses on a slope, and a graffiti-covered student block near a crossing. | The west-bank centre would show dense 5-6 storey stone commercial frontage and the market square; the suburbs (Nummi) would show detached single-family houses with no institutional buildings. Here we get campus slabs, wooden 19th-century houses on a rock hill, and a rock cutting street climbing away from the river — the Yliopistonmäki/Vartiovuori terrain. | West-bank city centre — no dense stone commercial street or market square appears, Outer suburbs — institutional campus buildings and old wooden town houses are absent there | **The university hill quarter east of the Aura river: Kasarmialue / Henrikinkatu-Vesilinnantie block** |

## 단서 대조

- 공통(unaided) 8 · aided 에만 2 · blind 에만 2

### aided 에서만 나온 단서 (보조 지식 의존 가능성)
- [urban_fabric/district] 1960s-70s render-and-brick apartment block on pilotis, heavy tagging, hedge and bike leaning against pole — inner-city student housing belt.
- [map_context/metro_area] Minimap toponyms Aura river district names: Kasarmialue, Publicum, Posankka, Nummenpakka, Turun linja-autoasema, Turun yliopisto — all Turku-specific.

### blind 에서만 나온 단서 (지도가 있으면 사라진 관찰)
- [signage/country] Blue square pedestrian-crossing sign with the walking figure and Nordic proportions, plus grey aluminium sign posts typical of Finnish municipal street furniture.
- [infrastructure/macro_region] Elevated steel salt/aggregate silo on braced legs at a depot yard, typical of Nordic winter-maintenance contractors.

### 공통 단서 (일반 추론)
- [language] Blue parking sign is bilingual Finnish/Swedish: 'AUTOMAATTI / AUTOMAT' — bilingual signage is legally required only in bilingual Finnish municipalities. ⇄ Blue parking sign is bilingual 'AUTOMAATTI / AUTOMAT' — Finnish above, Swedish below, the standard order in a Finnish-majority bilingual municipality. (sim 1.15)
- [signage] Yellow-bordered orange-filled triangular warning sign (pedestrian crossing warning) — the yellow/orange fill is a Finland/Sweden/Iceland marker, not the white-fill EU standard. ⇄ Yellow-bordered orange triangular warning sign with a black pedestrian-crossing pictogram — the Nordic yellow-ground triangle used in Finland and Sweden, ruling out EU-standard white-ground triangles further south. (sim 1.02)
- [geology] Mossy exposed granite/gneiss bedrock cutting directly beside the pavement on a short steep urban hill, with a rustic wooden-post W-beam guardrail. ⇄ Moss-covered exposed granite/gneiss rock cutting right at street level with a wooden-post W-beam guardrail — classic Fennoscandian shield bedrock urban topography. (sim 0.99)
- [language] Company decal on the silo reads 'AL-LIIKENNE OY' with a mobile number starting 0400 — 'Oy' is the Finnish limited-company suffix and 0400 is the classic Finnish mobile prefix. ⇄ Company sign on the grey silo reads 'AL-Liikenne Oy' — 'Liikenne' (traffic/transport) and the company suffix 'Oy' (osakeyhtiö) are exclusively Finnish corporate markers, not Swedish 'AB' or Estonian 'OÜ'. (sim 0.78)
- [vegetation] Birch-dominated stand turning yellow/orange in a low sun angle — southern boreal autumn. ⇄ Pure birch stand turning yellow with a few maples, no spruce dominance — southern boreal / hemiboreal coastal belt rather than northern Finland's pine-spruce taiga. (sim 0.74)
- [architecture] White rendered modernist building with green-tile banding, stepped clerestory volumes and rough granite plinth, set in a lawn park — reads as an Alvar Aalto-school campus building. ⇄ White modernist faceted concrete/tile-banded building set in a lawn park with globe-topped steel lamp posts — typical late-20th-century Finnish university campus building. (sim 0.7)
- [commerce] Window graphic reads 'unica' with a green fork-plant logo — Unica is the student restaurant company operating exclusively on the University of Turku / Åbo Akademi campuses. ⇄ Window bearing the green 'unica' logo with a fork-plant mark — Unica is the student restaurant operator of the Turku universities specifically, not the Helsinki (UniCafe) or Tampere (Juvenes) operators. (sim 0.68)
- [architecture] Two-storey wooden apartment houses with horizontal board cladding, white corner boards, decorated gable and glazed stair bay — classic Turku/Port Arthur-type wooden townscape rather than a stone-block city centre. ⇄ Two-storey beige wooden apartment houses with white trim, decorative gable and pilaster boards, on a hill street; a domed neoclassical building visible at the crest — Turku's Vanha Suurtori/observatory hill (Vartiovuori) side of the old wooden quarter. (sim 0.48)

## 원자 계층 (샌드박스 적재)

| 계층 | 의미 | 개수 |
|---|---|---|
| unaided | 로드뷰만으로 추론되는 일반 사실 | 15 |
| aided | 지도(보조 지식)가 있어야만 나온 사실 | 1 |
| blind-only | 지도 없이 볼 때만 나온 관찰 | 5 |

### unaided
- `atm_4d9407145355` [architecture/road-signage/point] Bilingual Finnish/Swedish parking sign 'AUTOMAATTI/AUTOMAT'
- `atm_31c9cb601191` [architecture/road-signage/point] Orange-filled triangular pedestrian warning sign
- `atm_94781f625372` [geography/soil-terrain-cue/point] Exposed mossy granite/gneiss bedrock beside urban street
- `atm_b1e39f97b1ae` [architecture/bollard-guardrail/point] Rustic wooden-post W-beam guardrail on rock cut
- `atm_7f05e696dca7` [economy/business-chain/point] 'Unica' student restaurant window graphic
- `atm_5858623b487c` [geography/landform/region] Fennoscandian shield bedrock outcrops in urban streetscapes
- `atm_16658dd9369b` [nature/vegetation-cue/point] Yellow-orange birch stand in low autumn sun
- `atm_3e618af0fb76` [economy/business-chain/point] Finnish 'Oy' company decal with 0400 mobile prefix
- `atm_55c619a2fd7c` [culture/education/country] Regional student-restaurant branding as a Finnish city discriminator
- `atm_fe5cb80f595c` [architecture/street-furniture/point] Two-storey wooden townhouse with board cladding and glazed stair bay
- `atm_627f83101bf7` [nature/vegetation-cue/point] Ochre/pink render apartment blocks on hillside with rail line below
- `atm_fbd568bc4194` [architecture/street-furniture/point] Modernist white-rendered campus building with green-tile banding
- `atm_33b56f8338e9` [language/toponymy/country] Finnish company suffix 'Oy' vs Scandinavian equivalents
- `atm_88962bc03b9e` [geography/hydrology/region] Emilia vs Romagna river basin identity
- `atm_03d15747400a` [culture/infrastructure-built/city] Munich MVG grass-track tram radials distinguish it from other Bavarian cities

### aided
- `atm_91ae73ff4837` [history/polity-rule/city] Turku as historically Swedish-influenced, now Finnish-majority coastal city

### blind-only
- `atm_2e471e3e4e9a` [architecture/street-furniture/point] Elevated steel salt/aggregate silo at depot yard
- `atm_7e2820c6616b` [architecture/street-furniture/point] Nordic-style blue pedestrian crossing sign on grey aluminium post
- `atm_f10b1ed3277f` [architecture/other/country] Aalto-school institutional modernism on Finnish campuses
- `atm_f1bfe154be60` [culture/politics-civic/country] Finland's bilingual coastal municipalities (Finnish/Swedish)
- `atm_fe84b8e08af6` [culture/urban-form/city] Turku's Yliopistonmäki/Kerttuli/Martti belt: campus meets wooden quarter
