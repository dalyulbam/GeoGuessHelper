# 자동 정정 루프 — 정정 원장

생성 2026-09-16 00:57 · 잡 32건(최신 기록, 그중 OK 32) · 실행 46회(corrections.jsonl) · 이번 기록 비용 $11.472 · 누적 실행 비용 $18.724 · 기획: docs/plan/impl-spec_260907.md §3

같은 캡처를 지도 없이(blind) 다시 판단하고(회상 원자가 있으면 2패스), 실측 pano 좌표·aided 분석과 대조해 "X 는 사실 X2 였다"는 정정을 만들어 kind=discriminator 원자로 적재한다. 사람 승인은 없다 — 회상돼 쓰인 원자는 confirming/misled 로 채점되어 hits/misses 가 오르내리고, 오답만 뒷받침한 원자는 retracted(회상 제외)된다.

## 집계

| 지표 | 값 |
|---|---|
| 국가 적중률(blind 최종) | 32/32 (100%) |
| 지역 적중률 | 26/32 (81%) |
| 도시 적중률 | 22/32 (69%) |
| 좌표 오차 km 중앙값 | 13.29 (n=32) |
| 오차 분포 | <1 10 · <10 6 · <100 11 · >=100 5 · na 0 |
| 2패스 사용(회상 원자 있음) | 31/32 |
| 2패스로 판단이 바뀐 건수(revised) | 0 |
| 판별자 원자 — 이번 기록에서 신규 / 병합 | 127 / 23 |
| 저장소의 kind=discriminator 원자(누적) | 136 |
| 철회된 원자 — 이번 기록 / 저장소 현재 status=retracted | 0 / 0 |
| 총비용(최신 기록 합) | $11.472 |

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
| 09-14 13:46 | `report_mx_irapuato_20.5606_-101.3794_260911_000832_ko-en-fr.html` | MX · Colonia El Palomar / Yóstiro (near Irapuato) | MX · Rural highway between Pénjamo and Cuerámaro area, Bajío lowlands | hit | hit | miss | 22.87 | 10/0 | 4 (4+0) | $0.000 | OK |
| 09-14 13:46 | `report_ng_ogoja_6.6599_8.8026_260910_124856_ko-en-fr.html` | NG · Ogoja | NG · Ogoja | hit | hit | hit | 0.89 | — | 5 (4+1) | $0.000 | OK |
| 09-14 13:46 | `report_co_cumaral_4.2716_-73.4903_260910_130524_ko-en-fr.html` | CO · Cumaral | CO · Cumaral | hit | hit | hit | 0.24 | 1/1 | 5 (4+1) | $0.000 | OK |
| 09-14 13:46 | `report_co_cumaral_4.2716_-73.4895_260910_234145_ko-en-fr.html` | CO · Cumaral | CO · Acacías / Granada area piedmont town, Meta | hit | miss | miss | 86.38 | 7/6 | 4 (2+2) | $0.000 | OK |
| 09-14 13:46 | `report_cl_quebrada-de-los-choros_-29.3752_-70.9551_260910_234553_ko-en-fr.html` | CL · Quebrada de los Choros / Chacho Martínez (Huasco valley area) | CL · Vallenar / Alto del Carmen area, Huasco Valley | hit | hit | hit | 85.92 | 10/4 | 4 (3+1) | $0.000 | OK |
| 09-14 13:46 | `report_tr_milas_37.4196_27.5948_260910_235044_ko-en-fr.html` | TR · Etrenli / Danışment (Milas district) | TR · Milas | hit | hit | hit | 18.44 | 10/6 | 4 (4+0) | $0.000 | OK |
| 09-14 13:46 | `report_th_ban-champa-thong_17.3071_103.5802_260910_235535_ko-en-fr.html` | TH · Ban Champa Thong, Nong Lat subdistrict, Warichaphum district | TH · Wanon Niwat / Ban Cham Pa Thong, Nong Lat subdistrict, Waritchaphum district | hit | hit | hit | 13.37 | 10/8 | 4 (3+1) | $0.000 | OK |
| 09-14 13:46 | `report_mx_la-tapona-mexquitic-de-carmona_22.2311_-101.2282_260911_000322_ko-en-fr.html` | MX · La Tapona, Mexquitic de Carmona | MX · rural village near Villa de Arista / Moctezuma area, Altiplano Potosino | hit | hit | miss | 56.62 | 10/4 | 4 (3+1) | $0.000 | OK |
| 09-14 13:46 | `report_nz_mangatoro_-40.2624_176.2236_260914_125629_ko-en-fr.html` | NZ · Mangatoro / Weber area, near Dannevirke | NZ · Manawatū-Whanganui / Tararua hill country (eastern North Island papa country) | hit | hit | miss | 28.12 | 10/5 | 4 (4+0) | $0.000 | OK |
| 09-14 13:46 | `report_it_rivanazzano-terme_44.9272_8.9683_260914_130123_ko-en-fr.html` | IT · Casalsaglio / Casalvecchio, near Rivanazzano Terme–Voghera | IT · foothill plain south of Parma/Reggio Emilia (e.g. Traversetolo–Montecchio belt) | hit | miss | miss | 118.04 | 10/5 | 4 (4+0) | $0.000 | OK |
| 09-14 13:46 | `report_jp_monzenmachi-susukino_37.3405_136.7847_260914_130520_ko-en-fr.html` | JP · Wajima (Monzenmachi Susukino, Noto Peninsula) | JP · rural hill village (likely Kanto/Tokai hinterland) | hit | miss | miss | 359.38 | 10/3 | 6 (6+0) | $0.000 | OK |
| 09-14 13:46 | `report_es_dos-hermanas_37.2874_-5.917_260914_131110_ko-en-fr.html` | ES · Dos Hermanas | ES · Dos Hermanas (Seville metropolitan area) | hit | hit | hit | 0.74 | 10/2 | 5 (4+1) | $0.000 | OK |
| 09-14 15:14 | `report_ng_ogoja_6.6599_8.8026_260910_131111_ko-en-fr.html` | NG · Ogoja | NG · Ogoja | hit | hit | hit | 0.89 | 8/8 | 6 (1+5) | $0.583 | OK |
| 09-16 00:21 | `report_fr_rethel_49.5097_4.3525_260916_001702_ko-en-fr.html` | FR · Rethel | FR · Chartres area (Lucé / Mainvilliers type suburb) | hit | miss | miss | 243.03 | 10/2 | 4 (4+0) | $0.599 | OK |
| 09-16 00:24 | `report_cl_anilco-villarrica_-39.4654_-72.2604_260916_002050_ko-en-fr.html` | CL · Añilco / Chihuaico (rural, near Villarrica) | CL · Chihuaico / Quetroco rural corridor southwest of Villarrica | hit | miss | hit | 16.09 | 10/5 | 5 (4+1) | $0.598 | OK |
| 09-16 00:50 | `report_at_ziegelwies_48.018_13.6705_260916_004432_ko-en-fr.html` | AT · Ziegelwies (Wolfsegg am Hausruck / Ottnang area) | AT · rural hamlet near Freistadt / Mühlviertel area | hit | hit | miss | 70.36 | 10/2 | 4 (3+1) | $0.566 | OK |
| 09-16 00:53 | `report_ru_poroshino_58.6062_49.7994_260916_005047_ko-en-fr.html` | RU · Poroshino / Talitsa (Vereshchagino district area) | RU · Small district town (likely Lyubim / Poshekhonye type raion center) | hit | miss | miss | 530.37 | 10/4 | 5 (4+1) | $0.644 | OK |

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
- **job_08cebc7d3644** (MX) — HIT at country and region: Mexico / Guanajuato (Bajío) was correctly identified, and the guess landed only ~23 km from the true point near Irapuato; only the intra-Bajío city call (Pénjamo/Cuerámaro rather than Irapuato/Yóstiro) was off. The decisive visible cues were the two-lane rural highway with
  - `atm_f3fb02fa3be5` [geography/soil-terrain-cue/region] MX>MX **Bajío basalt clearance piles vs Altiplano pale calcareous soil**
  - `atm_9aae63fb6cc3` [geography/road-marking/country] GT>MX **Mexican rural two-lane: dashed centre line only, no edge lines**
  - `atm_82d74b56e40b` [geography/settlement-pattern/region] MX>MX **Bajío linear colonia sits close to big cities, not only deep countryside**
  - `atm_e5dc061fdb33` [economy/utility-pole/country] US>MX **CFE rural wooden poles with small transformers vs US rural distribution**
