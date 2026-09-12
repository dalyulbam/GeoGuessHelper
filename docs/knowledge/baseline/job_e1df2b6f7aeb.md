---
{"job": "job_e1df2b6f7aeb", "report": "report_vn_thuong-dinh-phu-binh_21.492_105.9098_260906_210105_ko-en-fr.html", "truth": {"lat": 21.487608027413486, "lng": 105.9119446781629, "iso": "VN", "file_lat": 21.492, "file_lng": 105.9098, "n_panos": 9}, "lang": "en", "cost_usd": 0.6391, "created": 1788741322.1448667}
---

# 선택 장면 보고서 · VlJB8vGX…jXEA — blind vs aided

보고서 `report_vn_thuong-dinh-phu-binh_21.492_105.9098_260906_210105_ko-en-fr.html` · 캡처 6장 · 정답(pano 평균) 21.487608027413486, 105.9119446781629 · ISO VN

## 지표

| | blind (로드뷰만) | aided (지도 포함) | aided_prior (원 보고서) |
|---|---|---|---|
| 국가 | Vietnam (VN) | Vietnam (VN) | Vietnam |
| 국가 적중 | True | True | True |
| 지역 / 도시 | Thai Nguyen Province / Thuong Dinh / Diem Thuy, Phu Binh District | Thái Nguyên province / Thượng Đình / Hanh market area, Phú Bình district | Thái Nguyên province / Thượng Đình / Hanh market area, Phú Bình district |
| 좌표 오차 km | **3.46** | **3.71** | 3.27 |
| 도달 level | district | district | district |
| 단계 수 (ruled_out 있는 단계) | 5 (5) | 5 (5) | 5 (5) |
| 단서 수 | 10 | 11 | 12 |
| 확신 | 0.9 | 0.92 | 0.9 |

## 로직 변화 (감사 서술)

**판정**: `map-helped`

**blind 의 추론**: The blind chain used script, flags, banners, and vegetation to confirm Vietnam and Northern Vietnam midlands, then relied purely on photographed shop-sign addresses ('Hang Tai, Thuong Dinh, Phu Binh, Thai Nguyen' and 'Diem Thuy - Phu Binh - Thai Nguyen') and a directional sign 'PHU BINH 14 km' plus 'UBND xa Thuong Dinh 200 m' to fix district and commune, concluding a ribbon village ~14 km from Phu Binh town near Thuong Dinh/Diem Thuy.

**aided 의 추론**: The aided chain follows the same script/flag/vegetation reasoning for continent, country, and macro-region, but at the admin_region and district steps it begins citing road numbers (QL37, CT07, QL3) and named map features (Cho Hanh/Cau Hanh market, Tram Y Te xa Thuong Dinh) that only appear in the bottom map panel, and revises the distance to Phu Binh town from 14 km to 5 km and pinpoints the location to a specific market strip near the marker.

**달라진 것**: The core discriminators (script, flag, diacritics, vegetation/topography) are identical between chains and sufficient for country/macro-region. The delta appears in admin_region and district: the aided chain introduces road-numbering (QL37, CT07, QL3) and micro-toponyms (Cho Hanh, Cau Hanh, Tram Y Te xa Thuong Dinh) that are only visible on the map panel, not derivable from the street-level photos alone, and it also corrects the distance figure (14 km vs 5 km) in a way that exactly matches the marker's real position rather than independently reasoned. This shows the map was used to sharpen and effectively confirm the final pinpoint rather than merely narrating photographic evidence.

**지도가 있어야만 가능했던 단계**:
- admin_region: citation of QL37/CT07/QL3 highway network to distinguish Thai Nguyen from Bac Giang/Phu Tho
- district: naming Cho Hanh/Cau Hanh market and Tram Y Te xa Thuong Dinh as the specific pin location, and revising distance to Phu Binh town to 5 km based on map placement

