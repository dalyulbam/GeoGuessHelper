# 자동 정정 루프 — 정정 원장

생성 2026-09-07 16:26 · 잡 15건(최신 기록, 그중 OK 15) · 실행 16회(corrections.jsonl) · 이번 기록 비용 $8.482 · 누적 실행 비용 $9.028 · 기획: docs/plan/impl-spec_260907.md §3

같은 캡처를 지도 없이(blind) 다시 판단하고(회상 원자가 있으면 2패스), 실측 pano 좌표·aided 분석과 대조해 "X 는 사실 X2 였다"는 정정을 만들어 kind=discriminator 원자로 적재한다. 사람 승인은 없다 — 회상돼 쓰인 원자는 confirming/misled 로 채점되어 hits/misses 가 오르내리고, 오답만 뒷받침한 원자는 retracted(회상 제외)된다.

## 집계

| 지표 | 값 |
|---|---|
| 국가 적중률(blind 최종) | 15/15 (100%) |
| 지역 적중률 | 15/15 (100%) |
| 도시 적중률 | 14/15 (93%) |
| 좌표 오차 km 중앙값 | 1.17 (n=15) |
| 오차 분포 | <1 6 · <10 6 · <100 2 · >=100 1 · na 0 |
| 2패스 사용(회상 원자 있음) | 15/15 |
| 2패스로 판단이 바뀐 건수(revised) | 0 |
| 판별자 원자 — 이번 기록에서 신규 / 병합 | 66 / 7 |
| 저장소의 kind=discriminator 원자(누적) | 70 |
| 철회된 원자 — 이번 기록 / 저장소 현재 status=retracted | 0 / 0 |
| 총비용(최신 기록 합) | $8.482 |

## 잡별

