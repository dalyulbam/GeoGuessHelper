---
{"job": "job_1a2be2d9d7ff", "report": "report_es_esquinzo-fuerteventura_28.0717_-14.3123_260906_204807_ko-en-fr.html", "truth": {"lat": 28.070923471343104, "lng": -14.309379217012756, "iso": "ES", "file_lat": 28.0717, "file_lng": -14.3123, "n_panos": 9}, "lang": "en", "cost_usd": 0.6566, "created": 1788740938.3535385}
---

# 선택 장면 보고서 · lJp_ScSR…EhbQ — blind vs aided

보고서 `report_es_esquinzo-fuerteventura_28.0717_-14.3123_260906_204807_ko-en-fr.html` · 캡처 6장 · 정답(pano 평균) 28.070923471343104, -14.309379217012756 · ISO ES

## 지표

| | blind (로드뷰만) | aided (지도 포함) | aided_prior (원 보고서) |
|---|---|---|---|
| 국가 | Spain (ES) | Spain (ES) | Spain |
| 국가 적중 | True | True | True |
| 지역 / 도시 | Canary Islands (Fuerteventura, Las Palmas province) / Costa Calma / Playitas–Las Playitas area, Fuerteventura | Canary Islands — Fuerteventura, Jandía peninsula / Esquinzo / Butihondo (Pájara) | Canary Islands — Fuerteventura (Las Palmas province) / Esquinzo / Butihondo (Jandía), Pájara municipality |
| 좌표 오차 km | **46.07** | **0.51** | 0.28 |
| 도달 level | district | district | district |
| 단계 수 (ruled_out 있는 단계) | 5 (5) | 6 (6) | 6 (6) |
| 단서 수 | 10 | 8 | 9 |
| 확신 | 0.55 | 0.9 | 0.9 |

## 로직 변화 (감사 서술)

**판정**: `map-leaked-answer`

