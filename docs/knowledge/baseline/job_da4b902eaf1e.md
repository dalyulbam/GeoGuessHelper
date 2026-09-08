---
{"job": "job_da4b902eaf1e", "report": "report_jo_jerash_32.288_35.8899_260903_095803_ko-en-fr.html", "truth": {"lat": 32.287594703598, "lng": 35.88818390217687, "iso": "JO", "file_lat": 32.288, "file_lng": 35.8899, "n_panos": 7}, "lang": "en", "cost_usd": 0.6573, "created": 1788740154.3301928}
---

# 선택 장면 보고서 · 9e4BujI9…O-1A — blind vs aided

보고서 `report_jo_jerash_32.288_35.8899_260903_095803_ko-en-fr.html` · 캡처 6장 · 정답(pano 평균) 32.287594703598, 35.88818390217687 · ISO JO

## 지표

| | blind (로드뷰만) | aided (지도 포함) | aided_prior (원 보고서) |
|---|---|---|---|
| 국가 | Jordan (JO) | Jordan (JO) | Jordan |
| 국가 적중 | True | True | True |
| 지역 / 도시 | Jerash Governorate / Jerash (Wadi al-Deir / outskirts village) | Jerash Governorate / Jerash (western outskirts, Wadi al-Deir area) | Jerash Governorate / Jerash (Wadi Al-Deir / Al-Rayyan area, west of city centre) |
| 좌표 오차 km | **1.54** | **2.5** | 1.67 |
| 도달 level | district | district | district |
| 단계 수 (ruled_out 있는 단계) | 5 (5) | 5 (5) | 5 (5) |
| 단서 수 | 12 | 10 | 10 |
| 확신 | 0.62 | 0.9 | 0.88 |

## 로직 변화 (감사 서술)

**판정**: `map-helped`

**blind 의 추론**: The blind chain relies entirely on street-level cues: Arabic-only signage and limestone construction place it in the Levant; Jordanian mobile prefixes (077/078/079) confirm Jordan over Palestine/Syria/Lebanon; olive/pine cover and low-rise stone buildings point to the northwest highlands rather than Amman, the south, or the Badia; a hardware banner reading 'I'mar Jerash' and a shawarma sign labeled 'Wadi al-Deir' narrow it to Jerash Governorate and specifically the Wadi al-Deir vicinity. All discriminators are grounded in visible text, architecture, vegetation, and phone number formats.

**aided 의 추론**: The aided chain reaches the same conclusion using nearly identical signage, phone-prefix, and vegetation reasoning for continent/country/macro_region, but at the admin_region step it explicitly invokes 'adjacent map-context features (Jerash cultural centre, Jerash DMV, Jerash archaeological site) appear in the same frame band' — i.e., labels visible in the map panel — to confirm Jerash Governorate, rather than relying solely on the business-name banner. The district step also references the ridge road location and 'Al Rayyan Dairy area' in a way that suggests map-panel place names were consulted, not just street signage.

**달라진 것**: The first three levels (continent, country, macro_region) are unchanged and remain evidence-driven from street-level cues alone. The delta appears at admin_region, where the aided chain adds explicit mention of map-context POIs (Jerash cultural centre, DMV, archaeological site) as corroborating discriminators — these are only accessible via the map panel, not the street-view images. The district-level conclusion also gains a specific named feature ('Al Rayyan Dairy area') that likely comes from map labels rather than derivable from the shawarma-sign photo alone. This inflates confidence from 0.62 to 0.9 without truly new street-level evidence.

**지도가 있어야만 가능했던 단계**:
- admin_region: citing 'Jerash cultural centre, Jerash DMV, Jerash archaeological site' appearing in the same frame band as corroboration for Jerash Governorate
- district: naming 'Al Rayyan Dairy area' as the specific fringe location toward Wadi al-Deir