| 날짜 | 보고서 | 정답 | blind 판단 | 국가 | 지역 | 도시 | 오차 km | 2패스 offered/relied | 판별자(신규+병합) | 비용 | 상태 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 09-07 14:59 | `report_is_hvammstangi_65.3981_-20.944_260903_095326_ko-en-fr.html` | IS · Hvammstangi | IS · Hvammstangi | hit | hit | hit | 0.44 | 10/7 | 5 (2+3) | $0.343 | OK |
| 09-07 15:02 | `report_jo_jerash_32.288_35.8899_260903_095803_ko-en-fr.html` | JO · Jerash (Wadi Al-Deir / Al-Rayyan area, west of city centre) | JO · Jerash (surrounding hill villages) | hit | hit | hit | 2.60 | 10/4 | 5 (5+0) | $0.562 | OK |
| 09-07 15:04 | `report_ae_al-dhaid_25.2947_55.8809_260903_100319_ko-en-fr.html` | AE · Al Dhaid (Al Hosn / Al Suwaih area) | AE · Al Dhaid | hit | hit | hit | 0.83 | 1/1 | 4 (4+0) | $0.562 | OK |
| 09-07 15:07 | `report_bw_mahalapye_-23.1093_26.8227_260906_202206_ko-en-fr.html` | BW · Mahalapye (Borotsi / Tamocha area, west of A1) | BW · Mahalapye | hit | hit | hit | 1.17 | 10/6 | 5 (5+0) | $0.567 | OK |
| 09-07 15:10 | `report_bo_sucre_-19.0367_-65.2663_260906_203351_ko-en-fr.html` | BO · Sucre | BO · Sucre | hit | hit | hit | 0.63 | 10/5 | 5 (5+0) | $0.579 | OK |
| 09-07 15:13 | `report_za_uitenhage_-33.769_25.3892_260906_203817_ko-en-fr.html` | ZA · Uitenhage (Kariega) | ZA · Uitenhage (Kariega) | hit | hit | hit | 1.13 | 10/7 | 4 (4+0) | $0.582 | OK |
| 09-07 15:15 | `report_jp_keihoku-shimoyuge-kyoto_35.1932_135.6345_260906_204347_ko-en-fr.html` | JP · Keihoku Shimoyuge, Ukyo Ward, Kyoto City | JP · Rural valley village near Fukuchiyama / Tanba area, inland Kinki | hit | hit | miss | 41.63 | 10/5 | 4 (4+0) | $0.600 | OK |
| 09-07 15:18 | `report_es_esquinzo-fuerteventura_28.0717_-14.3123_260906_204807_ko-en-fr.html` | ES · Esquinzo / Butihondo (Jandía), Pájara municipality | ES · Costa Calma / Esquinzo-Butihondo, Jandía | hit | hit | hit | 13.29 | 10/4 | 6 (6+0) | $0.605 | OK |
| 09-07 15:21 | `report_it_fornovo-di-taro_44.6943_10.0983_260906_205248_ko-en-fr.html` | IT · Fornovo di Taro | IT · Fornovo di Taro | hit | hit | hit | 0.22 | 10/5 | 6 (6+0) | $0.605 | OK |
| 09-07 15:24 | `report_fi_turku_60.4575_22.2881_260906_205820_ko-en-fr.html` | FI · Turku | FI · Turku | hit | hit | hit | 0.36 | 10/2 | 5 (5+0) | $0.569 | OK |
| 09-07 15:27 | `report_vn_thuong-dinh-phu-binh_21.492_105.9098_260906_210105_ko-en-fr.html` | VN · Thượng Đình / Hanh market area, Phú Bình district | VN · Thượng Đình / Điềm Thụy commune area, Phú Bình district | hit | hit | hit | 2.80 | 9/8 | 5 (5+0) | $0.616 | OK |
| 09-07 15:30 | `report_de_munich_48.1691_11.5738_260906_215839_ko-en-fr.html` | DE · Munich | DE · Munich | hit | hit | hit | 2.70 | 10/5 | 5 (5+0) | $0.576 | OK |
| 09-07 15:32 | `report_cz_zelec-u-tabora_49.318_14.6477_260906_223536_ko-en-fr.html` | CZ · Želeč (Želeč u Tábora) | CZ · Želeč (near Tábor) | hit | hit | hit | 5.27 | 10/5 | 4 (4+0) | $0.582 | OK |
| 09-07 15:35 | `report_kz_beyneu_46.3143_54.4042_260906_223930_ko-en-fr.html` | KZ · Beyneu | KZ · Beyneu | hit | hit | hit | 101.74 | 10/5 | 5 (5+0) | $0.563 | OK |
| 09-07 16:24 | `report_is_hvammstangi_65.399_-20.9452_260907_162408_en.html` | IS · Hvammstangi | IS · Hvammstangi | hit | hit | hit | 0.42 | 10/9 | 5 (1+4) | $0.571 | OK |

## 이번 기록의 판별자 원자

- **job_959e71524c52** (IS) — Hit at every level: Iceland / Húnaþing vestra / Hvammstangi, 0.44 km from truth. The decisive readable evidence was the Icelandic street blade with ð and the -braut road element, the "HÓTEL HVAMMSTANGI" sign naming the town outright, and the "HEILBRIGÐISSTOFNUN VESTURLANDS" (HVE) clinic branding, wh
  - `atm_88ae5884a999` (병합) [language/toponymy/country] FO>IS **Road-name element -braut marks Iceland, not the Faroes**
  - `atm_4f5a928f1c77` [nature/landform/region] FO>IS **Wide shallow bay with low flat-topped tableland vs steep Faroese sound**
  - `atm_e857853e0979` (병합) [nature/vegetation-cue/country] NO>IS **Planted spruce blocks on bare heath vs Norwegian continuous forest**
  - `atm_895143231e7c` (병합) [economy/civic-building/region] IS>IS **Read the Heilbrigðisstofnun acronym before assuming the nearest region**
  - `atm_48f209c13e3a` [geography/soil-terrain-cue/region] IS>IS **No lava, only rolling grass: north-west Iceland vs the southern volcanic belt**