**blind 의 추론**: The blind chain relies purely on visual cues: right-hand traffic, EU signage, and volcanic scoria terrain to reach Canary Islands, then uses roof architecture (orange pantile vs Lanzarote's mandated flat white/green code) to eliminate Lanzarote in favor of Fuerteventura, and finally uses road geometry (ridge-hugging dual carriageway, walled resort village, distant island silhouette) to guess at a southern resort corridor, landing tentatively on Costa Calma/Jandía/Playitas with 0.55 confidence, explicitly hedging between several candidate locations.

**aided 의 추론**: The aided chain follows a similar early trajectory (EU standards, Spain, Canary Islands, Fuerteventura via road-code prefix and terrain) but then pivots at admin_region and beyond to directly reading map-only data: the FV-2 road code, named landmarks (Monte Aguda 305m, Barranco de Vinamar, Boca de Esquinzo), and labeled POIs (Fuerteventura Princess, Royal Palm Resort, Jandía Golf, Morro Jable) that pin the location precisely to Esquinzo–Butihondo. The final district-level step even reasons about which side of the FV-2 the camera sits on relative to the map pin, reaching 0.9 confidence.

**달라진 것**: The first three steps (continent, country, macro_region/country) are essentially unchanged in reasoning quality, both using genuine street-level visual evidence. The divergence begins at admin_region, where the aided chain adds the 'FV-2' road-code sign as a shortcut confirmation, but the decisive break is at metro_area and district: the blind chain builds a plausible but hedged guess from topography and architecture alone (ridge-hugging highway, walled resort, orange pantile roofs), while the aided chain simply reads off named map labels (Monte Aguda, Barranco de Vinamar, Fuerteventura Princess, Royal Palm Resort, Morro Jable) to assert an exact location. These labels could not be derived from the images and function as answer leakage rather than independent discrimination — the 'disc' text even cites the map pin's position directly ('the pin sits directly on the FV-2') as the discriminator.

**지도가 있어야만 가능했던 단계**:
- admin_region: FV-2 road-code prefix read directly from map tiles
- metro_area: naming of Monte Aguda, Barranco de Vinamar, Fuerteventura Princess, Royal Palm Resort, Jandía Golf, Morro Jable — all sourced from map labels, not street imagery
- district: reasoning about camera position relative to the map pin's placement on the FV-2 near the Princess complex

## blind 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | None | Right-hand traffic, EU-style circular 90 speed sign, black basaltic scoria slopes, Canary date palms, ocean horizon with no mainland behind. | Australia and southern-hemisphere options would drive on the left and use different sign shapes; North Africa/Middle East would show Arabic script and different guardrail/lighting hardware; mainland Mediterranean would have limestone or schist hills with pine/olive rather than fresh black scoria and pure Phoenix canariensis plantings. | Australia — left-hand traffic, North Africa/Middle East — no Arabic signage, EU road furniture, Mainland Mediterranean — volcanic scoria and Phoenix canariensis instead of karst/olive landscape | **Subtropical volcanic Atlantic island with EU-standard infrastructure, northern hemisphere** |
| `country` | None | High-standard divided highway with landscaped median, EU speed discs, modern European rental fleet and Mercedes Sprinter ambulance in yellow/orange livery, in a hyper-arid volcanic setting. | Madeira is deeply green and steep with tunnels every few hundred metres and 'via rápida' viaducts; Cape Verde would show poorer road surfaces, no continuous Jersey barrier, older vehicle fleet and no EU-standard ambulance; mainland Almería is arid but has plastic greenhouse seas and no basaltic scoria cones. The clean 2x2 divided highway with landscaped Jersey median and planted palm avenue is the Canarian pattern. | Madeira — lush laurel/eucalyptus vegetation, tunnel-heavy motorway, no desert, Cape Verde — lower infrastructure standard, no EU signage regime, Mainland Spain — no fresh scoria cones or oceanic island horizon | **Spain — Canary Islands** |
| `admin_region` | None | Very low, smooth, treeless ochre hills; no deep barrancos, no tall buildings, extreme aridity with only tussock scrub. | Tenerife and Gran Canaria south coasts are arid too but show steep barrancos, tall apartment towers and Teide/Roque Nublo massifs on the skyline; the western islands (La Palma, La Gomera, El Hierro) are green and heavily forested with no resort motorway. Here the relief is low, rounded and utterly bare with no big volcanic massif, and there is not a single high-rise. | La Palma / La Gomera / El Hierro — forested, no resort autovía, Tenerife — Teide massif and high-rise Adeje skyline absent, Gran Canaria — deep barranco topography and dune/high-rise Maspalomas skyline absent | **Eastern (desert) islands: Fuerteventura or Lanzarote** |
| `metro_area` | None | Resort buildings are white with prominent orange-red pantile hipped and stacked roofs, plus a large orange pagoda-roof central block; nearby a wide modern divided coastal highway. | Lanzarote enforces a strict island-wide aesthetic code: white cubic buildings with GREEN (coast) or brown (interior) woodwork and flat roofs, and low-rise resorts almost never use large orange pantile hipped roofs or big pagoda-like roof masses. Fuerteventura's resorts freely use terracotta pantile hipped roofs over white walls, exactly as seen at the hotel here. The tall lone ridge of dark scoria beside a modern 2x2 coastal corridor also matches Fuerteventura's FV-2 / Jandía–Costa Calma stretch rather than Lanzarote's LZ roads. | Lanzarote — César Manrique building code (flat white roofs, green/brown woodwork) contradicts the orange pantile roofscape | **Fuerteventura** |
| `district` | None | Divided highway climbing along a dark scoria ridge with the sea on one side and a single walled resort village below; a faint distant island/mountain silhouette across the water. | Corralejo sits on flat dune terrain with Lobos and Lanzarote clearly filling the horizon and has no ridge-hugging dual carriageway; Caleta de Fuste is flat, airport-adjacent and lacks the steep dark scoria hillside directly beside the road. The section shown has a divided highway cut into a hillside with the sea and a self-contained resort village stepping down to the shore, which is the Costa Calma / Jandía approach pattern. | Corralejo — flat dune landscape, no ridge-side dual carriageway, Caleta de Fuste — flat terrain, no scoria hillside above the road | **Southern/eastern resort corridor of Fuerteventura — most likely the Costa Calma–Jandía or Playitas stretch where the divided highway hugs a scoria ridge above a walled resort** |

## aided 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | Which continental road-standard family? | Right-hand traffic, EU-standard circular red-rimmed '90' speed sign, white-only lane markings, no yellow lines | North Africa would show Arabic-Latin bilingual signage and different sign fonts; Australia/Latin America would use yellow diamond warning signs and different speed values (100/110 or km posts). The clean concrete New-Jersey median, EU circular 90 sign and white-only markings say EU. | North Africa — no Arabic script anywhere, Australia/Latin America — sign shapes and marking colours differ, Middle East — no bilingual signage | **European (EU) road standards in a hot desert coastal setting** |
| `country` | Which right-hand-drive EU state with a hot desert coast? | Mercadona supermarket POI, Spanish toponyms (Barranco de Vinamar, Boca de Esquinzo), Spanish-style yellow ambulance | Madeira would drive on the right too but show Portuguese toponyms (Ribeira, Caniço) and lush laurel forest, not desert; Cyprus and Malta drive on the LEFT. A Mercadona is Spain-only. | Portugal/Madeira — Portuguese place-name morphology absent, no Mercadona, Cyprus, Malta — left-hand traffic | **Spain** |
| `macro_region` | Mainland Spanish desert coast or Atlantic volcanic island? | Dark volcanic scoria hillside dropping straight to the ocean, road code 'FV-2' on the map, Phoenix canariensis palm avenues | Almería's Cabo de Gata desert has sedimentary pale rock and plastic greenhouse seas; here the hillside is dark basaltic scoria with reddish oxidation, and the only trees are planted Canary date palms — a volcanic Macaronesian signature. Mainland motorways would also carry A-/AP- shields, not an 'FV-' code. | Almería/Murcia — non-volcanic geology, different road-code prefixes (A-, RM-) | **Canary Islands, not mainland Spain** |
| `admin_region` | Which eastern Canary island? | Road prefix FV-2; low, heavily eroded rounded brown ridges without any forest belt; long light-sand beach | Lanzarote (LZ- roads) has black lapilli fields and legally restricted white-with-green architecture, no orange pantiles; Tenerife (TF-) would show Teide's mass and pine belts inland; Gran Canaria (GC-) has deeply forested highlands. Fuerteventura's 'FV-' prefix plus low, rounded, eroded ochre ridges is unique. | Lanzarote — LZ- prefix, different lava-field texture, Tenerife — no volcanic cone/pine forest, wrong prefix, Gran Canaria — no green interior | **Fuerteventura** |
| `metro_area` | Which resort strip on Fuerteventura? | FV-2 climbing a steep coastal massif above terraced resort blocks; map shows Monte Aguda 305 m, Fuerteventura Princess, Royal Palm Resort, Jandía Golf and Morro Jable to the south | Corralejo sits on flat dune fields with Lobos/Lanzarote visible offshore, not on a steep massif; Caleta de Fuste is flat with an airport nearby. Here the FV-2 is cut into a steep coastal massif with resorts terraced below it — only the Jandía section between Costa Calma and Morro Jable looks like this, and Monte Aguda/Barranco de Vinamar pin it exactly. | Corralejo — dune plain, no 300 m massif at roadside, Caleta de Fuste — flat terrain, Morro Jable — larger town/port, not visible here | **Jandía peninsula, Esquinzo–Butihondo strip in Pájara municipality** |
| `district` | Which side of the FV-2 is the camera on? | The camera sits on the sea-side shoulder of the dual carriageway, looking down over the hotel car park; the pin on the map sits directly on the FV-2 just west of the Princess complex | If the camera were inside the Esquinzo urbanisation the FV-2 median and 90 km/h sign would not be in frame; if it were on the inland side, the sea and resort would be behind the ridge instead of downhill in the foreground. | Inland side of the ridge — sea would be hidden, Inside the urbanisation — no highway median visible there | **Slip-road/service area on the seaward shoulder of the FV-2 at the Fuerteventura Princess entrance** |

## 단서 대조

- 공통(unaided) 6 · aided 에만 2 · blind 에만 4

### aided 에서만 나온 단서 (보조 지식 의존 가능성)
- [map_text/district] Map labels FV-2 road, Esquinzo, Fuerteventura Princess, Monte Aguda (305 m), Barranco de Vinamar, Jandía Golf
- [commerce/country] Mercadona supermarket marker — Spanish national chain absent from Portugal's Madeira/Azores

### blind 에서만 나온 단서 (지도가 있으면 사라진 관찰)
- [road_infrastructure/country] Traffic keeps right; New Jersey concrete median barrier with narrow planted strip, and slim tapered concrete-topped edge barriers typical of Spanish autovía/carretera construction on the Canaries.
- [landscape/admin_region] Low rounded ochre hills without deep ravines, blue open ocean and a faint island/mountain silhouette on the horizon across a strait.
- [urban_form/district] Terraced white apartment bungalow village stepping down a bare slope with tennis courts and walled compounds — a purpose-built tourist urbanización rather than an organic town.
- [street/district] Palm-lined quiet residential street, kerbside parking, older asphalt, faded white markings; hillside cut in loose volcanic rubble immediately adjacent — small-scale resort street grid.

### 공통 단서 (일반 추론)
- [vegetation] Long rows of planted Phoenix canariensis (Canary Island date palm) along the highway, with only tussock/desert scrub as natural cover. ⇄ Planted Phoenix canariensis (Canary Island date palm) rows in a desert setting with no other trees (sim 1.17)
- [geology] Dark reddish-brown scoria/basalt hillside with almost no vegetation, thin ochre lapilli soil — young volcanic terrain, not sedimentary Mediterranean limestone. ⇄ Barren dark volcanic slope with almost no vegetation right against the sea, ochre-brown scoria hillside — typical of the eastern Canary Islands rather than mainland Spain (sim 0.64)
- [climate] Bright dry haze, dust-brown ground, no seasonal grass — extremely arid island, drier than Tenerife/Gran Canaria north sides. ⇄ Bone-dry gravel ground with no grass at sea level, permanent haze/calima on the horizon (sim 0.59)
- [vehicles] Yellow-and-white Mercedes Sprinter ambulance with orange striping, common Canarian SUC/private ambulance livery; European compact cars with white front / yellow rear plate proportions. ⇄ Yellow ambulance with orange chevrons, Spanish-style emergency livery on a Mercedes Sprinter (sim 0.53)
- [architecture] Large all-inclusive resort complex: whitewashed cubic blocks with orange-red pantile hipped roofs, low-rise sprawl, huge surface car park — the standard Fuerteventura/Lanzarote package-holiday resort typology. ⇄ Dense low-rise white apartment bungalows with orange pantile roofs cascading toward a turquoise beach — classic Jandía resort urbanisation (sim 0.44)
- [signage] Circular red-rimmed white speed-limit signs reading 90 on a dual carriageway — EU/Spanish convention; Spain's 90 km/h general limit on conventional/divided roads outside motorway status. ⇄ European-style circular red-bordered speed limit sign reading 90, white edge line, dashed lane marking, driving on the right — Spanish/EU road standard (sim 0.44)

## 원자 계층 (샌드박스 적재)

| 계층 | 의미 | 개수 |
|---|---|---|
| unaided | 로드뷰만으로 추론되는 일반 사실 | 17 |
| aided | 지도(보조 지식)가 있어야만 나온 사실 | 2 |
| blind-only | 지도 없이 볼 때만 나온 관찰 | 3 |

### unaided
- `atm_a4589fc02684` [nature/vegetation-cue/point] Planted Canary Island date palm rows along highway
- `atm_cb3f69cd015e` [economy/vehicle-fleet/point] Yellow-and-white Sprinter ambulance, Canarian livery
- `atm_f8e7be7b0d9e` [geography/soil-terrain-cue/point] Dark scoria/basalt volcanic hillside with lapilli soil
- `atm_fa4a01097cb0` [architecture/road-signage/point] Circular red-rimmed white 90 km/h speed sign
- `atm_7a43df241d1e` [economy/tourism-economy/region] Canary Islands resort monoculture economy pattern
- `atm_65dffb9b81a4` [architecture/street-furniture/point] Whitewashed cubic resort blocks with pantile hipped roofs
- `atm_0bc1b4908126` [architecture/street-furniture/point] Terraced white apartment bungalow tourist urbanización
- `atm_14083abe94c9` [geography/landform/polity] Eastern vs western Canary Islands: aridity gradient
- `atm_657115f2405b` [geography/infrastructure-built/country] Spain vs Portugal Atlantic island infrastructure cues
- `atm_91929140289a` [nature/vegetation/polity] Macaronesian islands: volcanic Atlantic archipelagos share flora signatures
- `atm_451bd69c8185` [economy/business-chain/point] Large branded all-inclusive beach resort complex
- `atm_1827c3b83cbe` [architecture/street-furniture/point] Dark glazed kawara roofing with occasional red-brown Sekishu tiles
- `atm_65012271441e` [architecture/bollard-guardrail/point] NJ concrete median barrier with tapered edge barriers
- `atm_92d3cbda7793` [economy/license-plate/point] EU-style white-front/yellow-rear plate proportions
- `atm_507a054ac2f3` [culture/roof-facade/country] Ryukyuan vernacular architecture markers
- `atm_3a9a71af27c1` [economy/business-chain/point] 'LA TEGOLAIA – DIVISIONE EMILIA' business board
- `atm_ace3b619ddf6` [architecture/roof-facade/region] Lanzarote's César Manrique aesthetic building code

### aided
- `atm_4a3a6d7cf5aa` [architecture/road-signage/point] Map overlay naming FV-2 road and local landmarks
- `atm_68d536a3a07f` [geography/road-signage/polity] Canary Islands road-code prefixes identify individual islands

### blind-only
- `atm_0dd664b70ce5` [architecture/road-marking/point] Faded white markings on older asphalt resort street
- `atm_82dc52ba9e94` [geography/soil-terrain-cue/point] Loose volcanic rubble hillside cut beside street
- `atm_ca5ccb4a1395` [geography/soil-terrain-cue/point] Rounded ochre hills without deep ravines by open ocean