- **job_7974038fd53d** (NG) — Full hit: Nigeria / Cross River North / Ogoja pinned within 0.9 km. The decisive chain was the Nigerian Pidgin billboard slogan plus the named senatorial campaign board, which fixes the northern Cross River senatorial district; the alternatives Ghana, Cameroon and the Igbo heartland were correctly e
  - `atm_73e57a387697` [language/dialect/country] CM>NG **Nigerian Pidgin billboard copy vs Krio or Cameroonian Pidgin**
  - `atm_d97630c7288f` [culture/politics-civic/region] GH>NG **Senatorial-district campaign boards localise Nigerian panos to one district**
  - `atm_c9e4132840cf` (병합) [language/toponymy/region] NG>NG **Non-Igbo personal names in an English-only Nigerian south-east streetscape**
  - `atm_e15595eeb416` [economy/infrastructure-built/region] NG>NG **Solar countdown traffic signals mark Nigerian LGA headquarters towns**
  - `atm_7e5c0b47c99a` [economy/business-chain/country] GH>NG **Independent Nigerian fuel marketers vs Ghanaian chain branding**
- **job_5fed61e7bed8** (CO) — HIT: Colombia / Meta / Cumaral confirmed to within 0.24 km. The decisive chain was the Spanish "SAS" legal suffix plus peso-magnitude pricing ($21.999 with dot thousands separator), the green "Calle 10" blade of the Colombian Calle/Carrera grid, and the pharmacy banner's own branch label reading "Cu
  - `atm_58cde6ebbc3d` [economy/listed-company/country] VE>CO **SAS suffix marks Colombia, not Venezuela or Ecuador**
  - `atm_91b8f6217bd1` (병합) [language/road-signage/country] EC>CO **Green Calle/Carrera blades vs other Latin street signage**
  - `atm_457ca0cf526a` [geography/urban-form/region] CO>CO **Llanos piedmont town fabric vs Amazonian frontier town**
  - `atm_e74ee1efa100` [economy/finance/country] EC>CO **Peso magnitude with dot separators rules out dollarized Ecuador**
  - `atm_1fe840b3212c` [culture/demography/region] CO>CO **Indigenous brand names hint at macro-region inside Colombia**