- **job_da4b902eaf1e** (JO) — HIT: Jordan / Jerash Governorate / western Jerash outskirts (Wadi Al-Deir) confirmed with 2.6 km error. The decisive chain was Arabic-only signage with English (not French/Hebrew) as second language, recurring 077x/079x mobile prefixes on shop fascias, cream limestone cladding with rooftop rebar and
  - `atm_72dbfd6dce2b` [culture/signage-language/country] SY>JO **Jordan vs Syria: mobile prefixes and facade material on shop signs**
  - `atm_403102c39f78` [culture/politics-civic/country] PS>JO **Jordan vs West Bank: prefixes, iconography and Street View coverage**
  - `atm_875df0438462` [nature/vegetation-cue/region] JO>JO **Jerash valley slopes vs Ajloun ridges: pure olive terrace vs pine mix**
  - `atm_04c54e42050d` [geography/urban-form/region] JO>JO **Absence of ruins does not exclude the governorate seat in hilly Jordan**
  - `atm_32e258afc8c1` [architecture/housing-typology/country] EG>JO **Jordanian incremental stone self-build vs Egyptian red brick**
- **job_f374e57f1b56** (AE) — Full hit: UAE / Sharjah Emirate / Al Dhaid, with an 0.83 km coordinate error. The analyst read the decisive text cues correctly — the 06 landline area code (Sharjah and Northern Emirates), explicit "Al Dhaid Sharjah, U.A.E." addresses, the 05x mobile prefixes and UAE tricolour bunting — and correctl
  - `atm_e605fa30bf62` [economy/infrastructure-built/region] AE>AE **UAE 06 landline code = Northern Emirates, not Abu Dhabi oasis**
  - `atm_1b29ea72a049` [language/signage-language/country] OM>AE **UAE vs Oman: mobile prefixes and flag bunting on shopfronts**
  - `atm_d0b59aaa3c23` [geography/urban-form/region] AE>AE **Gulf inland oasis town vs coastal emirate strip**
  - `atm_014560901a69` [language/license-plate/country] SA>AE **UAE vs Saudi Arabia: bilingual English fascias and plate colour**
- **job_b2b72f38b424** (BW) — Botswana / Mahalapye confirmed to within 1.2 km. The decisive visible evidence was the Botswana-specific consumer branding pair (St Louis Lager, Orange with Setswana 'reka airtime fa'), the 'WELCOME TO MAHALAPYE BRIGADE' vocational-centre board, and the transitional Tswana walled compounds with that
  - `atm_b6439b6999a1` [economy/business-chain/country] ZA>BW **St Louis Lager + Orange Setswana ads mean Botswana, not South Africa**
  - `atm_bb7944532780` [culture/education/country] ZA>BW **'Brigade' vocational compounds are a Botswana-only institutional label**
  - `atm_db06a93e906a` [architecture/housing-typology/country] ZA>BW **Absence of palisade fencing separates Botswana villages from South African ones**
  - `atm_b390d2961a02` [geography/settlement-pattern/region] BW>BW **Hardveld A1 corridor vs deep Kalahari inside Botswana**
  - `atm_e5c689a80f57` [geography/urban-form/city] BW>BW **Streetlit road with unkerbed sand verges = outer ward, not town centre**
- **job_41090529fe0b** (BO) — Sucre, Chuquisaca (Bolivia) confirmed with 0.63 km error. The ENTEL blue-oval wall paintings plus Tigo/Tigo Money agent signage fixed Bolivia against Peru/Argentina/Chile; the FANCESA-CONCRETEC construction banner with a 645xxxx local number plus mature valley shade trees and steep raw-brick increme
  - `atm_e4bdaab98801` [economy/business-chain/country] PE>BO **ENTEL blue oval wall paint vs Peru's Movistar/Claro murals**
  - `atm_03f50dbbbf22` [economy/industry/region] BO>BO **FANCESA hoardings mark Chuquisaca; COBOCE marks Cochabamba**
  - `atm_f6ff80704d60` [nature/vegetation-cue/region] BO>BO **Valley canopy vs treeless altiplano in Bolivia**
  - `atm_a23775db5390` [architecture/housing-typology/city] BO>BO **Raw-brick hillside periphery vs whitewashed UNESCO core**
  - `atm_cf43621783bd` [economy/vehicle-fleet/country] AR>BO **Bolivian micro fleet vs Argentine formal city buses**