## blind 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | None | Latin script with heavy diacritics, banana groves, wet rice paddies, dense motorbike traffic with helmets. | South Asia would show Devanagari/Bengali script and auto-rickshaws; East Asia (China/Japan) would show CJK characters and different pole hardware. | South Asia - no Indic script, no rickshaws, East Asia - no Han/kana characters | **Mainland Southeast Asia, tropical monsoon lowland** |
| `country` | None | Vietnamese orthography (ă, ơ, ư, ệ), red flags with yellow star, Vietnam Social Security banner. | Lao, Thai and Khmer use their own abugidas on shop signs; Indonesian/Filipino Latin text lacks the tone marks and horn vowels (ơ, ư) and would not feature the Vietnam Social Security logo or the yellow-star flag. | Laos/Thailand/Cambodia - non-Latin scripts, Indonesia/Philippines - no diacritics, left-side islands/jeepneys absent | **Vietnam** |
| `macro_region` | None | Ripening rice on undulating ground with low forested hills behind the village, north-style tube houses, tile roofs, 'Nhua Tien Phong' (Hai Phong brand) dealership. | Mekong Delta would show canals, stilted houses, water hyacinth and flat horizon with no hills; Central Highlands would show red basaltic soil and coffee/rubber plantations; North Central Coast would show sandy soil and casuarina. | Mekong Delta - no canals or stilt housing, Central Highlands - no red laterite plantations, North Central Coast - no sand/dune vegetation | **Northern Vietnam, midland/delta fringe** |
| `admin_region` | None | Two independent shop signs carry addresses 'Hang Tai, Thuong Dinh, Phu Binh, Thai Nguyen' and 'Diem Thuy - Phu Binh - Thai Nguyen'. | Bac Giang or Bac Ninh shop signs would end with those province names; the printed address lines here end explicitly in 'Phu Binh, Thai Nguyen'. | Bac Giang / Bac Ninh / Vinh Phuc - contradicted by printed province name, Hanoi outskirts - would use Hanoi district names | **Thai Nguyen Province, Phu Binh District** |
| `district` | None | Directional sign 'PHU BINH 14 km' plus 'UBND xa Thuong Dinh 200 m'; shop addresses split between Thuong Dinh and Diem Thuy. | If the camera were in Phu Binh town itself the directional sign would not read 'Phu Binh 14 km'; the 'UBND xa Thuong Dinh 200 m' plate fixes the commune seat immediately ahead, while a Diem Thuy-addressed shop indicates the route continues into the neighbouring commune. | Phu Binh town centre - sign shows it is 14 km away | **Ribbon village along the provincial road at Thuong Dinh commune centre, ~14 km from Phu Binh town, adjoining Diem Thuy** |

