---
{"job": "job_72caf482d49b", "report": "report_za_uitenhage_-33.769_25.3892_260906_203817_ko-en-fr.html", "truth": {"lat": -33.76791364929174, "lng": 25.39014908652164, "iso": "ZA", "file_lat": -33.769, "file_lng": 25.3892, "n_panos": 7}, "lang": "en", "cost_usd": 0.7056, "created": 1788740669.728717}
---

# 선택 장면 보고서 · ZexpLPkD…L-2g — blind vs aided

보고서 `report_za_uitenhage_-33.769_25.3892_260906_203817_ko-en-fr.html` · 캡처 6장 · 정답(pano 평균) -33.76791364929174, 25.39014908652164 · ISO ZA

## 지표

| | blind (로드뷰만) | aided (지도 포함) | aided_prior (원 보고서) |
|---|---|---|---|
| 국가 | South Africa (ZA) | South Africa (ZA) | South Africa |
| 국가 적중 | True | True | True |
| 지역 / 도시 | Eastern Cape / Uitenhage (Kariega) | Eastern Cape (Nelson Mandela Bay Metro) / Kariega (Uitenhage) | Eastern Cape (Nelson Mandela Bay Metro) / Uitenhage (Kariega) |
| 좌표 오차 km | **0.98** | **0.64** | 0.75 |
| 도달 level | district | district | district |
| 단계 수 (ruled_out 있는 단계) | 5 (5) | 5 (5) | 5 (5) |
| 단서 수 | 11 | 11 | 11 |
| 확신 | 0.9 | 0.93 | 0.93 |

## 로직 변화 (감사 서술)

**판정**: `blind-sufficient`

**blind 의 추론**: The blind chain moved from left-hand traffic plus palisade/burglar-bar security architecture to Southern Africa, then used SA-only retail and utility brands (BUCO, Gascor) with 041/082 dialling prefixes and the 'nelson mandela bay municipality' logo to lock South Africa. The Eastern Cape Autoweek billboard and metro branding gave the province and metro. Two explicit 'Uitenhage' signs plus a '61 Cuyler Street' address on a builders' merchant produced the town and then the light-industrial Cuyler Street / Graaff-Reinet Road corridor near the fire station and municipal court. Every narrowing step was carried by legible in-image text.

**aided 의 추론**: The aided chain reaches the same continent, country, province and metro through the same cues — left-hand traffic, BUCO/Gascor, 041 landlines, the Nelson Mandela Bay logo, the Eastern Cape Autoweek billboard. At town level it repeats 'BUCO UITENHAGE', the municipal court and fire station, adding an architectural argument about 1930s red-brick rail-and-motor industrial stock. At district level it again cites '61 Cuyler Street' and the court/fire cluster, but now names the R334 route number, 'Kariega Central / Uitenhage Lower Central', Penford, Motherwell, Caledon Street and a 'Swartkops River bench'.

**달라진 것**: Nothing structural changed: the map added no evidentiary step the blind chain lacked, since the blind chain had already read the town name and street address off signage. What the map added is vocabulary and false precision — route number R334, suburb names (Kariega Central, Penford, Motherwell, Uitenhage Lower Central), the neighbouring Caledon Street retail core, and the Swartkops River bench. These are gazetteer/basemap labels, not things visible in the panel, and the discriminators built on them ('Penford is residential', 'northern Kariega Central lacks the civic cluster') are post-hoc rationalisations of a marker already seen. Confidence rose only 0.90 to 0.93, which is the tell that the map confirmed rather than enabled. The heritage-brick argument at metro_area is also post-hoc dressing on a conclusion the branch signage had already given.

**지도가 있어야만 가능했던 단계**:
- district: naming the artery as R334-carrying — no route shield is cited anywhere in either chain
- district: 'Kariega Central / Uitenhage Lower Central' as the named locality label
- district: ruling out 'Penford' and 'Northern Kariega Central' — suburb names available only from a basemap
- district: 'near the Traffic Department and Swartkops River bench' — neither is observed in the imagery
- metro_area: ruling out 'Motherwell' as a comparison town, a candidate only salient once the metro is drawn on a map
- district: contrasting with the 'Caledon Street' retail core by name