- **job_72caf482d49b** (ZA) — HIT: South Africa / Eastern Cape / Uitenhage (Kariega) confirmed to within 1.13 km. The chain was carried by explicit, legible text — "nelson mandela bay MUNICIPALITY" on a Municipal Court, "BUCO UITENHAGE 600M" with an 041 code, a Nelson Mandela Bay Metro Fire & Emergency board, and an "EASTERN CAP
  - `atm_98d192cb04cf` [language/signage-language/country] BW>ZA **SA 0xx area codes on shop boards vs Botswana/Namibia national numbers**
  - `atm_a3435d33610c` [architecture/street-furniture/country] BW>ZA **Palisade + burglar bars everywhere means South Africa, not Botswana/Namibia**
  - `atm_49209926afa8` [geography/civic-building/region] ZA>ZA **Municipal branding names the metro: use it before guessing the city**
  - `atm_05d896f43565` [nature/vegetation/region] ZA>ZA **Dry sun-bleached veld with date palms and blue gums = Eastern Cape, not KZN**
- **job_5f8644e78b2c** (JP) — Country and prefecture were correct: Japan / Kyoto Prefecture, but the analyst placed the hamlet in the Fukuchiyama–Ayabe Tanba basin ~42 km west-northwest, whereas the truth is Shimoyuge in the Keihoku area of Ukyo Ward, Kyoto City — the Kitayama forestry valley of the upper Katsura (Kamigamo/Ōi) r
  - `atm_50a2614e4a4c` [economy/industry/region] JP>JP **Kyoto Kitayama forestry valley vs Tanba farming basin**
  - `atm_39acbbf97e17` [geography/landform/region] JP>JP **Basin morphology cannot separate inland Kinki valleys**
  - `atm_fbc7a5e5e104` [geography/settlement-pattern/region] JP>JP **Kyoto City's mountain wards look like deep countryside**
  - `atm_e0e0e7bcfbe4` [architecture/infrastructure-built/country] KR>JP **Japanese U-ditch and amado shutters vs Korean/Taiwanese village fabric**
- **job_1a2be2d9d7ff** (ES) — Full hit: Spain / Fuerteventura / Esquinzo-Butihondo on the Jandía strip, with only 13 km of along-strip error. The decisive chain was EU sign and plate geometry plus Spanish motorway hardware, an ochre eroded (not black lava) basaltic coast with only planted Canary date palms, and resort blocks wit
  - `atm_2259a51ab69b` [nature/soil-terrain-cue/region] ES>ES **Ochre eroded basalt vs black picón: Fuerteventura vs Lanzarote**
  - `atm_5c0a27638e98` [architecture/roof-facade/region] ES>ES **Orange pantile pyramidal resort roofs allowed on Fuerteventura, banned on Lanzarote**
  - `atm_36aae0a58891` [nature/vegetation-cue/region] PT>ES **Canarian desert coast vs Madeira: palms in gravel, not laurisilva**
  - `atm_eccd4326ddf2` [economy/infrastructure-built/country] CV>ES **Spanish state road hardware separates Canaries from Cape Verde**
  - `atm_b333b74897e9` [geography/urban-form/region] ES>ES **Corniche cut vs dune flat: Jandía vs Corralejo/Caleta de Fuste**
  - `atm_26dc0e77b3da` [geography/agriculture/country] ES>ES **Canarian arid coast vs SE mainland Spain: no greenhouses or olives**