## aided 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | None | Wet paddy right up to the shoulder, banana clumps, scooter-dominated traffic, Latin-script signs with tonal diacritics, tube houses. | Latin America would show Spanish/Portuguese signage and pickup-dominated traffic; South Asia would show Devanagari/Bengali script, auto-rickshaws and different guardrail types; East Asia proper (JP/KR/CN) would show CJK characters and far heavier car traffic. | Latin America - no Romance-language signage, motorbike modal share too high, South Asia - no Indic script, no auto-rickshaws, East Asia - no CJK characters | **Mainland Southeast Asia, monsoon rice belt** |
| `country` | None | Vietnamese quoc ngu with stacked diacritics everywhere, Vietnam Social Insurance (BHXH) blue propaganda banner, red flags with yellow star, red pennant 'phuong' banners. | Lao/Thai/Khmer would use their own abugida scripts on every shopfront; Indonesia uses Latin but without diacritics, drives with different road markings and would not fly a red flag with a yellow star or run Vietnam Social Insurance banners. | Laos/Thailand/Cambodia - non-Latin scripts absent from all six frames, Indonesia - diacritics and the national flag design exclude it | **Vietnam** |
| `macro_region` | None | Rolling low forested hills behind the paddy, no delta canal grid, temperate-ish broadleaf street trees mixed with banana. | The Mekong Delta would be dead flat with canals, water hyacinth and no forested hills; the Central Highlands would show red basalt soil with coffee/rubber rather than paddy; the Red River Delta proper has no low forested hills at all. Here paddy is interleaved with rounded eucalyptus/acacia-covered hillocks, the classic midland mosaic, and a tutoring centre is branded 'Viet Bac'. | Mekong Delta - hills and absence of canals, Central Highlands - no red basalt soil or perennial cash crops, North Central Coast - no coastal sand/dune vegetation | **Northern midlands (trung du) of North Vietnam** |
| `admin_region` | None | Two independent shop signboards give postal addresses ending 'Phú Bình - Thái Nguyên' (Hàng Tài, Thượng Đình and Điềm Thụy); map shows QL37 with CT07 expressway and QL3 to the west. | Had this been Bac Giang or Phu Tho, the shop address lines would end in those province names and the road number on the map would be QL31 or QL2 rather than QL37 with CT07 (Hanoi-Thai Nguyen/Cho Moi corridor) parallel to the west. | Bac Giang / Bac Ninh - addresses explicitly say Thai Nguyen, Phu Tho / Vinh Phuc - wrong highway pair, wrong province in addresses | **Thai Nguyen province, Phu Binh district** |
| `district` | None | Blue guide sign 'PHÚ BÌNH 5 km' plus 'UBND xã Thượng Đình 200 m'; map pin sits between Trạm Y Tế xã Thượng Đình and Chợ Hanh / Cầu Hanh on QL37. | Diem Thuy is only the address of a feed supplier's head office, not the pictured street; if we were in Phu Binh town the direction sign would not read 'PHU BINH 5 km' and the built form would be a continuous town, not a paddy-edged highway with a commune health station and a single market strip. | Phu Binh town - sign says it is still 5 km away, Diem Thuy - only appears as a distant business address | **Thuong Dinh commune, on QL37 near Cho Hanh / Cau Hanh, about 5 km from Phu Binh town** |

## 단서 대조

- 공통(unaided) 7 · aided 에만 4 · blind 에만 3

### aided 에서만 나온 단서 (보조 지식 의존 가능성)
- [signage/country] Blue government banner from Vietnam Social Insurance (BHXH) about one-time social insurance withdrawal and pension rights
- [vehicles/macro_region] Traffic is almost entirely small-displacement scooters/step-through motorbikes, riders on the right side of the road
- [map/metro_area] Map labels QL37 running NNW-SSE, with CT07 expressway and QL3 to the west, 'Trạm Y Tế xã Thượng Đình', 'Chợ Hanh', 'Núi đình', river meander to the east
- [signage/macro_region] Tutoring-centre sign 'TRUNG TÂM VIỆT BẮC' — 'Viet Bac' is the historic name for the northern mountain-midland region

### blind 에서만 나온 단서 (지도가 있으면 사라진 관찰)
- [language/admin_region] Shop sign address line reads 'Hang Tai, Thuong Dinh, Phu Binh, Thai Nguyen' - explicit commune/district/province.
- [traffic/country] Motorbikes dominate, driving on the right, helmets worn - Vietnamese norm.
- [commerce/cultural_sphere] 'Nhua Tien Phong' plastic pipe dealership - a Hai Phong-based brand distributed mainly across northern Vietnam.