## blind 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | None | Left-hand traffic, dry winter grass under a high sun, palms plus eucalypts, universal security fencing and window bars, bakkie-heavy fleet. | Australia would show yellow-and-black route shields, 'GIVE WAY' text and hardwood power poles; Southern Europe would drive on the right with EU-blue plate bands; Latin America would show Spanish/Portuguese signage. Instead: English-only signage, red-triangle yield without text, deep-set burglar bars and palisade fencing typical of Southern Africa. | Australia/NZ — signage typography and security hardening do not match, Southern Europe — wrong driving side and language, Latin America — language | **Southern Africa, southern hemisphere, left-hand traffic** |
| `country` | None | BUCO Hardware & Buildware, Gascor LPG, 'nelson mandela bay municipality' logo, 041 area code, 082 mobile prefix. | Namibia/Botswana/Zimbabwe would show different retail chains (Pupkewitz, Choppies) and no 'Nelson Mandela Bay' branding; South African-only chains BUCO and Gascor plus the SA municipal corporate identity settle it. | Namibia — 061/064 codes and Namibian municipal branding absent, Botswana/Zimbabwe — chains and municipal identity do not exist there | **South Africa** |
| `admin_region` | None | Municipal court branded Nelson Mandela Bay; billboard for South African Autoweek with 'EASTERN CAPE' and provincial crest; phone numbers all 041. | A Gauteng location would carry City of Johannesburg/Tshwane branding and 011/012 codes; Western Cape would show City of Cape Town and 021; KZN would show eThekwini and 031. Here the metro is explicitly Nelson Mandela Bay with 041, and a billboard names Eastern Cape. | Gauteng, Western Cape, KwaZulu-Natal | **Eastern Cape, Nelson Mandela Bay Metro** |
| `metro_area` | None | Two independent signs name Uitenhage; a builders' merchant lists '61 Cuyler Street'. | Gqeberha central would show coastal dune vegetation, multi-storey CBD blocks and 'Port Elizabeth/Gqeberha' municipal sub-branding; Despatch is far smaller with no metro fire station or municipal court. Here signs read 'BUCO UITENHAGE' and 'Fire & Emergency ... UITEN...', and an address on Cuyler Street. | Gqeberha central — different sub-branding and denser CBD fabric, Despatch — lacks these metro facilities, Colchester — rural village, no industrial strip | **Uitenhage (Kariega), the metro's north-western industrial town** |
| `district` | None | Wide arterial with grass median at the truck junction; behind it low warehouses, unsealed forecourts, palms and old eucalypts; municipal court and fire station within the same few hundred metres. | The CBD proper would show a continuous retail shopfront terrace with awnings and pedestrian density; a residential suburb would show walled houses and no builders' merchants. Instead we get single-storey sheds, gravel yards, aluminium fabricators, a gas depot and warehouse 'TO LET' boards — a fringe industrial street — anchored by the Cuyler Street address and the BUCO 600 m sign. | Uitenhage CBD retail core, Purely residential suburbs | **Uitenhage light-industrial fringe along the Cuyler Street / Graaff-Reinet Road corridor, near the metro fire station and municipal court** |

## aided 추론 사슬

| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |
|---|---|---|---|---|---|
| `continent` | Which continent and which driving side? | Vehicles keep left, dry grass verges, mixed palm/eucalyptus/thorn vegetation, heavy palisade fencing and burglar bars on all buildings, sun in the northern sky. | Australia would show yellow-on-black diamond warning signs and no palisade-and-razor security fencing on every commercial plot; Mediterranean Europe drives on the right and would not have this dry thicket/palm-eucalyptus mix with corrugated-iron industrial sheds. | Australia: sign typology and security architecture differ, Southern Europe/South America: right-hand traffic | **Southern Africa, left-hand traffic** |
| `country` | Which southern African state? | SADC-standard red triangular yield sign, BUCO hardware chain branding, Gascor/Air Products LPG boards, 041 landline and 082 cellular prefixes, 'Office Hours 07h30-15h45' time format. | Namibia would use N-prefixed routes, German/Afrikaans bilingual town signage and far sparser commercial density; Botswana/Zimbabwe lack BUCO and the 0xx three-digit landline scheme shown, and their municipal branding differs entirely. | Namibia: no BUCO/041 numbering, different municipal identity, Botswana and Zimbabwe: brands and phone formats do not match | **South Africa** |
| `admin_region` | Which province / metropolitan municipality? | Municipal building carries the 'nelson mandela bay MUNICIPALITY' logo; billboard advertises 'South African Autoweek, Eastern Cape'; landlines all begin 041. | Gauteng would show City of Johannesburg/Tshwane livery and 011/012 codes; Western Cape would show City of Cape Town green-and-white branding and 021 codes; KZN would show eThekwini branding, 031 codes and lush subtropical vegetation instead of this dry Albany thicket. | Gauteng, Western Cape, KwaZulu-Natal: wrong municipal branding and dialling codes | **Eastern Cape, Nelson Mandela Bay Metro** |
| `metro_area` | Which town inside the Nelson Mandela Bay metro? | Explicit branch naming 'BUCO UITENHAGE', a Uitenhage municipal court and metropolitan fire station, and 1930s-era red-brick/corrugated-roof industrial buildings typical of Uitenhage's rail-and-motor heritage rather than PE's coastal CBD. | Gqeberha's core would show coastal dune vegetation, larger multi-storey CBD blocks and 'Port Elizabeth/Gqeberha' branch naming; Despatch is far smaller with no municipal court or fire station of this scale; Motherwell is a residential township without this pre-war brick industrial stock. | Gqeberha core: different urban morphology and branch naming, Despatch and Motherwell: no metro court/fire complex or heritage industrial stock | **Kariega / Uitenhage** |
| `district` | Which street corridor within Uitenhage? | MRB Suppliers lists '61 Cuyler Street'; the municipal court, metropolitan fire and emergency station, and a BUCO branch 600 m away all cluster along the same wide R334-carrying artery with unpaved side verges and light-industrial plots. | Caledon Street central would show retail frontage (KFC, banks, continuous shopfront verandas) rather than aluminium fabricators and gas distributors; Penford is residential with no commercial palisade strip; the north end of Kariega Central lacks the municipal court/fire station cluster. | Caledon Street retail core: different land use, Penford: residential, Northern Kariega Central: lacks the civic cluster | **Cuyler Street corridor, Kariega Central / Uitenhage Lower Central, near the Traffic Department and Swartkops River bench** |

## 단서 대조

- 공통(unaided) 10 · aided 에만 1 · blind 에만 1

### aided 에서만 나온 단서 (보조 지식 의존 가능성)
- [urban_fabric/district] Low-rise light-industrial strip: aluminium manufacturers, cash & carry, unpaved verges, wide street reserve — Uitenhage's Cuyler/Lower Central industrial belt near the VW-driven automotive economy

### blind 에서만 나온 단서 (지도가 있으면 사라진 관찰)
- [signage/metro_area] Blue municipal board reading 'NEL... METROPO... FIRE & EM... UITEN...' (Nelson Mandela Bay Metropolitan Fire & Emergency, Uitenhage)