- **job_950227aac52b** (IT) — Full hit: Italy / Emilia-Romagna (Parma) / Fornovo di Taro confirmed to within 0.22 km. The decisive readable evidence was the town-entry sign "FORNOVO DI TARO", the brown "Parco Fluviale Regionale del Taro" and "Via Francigena" panels, plus Italian municipal fingerpost wording; the foothill braided
  - `atm_c67e18d2f564` [architecture/roof-facade/region] IT>IT **Pantile roofs = Emilian side; slate roofs = Lunigiana/Ligurian side of the Cisa**
  - `atm_d06a11087118` [nature/landform/region] IT>IT **Braided dry gravel torrent + rounded marl hills = Apennine plain margin, not Po plain**
  - `atm_a26ee42e2c47` [geography/protected-area/region] ES>IT **Italian brown 'Parco Fluviale Regionale' panels name the river and thus the province**
  - `atm_ea64ccafd76b` [language/signage-language/country] ES>IT **Municipal fingerpost generics separate Italy from Spain and France**
  - `atm_2ade05f79dfe` [geography/flag-emblem/country] SM>IT **No Sammarinese hardware: excluding San Marino in Apennine hill towns**
  - `atm_b263ea2c5db6` [economy/industry/region] IT>IT **Food-machinery business fingerposts flag the Parma valley corridors**
- **job_cab0458cd29c** (FI) — HIT: Finland / Turku confirmed with 0.36 km error. The decisive chain was language ("AL-LIIKENNE OY" with the Finnish Oy suffix), the Finnish-over-Swedish "AUTOMAATTI / AUTOMAT" parking plate, the yellow-field warning triangle, and the "unica" student-catering logo which is specific to the Turku uni
  - `atm_b227ef35bd59` [language/signage-language/country] SE>FI **Oy vs AB/AS/OÜ: company suffix pins Finland among Nordic-Baltic lookalikes**
  - `atm_126f0a24f257` [language/signage-language/region] FI>FI **Bilingual line order on Finnish official signs localises the municipality**
  - `atm_a1ebb65130d5` [culture/education/city] FI>FI **Student-catering brands split Finnish university cities**
  - `atm_de07ff165cf0` [geography/road-signage/country] DE>FI **Yellow-field warning triangles: Finland/Nordics vs white-field Central Europe**
  - `atm_a750174aa676` [nature/landform/region] FI>FI **Urban bedrock cuttings vs clay plains inside Finland**
- **job_e1df2b6f7aeb** (VN) — HIT: Vietnam / Thái Nguyên / Phú Bình (Thượng Đình) was confirmed within 2.8 km. The decisive evidence was directly readable: two unrelated shopfronts printed full four-level addresses ending in "Thượng Đình – Phú Bình – Thái Nguyên" and "Điềm Thụy – Phú Bình – Thái Nguyên", plus a state social-insu
  - `atm_724d7e7a7c99` [language/signage-language/country] LA>VN **Vietnamese shop signs print full four-level addresses; Laos/Thailand signs do not use Latin diacritics**
  - `atm_fb8200fcf326` [architecture/housing-typology/region] VN>VN **Kinh lowland tube houses vs upland minority stilt houses in northern Vietnam**
  - `atm_dbf7cd33fdfa` [nature/agriculture/region] VN>VN **Trung du paddy pockets vs Mekong Delta canal grid**
  - `atm_16179f1c9c92` [geography/road-signage/region] VN>VN **Commune-committee blue guide signs mark a commune strip, not a district town**
  - `atm_c4d0a832b76f` [economy/retail/region] VN>VN **Business address commune ≠ camera commune in rural Vietnam**
- **job_8740554c450d** (DE) — HIT: Germany / Bavaria / Munich confirmed, and the analyst even landed in Schwabing-West with only 2.7 km error. The German red 'A' Apotheke emblem, RSA red/white barrier boards with amber blinkers, and an ERDINGER WEISSBRÄU firewall mural excluded Austria and Switzerland and fixed Upper Bavaria; th
  - `atm_96d1691571d2` [culture/business-chain/country] AT>DE **Pharmacy emblem separates Germany from Austria and Switzerland**
  - `atm_4d08129bc533` [culture/street-furniture/country] AT>DE **German RSA roadworks boards vs Austrian/Swiss site furniture**
  - `atm_e8c4d3d9d65a` [geography/infrastructure-built/city] AT>DE **Munich green tram reservations vs Vienna paved tram track**
  - `atm_3a306a118800` [economy/business-chain/region] DE>DE **Bavarian brewery firewall murals localise the state**
  - `atm_46082e3a6ba5` [architecture/urban-form/city] DE>DE **Munich vs Nuremberg/Augsburg tram-city street proportions**