- **job_84ae6f5d2adb** (CO) — Colombia and the Meta/Llanos piedmont were both correct; the only real error was the metro step — the analyst explicitly ruled OUT Cumaral ("grid too extensive for such small municipalities") and chose Acacías/Granada, giving an 86 km miss. The grid-extent heuristic (Carrera 21 + Calle 10/19 implies
  - `atm_457ca0cf526a` (병합) [geography/urban-form/region] CO>CO **High carrera numbers do not imply a big town in Llanos colonization grids**
  - `atm_0c63c014b962` [nature/vegetation-cue/region] CO>CO **Closed shade canopy + hills right behind town = northern Meta piedmont, not Casanare plains**
  - `atm_91b8f6217bd1` (병합) [language/road-signage/country] VE>CO **Colombian green street blade with separate arrow plate vs Venezuelan plaques**
  - `atm_a4c8d01b5267` [architecture/religious-building/region] CO>CO **Piedmont colonization church vs Andean colonial church**
- **job_40a2fbc96793** (CL) — Chile / Norte Chico / interior Huasco-sector quebrada confirmed at 86 km error. The decisive visible cues were the overhead parronal table-grape trellis with shade netting on a narrow alluvial floor against utterly barren oxidised slopes, the derelict riveted steel truss railway viaduct on concrete 
  - `atm_e591fce7975b` (병합) [economy/agriculture/region] AR>CL **Parronal overhead trellis + shade net = Chile Norte Chico, not Argentine Cuyo**
  - `atm_fe1fabd7b881` [geography/infrastructure-built/region] CL>CL **Bare gravel quebrada road vs paved Elqui/Limarí trunk valley**
  - `atm_ca536dbdc238` [history/industry/region] PE>CL **Derelict steel truss viaduct over a farm valley = Atacama mining branch line**
  - `atm_173fb47af208` [culture/housing-typology/country] PE>CL **Absent adobe/estera housing and mototaxis rules out Peruvian coastal valley**
- **job_6a8a9a2f02f9** (TR) — HIT: Turkey / Muğla Province / Milas district confirmed, with only 18 km error (truth is the Etrenli–Danışment stretch of the dual D525 northwest of Milas, while the guess placed it southeast of Milas). The decisive visible cue was the bus-shelter fascia reading 'T.C. MUĞLA BÜYÜKŞEHİR BELEDİYESİ' wi
  - `atm_ad4651f749ac` [language/road-signage/country] GR>TR **Turkish KGM warning triangle + rain sub-plate vs Greek sign style**
  - `atm_e0ddecfde4f8` [culture/street-furniture/region] TR>TR **'T.C. … BÜYÜKŞEHİR BELEDİYESİ' shelters pin the province in rural Turkey**
  - `atm_1c6af51b0fc1` [nature/vegetation/region] TR>TR **Aegean olive-maquis hinterland vs Taurus/Antalya front**
  - `atm_676b94e88700` [economy/infrastructure-built/region] TR>TR **Muğla dual-carriageway legs: inland Söke road vs Bodrum coastal corridor**
- **job_29de0e559057** (TH) — HIT: Thailand / Sakon Nakhon / Ban Champa Thong (Nong Lat, Warichaphum) was read directly off the green Thesaban Tambon project board, whose Thai script and tambon–amphoe–changwat chain fixed both country and district; the 13 km offset only reflects the guessed position along the village approach ro
  - `atm_e2c9a9aafaf4` (병합) [language/script/country] LA>TH **Thai loops with tone marks vs Lao simplified glyphs on rural project boards**
  - `atm_735a7287ad2c` [geography/soil-terrain-cue/region] TH>TH **Red laterite shoulders on flat plateau mark Isan, not Central Thailand**
  - `atm_a1b98b03e41a` [architecture/housing-typology/region] MY>TH **Hardwood-over-masonry Isan houses vs stilted Malay kampung houses**
  - `atm_9a93da5618de` [language/toponymy/region] TH>TH **Ban/Nong toponyms vs Khmer Prasat/Ta- toponyms inside Isan**
- **job_2567d62419b9** (MX) — HIT at country and state level: Mexico / San Luis Potosí was correct, and the guess landed 57 km from La Tapona (Mexquitic de Carmona) rather than the estimated Villa de Arista/Moctezuma area. The decisive visible evidence was the Chihuahuan Altiplano flora guild (giant branching Yucca filifera, pla
  - `atm_e452dc6b1629` [nature/vegetation/region] MX>MX **Altiplano Potosino vs Zacatecas plateau: mesquite bosque and giant palma china**
  - `atm_4b98d01184f6` [architecture/roof-facade/country] US>MX **Rural Mexico vs US Southwest: castillo-framed brick and square concrete poles**
  - `atm_6073bba1ba34` [economy/agriculture/region] US>MX **Nopal hedge on rock-pile wall marks Mexican ejido parcels, not fenced US rangeland**
  - `atm_82d74b56e40b` (병합) [geography/settlement-pattern/region] MX>MX **Peri-urban ejido fringe can look as remote as a deep-rural rancho**
- **job_1c1d0cff604e** (NZ) — Near-exact hit: the analyst read NZ correctly from the NZTA one-lane-bridge sign pair (yellow diamond narrowing-bridge pictogram + blue priority plate with white/red arrows) and placed it in the eastern North Island papa hill country at -40.05/176.05, only 28 km from the true Tararua/Weber-Mangatoro
  - `atm_c0028c87af29` [geography/road-signage/country] AU>NZ **One-lane bridge: NZ pictogram+blue arrow plate vs Australian worded sign**
  - `atm_935d031e2a8f` [nature/soil-terrain-cue/region] NZ>NZ **Papa mudstone cut banks mark eastern North Island, not South Island greywacke**
  - `atm_30644ea72f98` [nature/climate/region] NZ>NZ **Drought-browned pasture with green gullies = eastern rain shadow, not Taranaki/Waikato**
  - `atm_b2e8cd2ce0f6` [nature/vegetation/country] AU>NZ **Toetoe plumes and broadleaf bush vs eucalypt woodland**
- **job_75b28777ae09** (IT) — Italy was correctly identified from the striped kerbside bins, concrete ENEL poles and plain-brick courtyard farm, but the region was placed ~118 km too far east: the pano is in the Lombard Oltrepò Pavese near Voghera/Rivanazzano, not the Parma–Reggio foothill belt. The analyst explicitly excluded t
  - `atm_ec115d67939d` [geography/agriculture/region] IT>IT **Oltrepò Pavese plain looks vine-free: vines sit behind the first hill line**
  - `atm_b905eb4795d9` [geography/landform/region] IT>IT **Pede-Apennine step is pan-regional: don't convert hill-front proximity into a province**
  - `atm_4ded08bf94f9` [architecture/housing-typology/region] IT>IT **Brick cascina with block shed is Lombard-to-Emilian, not diagnostic of Emilia**
  - `atm_3c739719836a` [economy/agriculture/region] IT>IT **Hay tedder and forage plots mean dairy, not specifically Parmigiano-Reggiano**
- **job_265023c0c00f** (JP) — Country (Japan) was correct, but the region was wrong: the analyst placed the scene in the Kanto/Boso Pacific-side satoyama when it is actually Noto Peninsula, Ishikawa (Hokuriku, Sea-of-Japan side). The decisive error was treating "no snow poles / no snowmelt sprinklers / healthy moso bamboo / stra
  - `atm_eba3dc758411` [geography/road-marking/region] JP>JP **Missing snowmelt sprinklers do not rule out Hokuriku on minor lanes**
  - `atm_18dfb917ecd9` [nature/vegetation/region] JP>JP **Moso bamboo thrives on the Sea-of-Japan coast up to Noto**
  - `atm_76095061912d` [architecture/roof-facade/region] JP>JP **Dark tarred board cladding + metal roof vs Kanto's kawara farm shed**
  - `atm_ebc7c2ef6ce2` [economy/settlement-pattern/region] JP>JP **Depopulating peninsula hamlet vs commuter-belt Kanto satoyama**
  - `atm_9f4060f146e8` [geography/soil-terrain-cue/region] JP>JP **Kanto loam is dark/reddish, not pale brown**
  - `atm_dca606fecd4d` [culture/street-furniture/country] KR>JP **Grated concrete U-ditch and orange-post convex mirror fix Japan, not the region**
- **job_22e8bfa81b66** (ES) — HIT at city level (0.74 km error): Spain / Seville province / Dos Hermanas was correctly read from the azulejo corner street tile with a bare Spanish noun ('COSTURERA'), Sevillian cream-and-salmon adosados with barrel tiles and rejas, DGT-style thin-rimmed prohibition discs, and a completely flat st
  - `atm_ad6ef340394d` (병합) [language/signage-language/region] PT>ES **Andalusian azulejo street plaque vs Portuguese enamel 'RUA' plate**
  - `atm_288c6a410e97` [architecture/housing-typology/region] ES>ES **Sevillian salmon-render adosados vs Levante flat-roof suburbia**
  - `atm_b0dafbf62e75` [geography/landform/region] ES>ES **Flat closed street-end horizon separates Seville plain from Granada/Malaga**
  - `atm_7f58247de8e3` [architecture/urban-form/region] ES>ES **Metro-satellite signature: brick bloques with garage fronts next to adosado estates**
  - `atm_e11e2807a7c5` [culture/road-marking/country] IT>ES **Spanish kerb-and-apron colour coding vs Italian street furniture**
- **job_737cae606637** (NG) — HIT at ~0.9 km: Nigeria / Cross River North / Ogoja was confirmed. The chain worked from the Pidgin campaign billboard with the 'DIST. SEN.' title and a non-Igbo, non-Yoruba surname, an independent hand-lettered 'PET O. OIL AND GAS LIMITED' canopy, and a single solar-lit countdown-signal roundabout 
  - `atm_7e5c0b47c99a` (병합) [economy/business-chain/country] GH>NG **Hand-lettered independent fuel canopies (NG) vs uniform chain livery (GH)**
  - `atm_d97630c7288f` (병합) [culture/politics-civic/region] GH>NG **'DIST. SEN.' billboards lock a pano to one Nigerian senatorial district**
  - `atm_73e57a387697` (병합) [language/signage-language/country] CM>NG **Nigerian Pidgin ad copy vs Krio or francophone-tinged Cameroonian Pidgin**
  - `atm_e15595eeb416` (병합) [economy/infrastructure-built/city] NG>NG **Solar countdown signals at one roundabout = Nigerian LGA headquarters**
  - `atm_c9e4132840cf` (병합) [language/toponymy/region] NG>NG **Minority-ethnic names on boards mark the Cross River–Benue belt, not Igbo core**
  - `atm_4f06d267683b` [geography/landform/region] NG>NG **Inland laterite motor-park town vs Niger Delta creek settlement**
- **job_e08677f6da27** (FR) — Country France was correct, but the analyst placed it in the Eure-et-Loir/Perche belt around Chartres when the pano is actually Rethel in the Ardennes (Grand Est), 243 km NE. The miss came from over-reading a generic post-1970s pavillon lotissement — cream render, claustra walls, brown interlocking 
  - `atm_5627f16f3656` [architecture/housing-typology/country] FR>FR **Pavillon lotissement kit is national, not regional — don't use it to narrow within France**
  - `atm_cb506827dd99` [nature/hydrology/region] FR>FR **Willow-poplar carr at the estate edge means alluvial valley town, not plateau town**
  - `atm_569d526de3f2` [economy/industry/region] FR>FR **Derelict brick/steel workshops inside the housing fabric point north-east, not to Beauce**
  - `atm_82c5df9afaa6` [geography/road-marking/country] BE>FR **Yellow kerb line and STOP hardware fix France but say nothing about the region**
- **job_b0e82f0744fd** (CL) — Chile / Araucanía / the Chihuaico–Quetroco corridor near Villarrica was confirmed: the green MOP sign with 'S-239-T' and 'S-875' letter-block codes plus Villarrica as the arrowed destination pinned both country and locality, and the 16 km error came only from placing the corridor south-west rather t
  - `atm_51fe315e02a2` [geography/road-marking/country] AR>CL **White double centre line marks Chile; yellow marks Argentina on the Andean flank**
  - `atm_530209fff20e` (병합) [geography/road-signage/country] AR>CL **MOP letter-number codes in white boxes vs Argentine green pentagon RP shields**
  - `atm_5c9530efe534` [geography/road-signage/region] CL>CL **An arrowed town name fixes the axis, not the side — check junction pair, not terrain feel**
  - `atm_e7200d85a9af` [culture/settlement-pattern/region] CL>CL **Araucanía Mapuche smallholder frontier vs Llanquihue colono belt**
  - `atm_d67c3162a1df` [nature/vegetation-cue/country] NZ>CL **Foxglove verges and Nothofagus/radiata mix mark the Chilean rainy south, not NZ**
- **job_d73fa5a85866** (AT) — Austria and Upper Austria were both correct; only the intra-province placement drifted ~70 km, because the analyst leaned on "Mühlviertel granite plateau" while the truth is the Hausruckviertel, the Alpine-foreland hill country south of the Danube. The decisive country-level cues (deep-coloured rend
  - `atm_2b6dc49fc1c4` [geography/landform/region] AT>AT **Hausruck molasse hills vs Mühlviertel granite plateau in Upper Austria**
  - `atm_db146264aff3` (병합) [culture/infrastructure-built/country] CZ>AT **Austrian per-household wheeled bins vs Czech igloo cluster**
  - `atm_b810b26db38d` [architecture/housing-typology/country] DE>AT **Austrian Einfamilienhaus render palette vs German rural render**
  - `atm_43d855f5ee3f` [culture/road-signage/country] CH>AT **Yellow blade waymarkers and unmarked Güterwege vs Swiss road furniture**
- **job_7f80274db73c** (RU) — Country was correct (Russia), but the analyst placed the scene in the Yaroslavl–Kostroma–Vologda triangle when the truth lies ~530 km east, on the Kirov/Perm (Vyatka–Kama, Cis-Ural) taiga margin. The miss came from treating 19th-century bare-brick merchant houses with kokoshnik hoods, silicate-brick
  - `atm_efd274cc5e3b` [architecture/roof-facade/country] RU>RU **Provincial bare-brick merchant houses are pan-Russian, not an Upper Volga fingerprint**
  - `atm_662e72e8fe22` [economy/industry/region] RU>RU **Timber-yard retail strips flag Vyatka–Kama taiga raion towns over Upper Volga ones**
  - `atm_f45fd2528590` [nature/vegetation-cue/region] RU>RU **Southern-taiga spruce-birch without oak is a belt, not a point**
  - `atm_e5601aaeca59` (병합) [language/signage-language/region] RU>RU **Absent titular-republic markers do not mean 'move west'**
  - `atm_7291fe70d227` [geography/landform/region] RU>RU **Flat terrain does not exclude the Cis-Urals**

## 유도 확인 (redo)

같은 잡을 `--redo` 로 다시 돌렸을 때 **직전 실행이 만든 판별자**가 2패스 `known_offered` 에 들어왔는가, 그리고 모델이 `relied_on_atoms` 에 적었는가. 경로: conf = 혼동 ISO("X>X2" 의 X 가 blind 추측/대안에 있음) · ent = 엔티티 접점 · geo = 스코프 지오해시 접점.

- **job_959e71524c52** (IS) · 직전 실행 09-07 14:55 판별자 4개 → 이번 2패스 offered **3** · relied **3** · 이번 blind 판단 IS (국가 hit)
  - `atm_e857853e0979` relied via conf,ent,geo
  - `atm_895143231e7c` relied via ent,geo
  - `atm_efacbff46c0c` not offered via —
  - `atm_88ae5884a999` relied via conf,ent,geo
- **job_737cae606637** (NG) · 직전 실행 09-10 13:14 판별자 0개 → 이번 2패스 offered **0** · relied **0** · 이번 blind 판단 NG (국가 hit)

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
| 09-10 12:50 | job_7974038fd53d | NG | NG | hit | 0.89 | 0/0 |  | 5 | 0/0 | $0.431 |
| 09-10 13:11 | job_5fed61e7bed8 | CO | CO | hit | 0.24 | 1/1 |  | 5 | 0/0 | $0.493 |
| 09-10 13:14 | job_737cae606637 | NG | None | None | — | 0/0 |  | 0 | 0/0 | $0.000 |
| 09-10 23:55 | job_84ae6f5d2adb | CO | CO | hit | 86.38 | 7/6 |  | 4 | 0/0 | $0.586 |
| 09-11 00:08 | job_40a2fbc96793 | CL | CL | hit | 85.92 | 10/4 |  | 4 | 0/0 | $0.552 |
| 09-11 00:11 | job_6a8a9a2f02f9 | TR | TR | hit | 18.44 | 10/6 |  | 4 | 0/0 | $0.587 |
| 09-11 00:14 | job_29de0e559057 | TH | TH | hit | 13.37 | 10/8 |  | 4 | 0/0 | $0.576 |
| 09-11 00:16 | job_2567d62419b9 | MX | MX | hit | 56.62 | 10/4 |  | 4 | 0/0 | $0.599 |
| 09-11 00:19 | job_08cebc7d3644 | MX | MX | hit | 22.87 | 10/0 |  | 4 | 0/0 | $0.600 |
| 09-14 13:11 | job_1c1d0cff604e | NZ | NZ | hit | 28.12 | 10/5 |  | 4 | 0/0 | $0.543 |
| 09-14 13:13 | job_75b28777ae09 | IT | IT | hit | 118.04 | 10/5 |  | 4 | 0/0 | $0.565 |
| 09-14 13:16 | job_265023c0c00f | JP | JP | hit | 359.38 | 10/3 |  | 6 | 0/0 | $0.593 |
| 09-14 13:19 | job_22e8bfa81b66 | ES | ES | hit | 0.74 | 10/2 |  | 5 | 0/0 | $0.580 |
| 09-14 13:46 | job_08cebc7d3644 | MX | MX | hit | 22.87 | 10/0 |  | 4 | 3/0 | $0.000 |
| 09-14 13:46 | job_7974038fd53d | NG | NG | hit | 0.89 | 0/0 |  | 5 | 0/0 | $0.000 |
| 09-14 13:46 | job_5fed61e7bed8 | CO | CO | hit | 0.24 | 1/1 |  | 5 | 0/1 | $0.000 |
| 09-14 13:46 | job_84ae6f5d2adb | CO | CO | hit | 86.38 | 7/6 |  | 4 | 3/1 | $0.000 |
| 09-14 13:46 | job_40a2fbc96793 | CL | CL | hit | 85.92 | 10/4 |  | 4 | 4/0 | $0.000 |
| 09-14 13:46 | job_6a8a9a2f02f9 | TR | TR | hit | 18.44 | 10/6 |  | 4 | 6/0 | $0.000 |
| 09-14 13:46 | job_29de0e559057 | TH | TH | hit | 13.37 | 10/8 |  | 4 | 6/0 | $0.000 |
| 09-14 13:46 | job_2567d62419b9 | MX | MX | hit | 56.62 | 10/4 |  | 4 | 4/0 | $0.000 |
| 09-14 13:46 | job_1c1d0cff604e | NZ | NZ | hit | 28.12 | 10/5 |  | 4 | 5/0 | $0.000 |
| 09-14 13:46 | job_75b28777ae09 | IT | IT | hit | 118.04 | 10/5 |  | 4 | 1/3 | $0.000 |
| 09-14 13:46 | job_265023c0c00f | JP | JP | hit | 359.38 | 10/3 |  | 6 | 2/1 | $0.000 |
| 09-14 13:46 | job_22e8bfa81b66 | ES | ES | hit | 0.74 | 10/2 |  | 5 | 2/0 | $0.000 |
| 09-14 15:14 | job_737cae606637 | NG | NG | hit | 0.89 | 8/8 |  | 6 | 8/0 | $0.583 |
| 09-16 00:21 | job_e08677f6da27 | FR | FR | hit | 243.03 | 10/2 |  | 4 | 2/1 | $0.599 |
| 09-16 00:24 | job_b0e82f0744fd | CL | CL | hit | 16.09 | 10/5 |  | 5 | 4/1 | $0.598 |
| 09-16 00:50 | job_d73fa5a85866 | AT | AT | hit | 70.36 | 10/2 |  | 4 | 2/0 | $0.566 |
| 09-16 00:53 | job_7f80274db73c | RU | RU | hit | 530.37 | 10/4 |  | 5 | 3/1 | $0.644 |

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