### 공통 단서 (일반 추론)
- [signage] Billboard for 'South African Autoweek, 1-3 Oct 2025, Eastern Cape' with Eastern Cape provincial and G20 logos ⇄ Billboard for 'South African Autoweek 1-3 Oct 2025, Eastern Cape' with Eastern Cape provincial and G20 logos (sim 1.73)
- [language] Afrikaans surname business branding 'MULDER Property Rentals' alongside English-only signage ⇄ Afrikaans surname business branding ('MULDER Property Rentals'), English trade signage, 082 Vodacom mobile prefix (sim 1.38)
- [signage] 'nelson mandela bay municipality — Municipal Court, Office Hours: 07h30 - 15h45' — 24h-style time written with 'h' separator is a South African convention ⇄ Blue municipal sign reads 'nelson mandela bay MUNICIPALITY - Municipal Court, Office Hours 07h30 - 15h45' — the 07h30 time format is distinctly South African (sim 1.23)
- [vehicles] Heavy presence of half-ton bakkies (Ford Ranger, Hyundai H100 dropside) and a Renault Kwid — a distinctly South African vehicle mix ⇄ Fleet dominated by white bakkies (Ford Ranger, Hyundai H100 drop-side) and a Renault Kwid — a vehicle mix specific to the South African market (sim 1.19)
- [vegetation] Canary Island date palms mixed with mature eucalyptus and dry winter grass verges ⇄ Canary/date palms mixed with eucalyptus and thorn trees over dry yellow-brown grass, strong low sun and deep blue sky (sim 0.97)
- [traffic_infrastructure] Inverted red-bordered triangular YIELD sign and pedestrian-crossing sign on a leaning pole; painted zebra/chevron road markings; traffic keeps left ⇄ Traffic moves on the left; red-bordered inverted-triangle YIELD sign paired with a pedestrian-crossing plate of the South African SADC-standard design (sim 0.85)
- [commerce] 'MRB Building Materials Suppliers, 61 Cuyler Street, Tel 041 991 ...' — Cuyler Street is a Uitenhage main street; 041 is the Gqeberha/Uitenhage area code ⇄ MRB Building Materials Suppliers gives its address as '61 Cuyler Street' with 041 phone prefix — Cuyler Street is the main artery through Uitenhage/Kariega central (sim 0.79)
- [commerce] 'Gascor' LPG distributor with Air Products / ESAB accreditation boards, 'LP Gas, Welding & Gas Consumables, Paraffin & Wood' ⇄ Gascor and Air Products LPG distributor signage with 041 landline; ESAB welding board — South African industrial supply chain (sim 0.64)
- [signage] BUCO hardware directional sign reads 'BUCO UITENHAGE 600M' with phone code 041 ⇄ BUCO Hardware & Building Materials sign explicitly reading 'BUCO UITENHAGE' with 041 dialling code (Port Elizabeth/Gqeberha area code) (sim 0.6)
- [architecture] Single-storey face-brick and plastered municipal building with green corrugated-iron roof, burglar bars on every window, palisade steel fencing ⇄ Tall galvanised palisade fencing and burglar bars on every window — the South African urban security vernacular (sim 0.59)

## 원자 계층 (샌드박스 적재)

| 계층 | 의미 | 개수 |
|---|---|---|
| unaided | 로드뷰만으로 추론되는 일반 사실 | 17 |
| aided | 지도(보조 지식)가 있어야만 나온 사실 | 0 |
| blind-only | 지도 없이 볼 때만 나온 관찰 | 1 |

### unaided
- `atm_3576bd565590` [architecture/road-signage/point] South African Autoweek billboard with Eastern Cape provincial logo
- `atm_76597c6a1141` [economy/business-chain/point] Afrikaans surname property rental business signage
- `atm_e74b18b19959` [economy/business-chain/point] MRB Building Materials Suppliers on Cuyler Street with 041 area code
- `atm_1f13b3e189ab` [economy/vehicle-fleet/point] South African bakkie-dominant vehicle mix
- `atm_4470109c57eb` [architecture/road-signage/point] South African 'h' time notation on municipal office sign
- `atm_e84d4e933277` [nature/vegetation-cue/point] Canary Island date palms with eucalyptus and dry winter grass
- `atm_7311df0e4fa3` [history/industry/city] Uitenhage/Kariega automotive manufacturing legacy
- `atm_990ab226093b` [geography/infrastructure-built/country] South African phone number area codes as regional locator
- `atm_3b89499a7ce5` [economy/business-chain/point] Gascor LPG distributor with Air Products/ESAB accreditation
- `atm_f10353f07da8` [economy/business-chain/country] South African national retail/hardware chains as locator signal
- `atm_4fbaa0d62307` [architecture/road-signage/point] Inverted triangular yield sign on leaning pole with left-hand traffic
- `atm_32a347b0ab5e` [culture/demography/region] Afrikaans-English commercial layer over isiXhosa-majority Eastern Cape population
- `atm_480bec883360` [architecture/street-furniture/point] Face-brick municipal building with palisade fencing and burglar bars
- `atm_a428bc42f357` [architecture/road-signage/point] BUCO hardware directional sign with local phone code
- `atm_b25072b97c43` [culture/toponymy/country] Administrative address signage convention in Vietnam names commune-district-province
- `atm_65012271441e` [architecture/bollard-guardrail/point] NJ concrete median barrier with tapered edge barriers
- `atm_cdf02553d9d6` [culture/bollard-guardrail/country] South African security hardening as vernacular marker

### blind-only
- `atm_036be6c811e8` [geography/urban-form/city] Light-industrial fringe corridor vs CBD retail core visual distinction