- **job_81dd4684ad63** (CZ) — Full hit: Czechia / South Bohemian Region (Tábor District) / Želeč, 5.3 km error. The Czech-only orthography on the EKO-KOM igloo labels ('PAPÍR', 'SKLO směs', 'DĚKUJEME VÁM, ŽE TŘÍDÍTE') plus the joinery van's 'TRUHLÁŘSTVÍ PÍCHA ŽELEČ' with a .cz address pinned both country and settlement, and the 
  - `atm_647da10d11f2` [language/signage-language/country] SK>CZ **Czech vs Slovak sorted-waste igloo lettering**
  - `atm_db146264aff3` [culture/infrastructure-built/country] AT>CZ **Czech village waste bins vs Austrian German-labelled containers**
  - `atm_302a89d8dbd0` [geography/agriculture/region] CZ>CZ **South Bohemian plateau vs Moravian vineyard lowland**
  - `atm_a4c8bfdf483f` [economy/toponymy/region] CZ>CZ **Tradesman van toponym as district anchor in Czechia**
- **job_773111a6092d** (KZ) — Kazakhstan / Mangystau / Beyneu was confirmed at country, region and city level; only the coordinate estimate was ~100 km off along the same rail-and-highway corridor. The decisive visible cues were the Latin 'STOP' octagon on a GOST-template post, black-white striped concrete delineators and A-fram
  - `atm_3d749d8750f5` [language/signage-language/country] RU>KZ **Latin STOP on GOST posts means Kazakhstan, not Russia**
  - `atm_a17b789438ca` [architecture/housing-typology/region] UZ>KZ **Mangystau new-build walled villas vs Uzbek mahalla courtyards**
  - `atm_cbb4d80090b5` [culture/street-furniture/country] TM>KZ **Saxaul flats without state decor exclude Turkmenistan**
  - `atm_349a166db7f2` [geography/landform/region] KZ>KZ **Beyneu-type rail-trunk crossing vs Mangystau chalk-scarp towns**
  - `atm_d1aae5dd7a36` [economy/utility-pole/region] KZ>KZ **Reinforced A-frame timber poles mark windy western Kazakh corridors**
- **job_0d8cef6d063b** (IS) — HIT: Iceland / Húnaþing vestra / Hvammstangi confirmed to within 0.42 km. The decisive readable evidence was the Icelandic orthography on the street blade ('Norður...braut', with ð), the hotel sign ending '-MSTANGI', and the clinic facade branded 'HEILBRIGÐISSTOFNUN VESTURLANDS' with Lyfja pharmacy 
  - `atm_88ae5884a999` (병합) [language/toponymy/country] FO>IS **Icelandic '-braut' street blades vs Faroese '-vegur/-gøta'**
  - `atm_4f5a928f1c77` (병합) [nature/landform/region] FO>IS **Low flat-topped far shore across a wide bay = Iceland, not a Faroese sound**
  - `atm_895143231e7c` (병합) [economy/civic-building/region] IS>IS **Read the Heilbrigðisstofnun acronym: HVE on a north-facing bay means Húnaþing vestra**
  - `atm_e857853e0979` (병합) [nature/vegetation/country] NO>IS **Planted spruce blocks on bare heath vs Norway's continuous forest**
  - `atm_74079b0c8ba5` [architecture/religious-building/country] FO>IS **Icelandic village core: white spire, rainbow crosswalk, blue-canopy fuel station**

## 유도 확인 (redo)

같은 잡을 `--redo` 로 다시 돌렸을 때 **직전 실행이 만든 판별자**가 2패스 `known_offered` 에 들어왔는가, 그리고 모델이 `relied_on_atoms` 에 적었는가. 경로: conf = 혼동 ISO("X>X2" 의 X 가 blind 추측/대안에 있음) · ent = 엔티티 접점 · geo = 스코프 지오해시 접점.