### 공통 단서 (일반 추론)
- [infrastructure] Concrete-post W-beam guardrail with red reflector tabs and small white kilometre marker with red cap - standard Vietnamese provincial road furniture. ⇄ Two-lane rural highway with yellow centre dashes, W-beam guardrail on concrete posts with red reflector dots, small white kilometre marker post with red top (sim 0.94)
- [vegetation] Ripening golden rice paddies immediately adjacent to the road with low hills and banana clumps - Red River delta fringe / northern midland landscape rather than Mekong delta canals. ⇄ Ripening golden paddy immediately adjacent to the highway, plus banana clumps and forested low hills behind (sim 0.87)
- [signage] Blue directional sign reading 'PHU BINH 14 km' and 'UBND xa Thuong Dinh 200 m' - places the camera roughly 14 km from the district town, at Thuong Dinh commune centre. ⇄ Blue direction sign reading 'PHÚ BÌNH 5 km' and 'UBND xã Thượng Đình 200 m' (sim 0.87)
- [language] Vietnamese Latin script with full diacritics on a social-insurance banner ('NHAN BAO HIEM XA HOI MOT LAN...') with the VSS logo. ⇄ Vietnamese Latin script with full diacritics: 'NHỰA TIỀN PHONG', 'Cửa hàng TUẤN THỦY', address line 'Hàng Tài, Thượng Đình, Phú Bình, Thái Nguyên' (sim 0.81)
- [architecture] Narrow tube houses with ornate balustrades, red tile roofs and painted concrete facades typical of northern Vietnamese market towns. ⇄ Narrow multi-storey tube houses with ornate balustrades and pastel plaster, shophouse ground floors with roller shutters (sim 0.72)
- [language] Second shop sign address 'DC: Diem Thuy - Phu Binh - Thai Nguyen', confirming the same district. ⇄ Shop sign address 'ĐC: ĐIỀM THỤY - PHÚ BÌNH - THÁI NGUYÊN' for an animal-feed distributor (sim 0.69)
- [politics] Rows of red socialist flags and red-yellow national flags lining shopfronts, typical of Vietnamese commune main streets during holidays. ⇄ Rows of red Vietnamese national flags and red 'phướn' pennant banners lining the village street, typical of a Party/holiday commemoration period (sim 0.62)

## 원자 계층 (샌드박스 적재)

| 계층 | 의미 | 개수 |
|---|---|---|
| unaided | 로드뷰만으로 추론되는 일반 사실 | 15 |
| aided | 지도(보조 지식)가 있어야만 나온 사실 | 2 |
| blind-only | 지도 없이 볼 때만 나온 관찰 | 2 |

### unaided
- `atm_0bb59e38afd7` [architecture/road-signage/point] Blue directional sign to Phu Binh and commune office
- `atm_5bdd8c914eef` [culture/flag-emblem/point] Rows of red socialist and national flags on shopfronts
- `atm_e2517fdd0987` [architecture/bollard-guardrail/point] Concrete-post W-beam guardrail with red reflectors and km marker
- `atm_ef5e724a7611` [architecture/street-furniture/point] Narrow tube houses with ornate balustrades and tile roofs
- `atm_49e5d481af9e` [economy/vehicle-fleet/point] Motorbike-dominant right-hand traffic with helmets
- `atm_5eb0cfb170dc` [architecture/road-signage/point] Vietnamese social-insurance banner with VSS logo
- `atm_baf1e926f4c7` [culture/flag-emblem/country] Vietnam flag and Social Security banners as national visual markers
- `atm_026a8abf04fa` [nature/vegetation-cue/point] Golden rice paddies with low hills and banana clumps
- `atm_a76e46519aa6` [geography/landform/region] Northern Vietnam midland/delta fringe distinguished by undulating terrain and forested hills
- `atm_1d9255908236` [architecture/road-signage/point] Shop sign with full commune/district/province address
- `atm_1c92be9992bc` [language/script/country] Vietnamese Latin orthography with diacritics distinguishes it regionally
- `atm_bf2a442eac6d` [economy/business-chain/point] Villa-shophouse mixed commercial units
- `atm_cba423bb5a1d` [architecture/housing-typology/region] Northern Vietnamese tube houses and tile roofs in ribbon village form
- `atm_b25072b97c43` [culture/toponymy/country] Administrative address signage convention in Vietnam names commune-district-province
- `atm_09d010308dc1` [economy/tourism-economy/country] Prosperous Czech rural village socioeconomic markers

### aided
- `atm_92a6dec05984` [architecture/road-signage/point] Tutoring-centre sign referencing 'Viet Bac' region name
- `atm_9f1c08f628a4` [architecture/road-signage/point] Map labels for QL37 corridor near Thuong Dinh

### blind-only
- `atm_14ebe6893af6` [economy/business-chain/point] Nhua Tien Phong plastic pipe dealership signage
- `atm_ddf31e7785e4` [economy/industry/region] Thai Nguyen Province industrial growth linked to Samsung manufacturing complex