## blind 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | Which macro-region does this arid limestone hill town with Arabic-only signage belong to? | Arabic Naskh signage, pale limestone construction, olive groves, rooftop water tanks, right-hand traffic. | North Africa (Maghreb) would show French bilingual signage and different plate colours; Central Asia would use Cyrillic/Latin Turkic; Southern Europe would show Latin script and tiled pitched roofs. | North Africa: no French co-signage, Central Asia: no Cyrillic/Turkic, Southern Europe: no Latin-script civic signage or tiled roofs | **Eastern Mediterranean / Levant** |
| `country` | Jordan, Palestine, Syria or Lebanon? | Multiple shop phone numbers begin 077, 078, 079 in ten-digit local format (0779644224, 0778190195, 0776471458, 0779233366). | West Bank Palestinian mobiles run 059x/056x; Syrian mobiles run 093/094/098; Lebanese run 03/70/71/76/81 (8 digits). Only Jordan uses the 077/078/079 Zain/Orange/Umniah block. Palestinian towns would also show Arabic+Hebrew traces, martyr posters or PA-style green plates; Lebanon would show heavy French signage. | Palestine: wrong mobile prefixes, no Hebrew/PA visual markers, Syria: wrong prefixes, no Syrian regime-era signage or Baath iconography, Lebanon: no French co-signage, wrong number format | **Jordan** |
| `macro_region` | Northwestern highlands, Amman metro, or the arid south/east of Jordan? | Hillsides carry continuous olive orchards and pines; deep valleys with dispersed white villages; only low-rise stone buildings, no dense urban fabric or highway furniture. | Amman would show six-plus-storey blocks, blue directional signs, traffic lights and dense continuous frontage; the southern plateau (Karak/Ma'an) is treeless with red-brown soil and no olive canopy; the eastern Badia is basalt desert without any orchard cover. | Amman-Zarqa: no urban density, no signalised junctions, Southern Jordan: too much olive/pine cover for that rainfall, Eastern Badia: no basalt, no desert steppe | **Northwest Jordan rain-fed highland belt** |
| `admin_region` | Irbid, Ajloun or Jerash governorate? | A building-materials banner is branded 'I'mar Jerash' — local suppliers overwhelmingly name their own town — and a shawarma sign carries the sub-locality 'Wadi al-Deir'. | An Irbid-based location would carry 'Irbid' branding and sit on flatter, more built-up Houran plain terrain; Ajloun town centre is more forested with denser evergreen oak and steeper streets. The Jerash reference plus moderately open olive slopes fits Jerash Governorate. | Irbid: no Irbid branding, terrain too dissected, Ajloun: oak forest cover absent, and local firm names Jerash instead | **Jerash Governorate, hillside village on a valley road** |
| `district` | Which settlement within Jerash Governorate? | 'Wadi al-Deir' appears as the branch identifier on the shawarma chain sign; the scene is a single sloping commercial strip in a village overlooking a broad settled valley. | Jerash city proper would show the Roman ruins, tourist signage and multi-lane streets; Souf is a larger refugee-camp-adjacent town with denser continuous shopfronts. A one-strip village on a descending valley road matches the Wadi al-Deir side of Jerash. | Jerash city centre: no Roman ruins, tourism signage or urban width, Souf: fabric too sparse and rural | **Small village strip in the Wadi al-Deir vicinity west/north of Jerash city** |

## aided 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | None | All commercial signage is Arabic-only with sparse English, buildings are pale limestone-clad concrete frames with flat roofs and black/blue roof water tanks. | North African Arabic streets would show heavy French bilingual signage and different plate style; Central Asia would show Cyrillic/Turkic Latin; southern Europe would show Latin-script signage and tiled pitched roofs instead of flat concrete slabs with roof water tanks. | North Africa: no French bilingual signage, Central Asia: no Cyrillic/Turkic script, Southern Europe: no Latin script, no pitched tiled roofs | **Arabic-speaking Middle East, Levantine highland belt** |
| `country` | None | Shop phone numbers: 0778190195, 0779885566, 0791519494, 0779644224, 0776471458, 0779233366 — all Jordanian mobile prefixes; no Hebrew anywhere; Google coverage is continuous (full official Street View, unlike Syria/Lebanon). | West Bank shops would carry 059/056 Palestinian mobile prefixes and often Hebrew-influenced products; Syria would use 093/094/098 prefixes and show war-damaged concrete plus Assad-era municipal signage; Lebanon would show 03/70/71 prefixes and much French usage. Here every advertised mobile number is 077x/079x, the exact Jordanian mobile block. | Palestine: wrong mobile prefixes, no Hebrew co-signage, Syria: prefix mismatch, official StreetView car coverage absent there, Lebanon: no French, prefix mismatch, Israel: no Hebrew signage at all | **Jordan** |
| `macro_region` | None | Rolling limestone ridges covered in olive orchards, valley-side dense white housing, cool clear winter light with green grass in one capture. | Mafraq/Zarqa would show black basalt fields and treeless steppe; the Jordan Valley or Aqaba would show banana/palm plantations, sub-sea-level haze and no olive terraces; Karak would show deeper red sandstone and more sparse settlement. Here mature olive groves cover stony terra-rossa terraces between white hilltop villages — the classic 400-600 mm rainfall northern highlands. | Zarqa/Mafraq: no basalt desert steppe, Jordan Valley/Aqaba: no palms/bananas, terrain is upland, Karak/Tafilah: rock and settlement pattern differ, Amman metro: no high-rise or wide dual carriageways, village-scale infrastructure | **Northern highland belt (Ajloun-Jerash-Irbid), not the desert or Rift floor** |
| `admin_region` | None | Business names reference Jerash directly, and adjacent map-context features (Jerash cultural centre, Jerash DMV, Jerash archaeological site) appear in the same frame band. | An Irbid or Ajloun business would brand itself 'إعمار إربد' or reference Ajloun/Kufranja; the hardware store here is explicitly 'إعمار جرش' (Jerash Construction), and local naming 'وادي الدير' matches the Wadi al-Deir valley immediately west of Jerash city, not any Ajloun or Irbid locality. | Irbid: no Irbid-branded businesses, terrain more open plateau there, Ajloun: denser pine/oak forest and steeper relief, different local toponyms | **Jerash Governorate** |
| `district` | None | Continuous low commercial parade on one side, open olive terraces and unpaved shoulders on the other; local shawarma branded 'Wadi al-Deir'; road narrows to a steep single-lane lane in one capture. | Jerash city centre would show the Roman colonnaded ruins, Hadrian's Arch, hotel/tourist signage and multi-lane through roads; Souf and Sakib are separate hill villages further west with much thinner commercial strips. Here the strip is a single-sided ridge road of building-materials, dairy, furniture and shawarma shops, with olive terraces starting directly across the road — a transition zone where urban Jerash meets farmland. | Jerash city centre: no ruins, no tourist signage, road too narrow, Souf/Sakib: commercial density here is too high for those villages | **Western fringe of Jerash city along the ridge road toward Wadi al-Deir / Al Rayyan Dairy area** |

## 단서 대조

- 공통(unaided) 8 · aided 에만 2 · blind 에만 4

### aided 에서만 나온 단서 (보조 지식 의존 가능성)
- [roads/district] Narrow unmarked asphalt street with no curbs on one side, wooden/concrete utility poles carrying low-slung bare conductors across the road
- [vehicles/country] Older 1990s-2000s Japanese sedans (Civic, Camry) and small pickups, right-hand traffic, white rectangular plates

### blind 에서만 나온 단서 (지도가 있으면 사라진 관찰)
- [phone_numbers/country] Furniture shop lists 0778 190 195 with WhatsApp icon — again Jordanian 078 prefix, not Palestinian 059/056 or Syrian 093/094.
- [utilities/macro_region] Large blue polyethylene rooftop water tanks plus rooftop solar/steel water drums — hallmark of intermittent municipal water supply in Jordan/Palestine.
- [road_infrastructure/country] Right-hand traffic, no lane markings, informal gravel shoulders, no Israeli-style yellow-and-black plates or Hebrew signage.
- [signage/district] Older 2017 imagery with vaulted stone arch shopfront ('Al-Mokhtar' events hall) on a narrow steep lane — traditional Ottoman-era stone vernacular common in Jerash-area villages.

### 공통 단서 (일반 추론)
- [architecture] Two- to three-storey buildings faced in pale cream limestone cladding with concrete frames and rebar left for future floors — standard Jordanian/Levantine incremental construction. ⇄ Buildings faced with cut pale cream limestone over concrete frames, flat roofs, rebar left protruding for future floors, blue polyethylene water tanks on roofs (sim 1.03)
- [language] All shop signage in Arabic (Naskh/modern display faces) with Latin transliteration secondary — 'Adam & Kenan market', 'Shawarma'. ⇄ Shop signage exclusively in Arabic script with occasional English transliteration (Adam & Kenan market, Baker Street Shawarma), Levantine commercial vocabulary (مفروشات, مطعم) (sim 0.94)
- [phone_numbers] Mobile numbers printed as 0779644224, 0779885566, 0791519494 — the Jordanian 077/079/078 mobile prefix pattern (10 digits starting 07). ⇄ Mobile numbers begin 077 / 0779 / 0791 / 0778 — Jordanian mobile prefixes (Orange/Umniah/Zain 077, 078, 079) (sim 0.84)
- [commerce] Local pharmacy, paint/decor shop, small supermarket cluster under a stone arcade with pale ashlar columns — small provincial town commercial strip, not a capital-city district. ⇄ Local mix of pharmacy, decor shop, mini-market and furniture store under stone arcades with columns — self-contained neighbourhood high street rather than tourist strip (sim 0.73)
- [toponym] Shawarma sign sub-line reads 'وادي الدير' (Wadi al-Deir), a locality name used in the Jerash/Ajloun hill belt. ⇄ Shawarma sign reads 'وادي الدير' (Wadi al-Deir), a named neighbourhood/valley on the western side of Jerash (sim 0.6)
- [toponym] Building-materials banner reads 'إعمار جرش لمواد البناء' (I'mar Jerash Building Materials) — the firm names itself after Jerash. ⇄ Building-materials shop named 'إعمار جرش' (Jerash Construction), i.e. a business branded with the city of Jerash (sim 0.54)
- [vegetation] Dense olive groves and scattered pines on the hillside, greener than typical Jordanian steppe — matches the higher-rainfall Ajloun/Jerash highlands rather than Amman's east or the Badia. ⇄ Dense olive groves on stony terraced slopes right up to the roadside, dry grass, no irrigation-dependent tropical growth (sim 0.44)
- [terrain] Rolling limestone ridges with dispersed white villages across the valley, road descending steeply — northwest Jordan hill country topography. ⇄ Town sprawling over rolling limestone ridges at moderate elevation with white flat-roofed houses spreading across facing hillsides (sim 0.43)

## 원자 계층 (샌드박스 적재)

| 계층 | 의미 | 개수 |
|---|---|---|
| unaided | 로드뷰만으로 추론되는 일반 사실 | 18 |
| aided | 지도(보조 지식)가 있어야만 나온 사실 | 2 |
| blind-only | 지도 없이 볼 때만 나온 관찰 | 1 |

### unaided
- `atm_42b912b26bbf` [language/other/country] Jordanian mobile number prefixes (077/078/079)
- `atm_63c3ad64dad6` [architecture/street-furniture/point] Cream limestone cladding with exposed rebar on unfinished floors
- `atm_1acadd8b27f7` [architecture/road-signage/point] Arabic-primary shop signage with Latin transliteration
- `atm_03d35c9a338e` [economy/business-chain/point] Stone arcade commercial strip with small provincial shops
- `atm_51a0d20a5490` [economy/business-chain/point] Building-materials firm named after Jerash
- `atm_5c1e191a8ab8` [architecture/street-furniture/point] Jordanian mobile phone prefix pattern on printed signage
- `atm_7a2109fba073` [economy/vehicle-fleet/point] Modern crossovers/SUVs with EU-format plates, right-hand traffic
- `atm_36ed6445c8a6` [architecture/road-marking/point] Unmarked right-hand traffic road with gravel shoulders
- `atm_7b5b6b12f4d5` [geography/soil-terrain-cue/point] Rolling limestone ridges with dispersed white hilltop villages
- `atm_83f9484293d2` [nature/vegetation-cue/point] Dense olive groves and scattered pines on hillside
- `atm_5421397182ee` [geography/climate/region] Northwest Jordan rain-fed highland belt (Ajloun-Jerash-Irbid)
- `atm_3ed1a91ef487` [culture/housing-typology/country] Jordanian vernacular owner-built concrete-and-limestone housing
- `atm_f1601133c107` [geography/road-marking/global] Diagnostic road/traffic signage cues for continent-level discrimination
- `atm_a428bc42f357` [architecture/road-signage/point] BUCO hardware directional sign with local phone code
- `atm_77045e0f39b2` [architecture/road-marking/point] Narrow unmarked road with paver sidewalk and wheelie bin
- `atm_057a30b768a5` [economy/vehicle-fleet/country] Aged Japanese/Korean used-car fleet in Jordan
- `atm_632345065d73` [economy/toponymy/region] Local business self-branding with town/sub-locality names in rural Jordan
- `atm_38169a37b400` [architecture/street-furniture/point] Ottoman-era stone vaulted shopfront on steep lane

### aided
- `atm_42c9e515cae9` [economy/retail/country] Jordanian provincial micro-business economy via mobile/WhatsApp advertising
- `atm_f289a4c130dc` [culture/demography/region] Jerash Governorate population composition

### blind-only
- `atm_a9f3f24c8706` [architecture/street-furniture/point] Rooftop blue polyethylene water tanks and steel drums