- **job_959e71524c52** (IS) · 직전 실행 09-07 14:55 판별자 4개 → 이번 2패스 offered **3** · relied **3** · 이번 blind 판단 IS (국가 hit)
  - `atm_e857853e0979` relied via conf,ent,geo
  - `atm_895143231e7c` relied via ent,geo
  - `atm_efacbff46c0c` not offered via —
  - `atm_88ae5884a999` relied via conf,ent,geo

## 학습 곡선 (시간순, corrections.jsonl)

| 시각 | 잡 | 정답 | blind | 국가 | 오차 km | 2패스 | revised | 판별자 | hits/misses | 비용 |
|---|---|---|---|---|---|---|---|---|---|---|
| 09-07 14:55 | job_959e71524c52 | IS | IS | hit | 0.44 | 10/6 |  | 4 | 5/0 | $0.546 |
| 09-07 14:59 | job_959e71524c52 | IS | IS | hit | 0.44 | 10/7 |  | 5 | 6/0 | $0.343 |
| 09-07 15:02 | job_da4b902eaf1e | JO | JO | hit | 2.60 | 10/4 |  | 5 | 3/1 | $0.562 |
| 09-07 15:04 | job_f374e57f1b56 | AE | AE | hit | 0.83 | 1/1 |  | 4 | 0/1 | $0.562 |
| 09-07 15:07 | job_b2b72f38b424 | BW | BW | hit | 1.17 | 10/6 |  | 5 | 6/0 | $0.567 |
| 09-07 15:10 | job_41090529fe0b | BO | BO | hit | 0.63 | 10/5 |  | 5 | 5/0 | $0.579 |
| 09-07 15:13 | job_72caf482d49b | ZA | ZA | hit | 1.13 | 10/7 |  | 4 | 7/0 | $0.582 |
| 09-07 15:15 | job_5f8644e78b2c | JP | JP | hit | 41.63 | 10/5 |  | 4 | 4/1 | $0.600 |
| 09-07 15:18 | job_1a2be2d9d7ff | ES | ES | hit | 13.29 | 10/4 |  | 6 | 4/0 | $0.605 |
| 09-07 15:21 | job_950227aac52b | IT | IT | hit | 0.22 | 10/5 |  | 6 | 5/0 | $0.605 |
| 09-07 15:24 | job_cab0458cd29c | FI | FI | hit | 0.36 | 10/2 |  | 5 | 0/0 | $0.569 |
| 09-07 15:27 | job_e1df2b6f7aeb | VN | VN | hit | 2.80 | 9/8 |  | 5 | 7/1 | $0.616 |
| 09-07 15:30 | job_8740554c450d | DE | DE | hit | 2.70 | 10/5 |  | 5 | 5/0 | $0.576 |
| 09-07 15:32 | job_81dd4684ad63 | CZ | CZ | hit | 5.27 | 10/5 |  | 4 | 5/0 | $0.582 |
| 09-07 15:35 | job_773111a6092d | KZ | KZ | hit | 101.74 | 10/5 |  | 5 | 5/0 | $0.563 |
| 09-07 16:24 | job_0d8cef6d063b | IS | IS | hit | 0.42 | 10/9 |  | 5 | 9/0 | $0.571 |

## 파일

- `corr_<job_id>.json` — 잡 1건의 전체 기록(blind 두 패스·verdict·정정 도구 출력·적재·채점). redo 는 덮어쓴다.
- `corrections.jsonl` — 실행 1회 = 1줄(시계열). redo 도 줄이 추가된다.
- 원자 자체는 `docs/knowledge/atoms/` 에 kind=discriminator · origin=correction · tier=unaided 로, 회상 로그는 `docs/knowledge/recall_log.jsonl`(mode=blind-p2), 채점은 `docs/knowledge/evidence_log.jsonl`.

## 재실행

```
uv run geoguesshelper correct run --dry-run          # 대상·비용 추정만
uv run geoguesshelper correct run --limit 1
uv run geoguesshelper correct run --redo --job <id>  # 유도 확인
uv run geoguesshelper correct run                    # 전부(OK 인 잡은 건너뜀)
uv run geoguesshelper correct report                 # 이 문서 재생성(LLM 호출 없음)
```
