"""보고서/분석 출력 언어(i18n).

- LANGUAGES: 지원 언어 레지스트리 (code, 원어명, Claude 지시용 영어명).
- claude_language_directive(): 분석 프롬프트에 붙일 "이 언어로 써라" 지시문.
- report_strings(lang): 리포트 HTML 의 고정 라벨(챕터명/각주 등). 누락 키는 영어로 폴백.
- enum_label(lang, kind, value): driving_side/hemisphere/weight 등 영어 enum 을 언어별 표기로.

설계: 분석 '내용'(관찰·문화경제 서술 등)은 Claude 가 대상 언어로 직접 쓰고, driving_side/
hemisphere/weight/country_iso 같은 '로직 enum' 은 영어로 고정 → 코드/CSS 가 안정적으로 처리하고
표시만 언어별로 매핑한다.
"""
from __future__ import annotations

# (code, 원어 표기, Claude 지시용 영어명)
LANGUAGES: list[tuple[str, str, str]] = [
    ("ko", "한국어", "Korean"),
    ("en", "English", "English"),
    ("ja", "日本語", "Japanese"),
    ("zh", "中文", "Chinese (Simplified)"),
    ("es", "Español", "Spanish"),
    ("fr", "Français", "French"),
    ("de", "Deutsch", "German"),
]

_NATIVE = {code: native for code, native, _ in LANGUAGES}
_CLAUDE = {code: eng for code, _, eng in LANGUAGES}
DEFAULT_LANG = "ko"

# ── 기준(base) 언어 ──────────────────────────────────────────────
# 분석·리서치는 **한 번만** 이 언어로 수행하고, 나머지 선택 언어는 번역으로 만든다.
# 우선순위: 영어 → 유럽어(프랑스어·스페인어·독일어) → 일본어 → 한국어 → 중국어.
# 영어를 최우선으로 두는 이유는 web_search 로 닿는 1차 자료가 가장 많고, 고유명사·
# 역사 용어가 원문 표기에 가장 가깝게 유지되기 때문이다(번역 손실이 한 방향으로만 발생).
BASE_LANG_PRIORITY: list[str] = ["en", "fr", "es", "de", "ja", "ko", "zh"]


def pick_base_lang(langs) -> str:
    """선택된 언어들 중 조사(analysis/research)를 수행할 기준 언어 1개를 고른다.

    선택 목록에 우선순위 언어가 하나도 없으면 목록의 첫 번째를 쓴다.
    """
    codes = [normalize(x) for x in (langs or []) if x]
    if not codes:
        return "en" if "en" in _NATIVE else DEFAULT_LANG
    for pref in BASE_LANG_PRIORITY:
        if pref in codes:
            return pref
    return codes[0]


def is_supported(lang: str) -> bool:
    return lang in _NATIVE


def normalize(lang: str | None) -> str:
    lang = (lang or "").strip().lower()
    if lang in _NATIVE:
        return lang
    # ko-KR → ko 같은 지역 접미사 허용
    base = lang.split("-")[0]
    return base if base in _NATIVE else DEFAULT_LANG


def native_name(lang: str) -> str:
    return _NATIVE.get(normalize(lang), lang)


def languages_public() -> list[dict]:
    """프론트로 내려보낼 언어 목록."""
    return [{"code": c, "name": n} for c, n, _ in LANGUAGES]


def claude_language_directive(lang: str) -> str:
    """분석 시스템 프롬프트에 덧붙일 언어 지시문."""
    name = _CLAUDE.get(normalize(lang), "English")
    return (
        f"\n\nOUTPUT LANGUAGE\n"
        f"- Write EVERY natural-language string value in the tool call in {name}. "
        f"This includes: observation, evidence, cultural_economic_read, region_or_state, city, "
        f"the country display name, and landmark name/type.\n"
        f"- Keep these fields as fixed English/ASCII tokens (do NOT translate): country_iso (ISO code), "
        f"driving_side (one of: left, right, unknown), hemisphere (one of: northern, southern, unknown), "
        f"each cue's weight (one of: high, medium, low), and place_slug (ASCII a-z 0-9 hyphens only — "
        f"the romanized city, or a more world-famous landmark here; same value regardless of language).\n"
        f"- Numbers/coordinates stay numeric."
    )


# ── 리포트 고정 라벨 ─────────────────────────────────────────────
# 누락 키는 en 으로 폴백한다.
_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "report_word": "Report",
        "unknown": "Unknown",
        "scenes_h": "Captured scenes",
        "scenes_meta": "{n} shots · street view▲ / map▼",
        "no_embed": "No embedded captures.",
        "scenes_dropped": "{n} further capture(s) were selected but exceeded the analysis limit, so they are not shown here and were not used as evidence.",
        "k_h": "Referenced knowledge",
        "k_sub": "knowledge base",
        "tr_failed": "This document could not be translated — the text below is the original {src}. {done}/{total} strings translated.",
        "k_note": "These facts were already established by earlier reports and were reused instead of researched again. Each id links to the stored atom.",
        "syn_word": "Synthesis",
        "syn_thesis": "What connects these places",
        "syn_conn": "Cross-place connections",
        "syn_open": "Open questions",
        "syn_basis": "Built from stored knowledge only — no new web research was performed.",
        "syn_places": "Places drawn on",
        "syn_atoms": "Atoms used",
        "maps_h": "Where this is",
        "maps_sub": "same point at four scales",
        "maps_open": "Open in Google Maps",
        "map_admin": "Region",
        "map_metro": "Metro area",
        "map_urban": "Urban fabric",
        "map_block": "Block",
        "narrow_h": "How it was narrowed down",
        "narrow_sub": "each step must eliminate a live candidate",
        "narrow_note": "Naming the country is the easy part. What follows is the chain that cuts from there down to the actual place — and what each observation ruled out.",
        "narrow_q": "Question",
        "narrow_obs": "Observed",
        "narrow_disc": "Why it cuts",
        "narrow_out": "Ruled out",
        "narrow_in": "Survives",
        "narrow_cand": "Candidates",
        "narrow_nocut": "Could not be split from the image at this level.",
        "script_h": "Reading",
        "script_sub": "drafted fast, checked for fidelity",
        "script_by": "Drafted / checked by",
        "conf_label": "Best-guess confidence {conf}%",
        "coord_prefix": "Est. coordinates ~",
        "driving_tpl": "Drives {side}",
        "hemi_tpl": "{hemi} hemisphere",
        "cues_h": "Identification cues",
        "cues_sub": "cues",
        "col_category": "Category",
        "col_observation": "Observation",
        "col_weight": "Weight",
        "langs_h": "Language · Script",
        "col_language": "Language",
        "col_evidence": "Evidence",
        "col_conf": "Conf.",
        "landmarks_h": "Landmarks · Features",
        "culture_h": "Culture · Economy",
        "alts_label": "Alternatives:",
        "meta_model": "Model",
        "meta_cost": "Cost ~$",
        "meta_images": "{n} images",
        "foot": (
            "GeoGuessHelper · study report — using it during live GeoGuessr rounds is cheating.<br>"
            "Google Maps Platform ToS compliant: Street View imagery is not redistributed, kept for "
            "personal review only; stored identifiers are lat/lng · pano_id. Faces and license plates "
            "are blurred — no individuals are identified."
        ),
    },
    "ko": {
        "report_word": "리포트",
        "unknown": "미상",
        "scenes_h": "캡처 장면",
        "scenes_meta": "{n}장 · 로드뷰▲ / 지도▼",
        "no_embed": "임베드된 캡처가 없습니다.",
        "scenes_dropped": "선택된 캡처 {n}장은 분석 상한을 넘어 분석에 쓰이지 않았으므로 여기에도 싣지 않았습니다.",
        "k_h": "참조한 지식",
        "k_sub": "지식 저장소",
        "tr_failed": "이 문서는 번역에 실패해 아래 본문이 원문({src}) 그대로입니다. {done}/{total}개만 번역되었습니다.",
        "k_note": "아래 사실은 이전 보고서에서 이미 확립된 것이라 다시 조사하지 않고 재사용했습니다. 각 id 는 저장된 원자로 연결됩니다.",
        "syn_word": "종합 보고서",
        "syn_thesis": "이 장소들을 잇는 것",
        "syn_conn": "장소 간 연결",
        "syn_open": "남은 질문",
        "syn_basis": "저장된 지식만으로 재구성했습니다 — 새 웹 검색은 하지 않았습니다.",
        "syn_places": "재료가 된 장소",
        "syn_atoms": "사용한 원자",
        "maps_h": "여기가 어디인가",
        "maps_sub": "같은 지점을 네 축척으로",
        "maps_open": "구글 지도에서 열기",
        "map_admin": "광역 행정구역",
        "map_metro": "도시권",
        "map_urban": "시가",
        "map_block": "블록",
        "narrow_h": "어떻게 좁혔는가",
        "narrow_sub": "각 단계는 살아있는 후보를 반드시 하나 이상 배제한다",
        "narrow_note": "국가를 맞히는 것은 쉬운 부분입니다. 아래는 거기서부터 실제 장소까지 잘라 들어간 사슬이며, 각 관찰이 무엇을 배제했는지를 보여줍니다.",
        "narrow_q": "가르는 질문",
        "narrow_obs": "관찰",
        "narrow_disc": "왜 갈리는가",
        "narrow_out": "배제",
        "narrow_in": "남음",
        "narrow_cand": "후보",
        "narrow_nocut": "이 단계는 이미지만으로 가를 수 없었습니다.",
        "script_h": "읽기",
        "script_sub": "빠르게 초안, 사실 정합성 검증",
        "script_by": "초안 / 검증",
        "conf_label": "최선 추정 신뢰도 {conf}%",
        "coord_prefix": "추정 좌표 ~",
        "driving_tpl": "주행 {side}",
        "hemi_tpl": "{hemi}반구",
        "cues_h": "식별 단서",
        "cues_sub": "cues",
        "col_category": "범주",
        "col_observation": "관찰",
        "col_weight": "가중",
        "langs_h": "언어 · 문자",
        "col_language": "언어",
        "col_evidence": "근거",
        "col_conf": "신뢰",
        "landmarks_h": "기념물 · 지형지물",
        "culture_h": "문화 · 경제",
        "alts_label": "대안 후보:",
        "meta_model": "모델",
        "meta_cost": "비용 ~$",
        "meta_images": "이미지 {n}장",
        "foot": (
            "GeoGuessHelper · 복기(study) 리포트 — 라이브 지오게서 라운드 사용은 부정행위.<br>"
            "Google Maps Platform ToS 준수: 스트리트뷰 이미지는 재배포 금지, 개인 보관용 · 저장 식별자는 "
            "lat/lng·pano_id. 얼굴·번호판은 블러 처리됨 — 개인 식별 안 함."
        ),
    },
    "ja": {
        "k_h": "参照知識",
        "k_note": "これらの事実は以前のレポートで既に確立されており、再度調査するのではなく再利用されました。各IDは保存されたアトムにリンクしています。",
        "k_sub": "ナレッジベース",
        "map_admin": "地域",
        "map_block": "街区",
        "map_metro": "都市圏",
        "map_urban": "都市構造",
        "maps_h": "位置",
        "maps_open": "Google マップで開く",
        "maps_sub": "4つの縮尺で見る同一地点",
        "narrow_cand": "候補",
        "narrow_disc": "絞り込みの理由",
        "narrow_h": "どのように絞り込まれたか",
        "narrow_in": "残存",
        "narrow_nocut": "この段階では画像から絞り込むことができませんでした。",
        "narrow_note": "国名を特定するのは簡単な部分です。以下は、そこから実際の場所まで絞り込んでいく連鎖と、それぞれの観察が何を除外したかを示しています。",
        "narrow_obs": "観察された内容",
        "narrow_out": "除外された候補",
        "narrow_q": "質問",
        "narrow_sub": "各段階で現存する候補を一つ排除する必要がある",
        "scenes_dropped": "さらに{n}件のキャプチャが選択されましたが、分析上限を超えたため、ここには表示されず、証拠としても使用されていません。",
        "script_by": "起草・確認担当",
        "script_h": "読解",
        "script_sub": "迅速に起草し、正確性を確認",
        "syn_atoms": "使用されたアトム",
        "syn_basis": "保存された知識のみに基づいて作成されています — 新たなウェブ調査は行われていません。",
        "syn_conn": "場所間のつながり",
        "syn_open": "未解決の疑問",
        "syn_places": "参照した場所",
        "syn_thesis": "これらの場所をつなぐもの",
        "syn_word": "総合",
        "tr_failed": "この文書は翻訳に失敗し、本文は原文（{src}）のままです。{done}/{total} 件のみ翻訳されました。",
        "report_word": "レポート",
        "unknown": "不明",
        "scenes_h": "キャプチャ場面",
        "scenes_meta": "{n}枚 · ストリートビュー▲ / 地図▼",
        "no_embed": "埋め込まれたキャプチャがありません。",
        "conf_label": "ベスト推定の信頼度 {conf}%",
        "coord_prefix": "推定座標 ~",
        "driving_tpl": "通行 {side}",
        "hemi_tpl": "{hemi}半球",
        "cues_h": "識別の手がかり",
        "cues_sub": "cues",
        "col_category": "分類",
        "col_observation": "観察",
        "col_weight": "重み",
        "langs_h": "言語 · 文字",
        "col_language": "言語",
        "col_evidence": "根拠",
        "col_conf": "信頼",
        "landmarks_h": "ランドマーク · 地形",
        "culture_h": "文化 · 経済",
        "alts_label": "代替候補:",
        "meta_model": "モデル",
        "meta_cost": "コスト ~$",
        "meta_images": "画像 {n}枚",
        "foot": (
            "GeoGuessHelper · 復習(study)レポート — ライブのGeoGuessrラウンドでの使用は不正行為です。<br>"
            "Google Maps Platform ToS 準拠：ストリートビュー画像は再配布せず、個人の復習用のみ。保存識別子は "
            "lat/lng·pano_id。顔・ナンバープレートはぼかし処理 — 個人は特定しません。"
        ),
    },
    "zh": {
        "k_h": "参考知识",
        "k_note": "这些事实已在先前的报告中确立，此处直接复用而非重新研究。每个 id 都链接到已存储的知识原子。",
        "k_sub": "知识库",
        "map_admin": "地区",
        "map_block": "街区",
        "map_metro": "都市区",
        "map_urban": "城市结构",
        "maps_h": "位置所在",
        "maps_open": "在谷歌地图中打开",
        "maps_sub": "同一地点的四种比例尺视图",
        "narrow_cand": "候选项",
        "narrow_disc": "排除理由",
        "narrow_h": "如何逐步缩小范围",
        "narrow_in": "保留",
        "narrow_nocut": "在此层级无法从图像中区分出结果。",
        "narrow_note": "确定国家只是简单的一步。接下来的是从国家层层缩小到具体地点的推理链——以及每项观察排除了什么。",
        "narrow_obs": "观察到",
        "narrow_out": "已排除",
        "narrow_q": "问题",
        "narrow_sub": "每一步都必须排除一个仍有效的候选项",
        "scenes_dropped": "另有 {n} 个已选取的画面因超出分析上限而未在此处显示，也未被用作证据。",
        "script_by": "撰写／核查者",
        "script_h": "解读",
        "script_sub": "快速起草，经核查以确保准确性",
        "syn_atoms": "所用知识原子",
        "syn_basis": "仅基于已存储的知识构建——未进行新的网络调研。",
        "syn_conn": "跨地点关联",
        "syn_open": "未解问题",
        "syn_places": "所涉地点",
        "syn_thesis": "这些地点之间的联系",
        "syn_word": "综合",
        "tr_failed": "本文档翻译失败，下方正文为原文（{src}）。仅翻译了 {done}/{total} 条。",
        "report_word": "报告",
        "unknown": "未知",
        "scenes_h": "截取场景",
        "scenes_meta": "{n} 张 · 街景▲ / 地图▼",
        "no_embed": "没有嵌入的截图。",
        "conf_label": "最佳猜测置信度 {conf}%",
        "coord_prefix": "估计坐标 ~",
        "driving_tpl": "{side}行驶",
        "hemi_tpl": "{hemi}半球",
        "cues_h": "识别线索",
        "cues_sub": "cues",
        "col_category": "类别",
        "col_observation": "观察",
        "col_weight": "权重",
        "langs_h": "语言 · 文字",
        "col_language": "语言",
        "col_evidence": "依据",
        "col_conf": "置信",
        "landmarks_h": "地标 · 地形",
        "culture_h": "文化 · 经济",
        "alts_label": "备选:",
        "meta_model": "模型",
        "meta_cost": "费用 ~$",
        "meta_images": "{n} 张图片",
        "foot": (
            "GeoGuessHelper · 复盘(study)报告 — 在实时 GeoGuessr 对局中使用属于作弊。<br>"
            "遵守 Google Maps Platform ToS：街景图像不得再分发，仅供个人复盘；存储标识符为 "
            "lat/lng·pano_id。人脸与车牌已模糊 — 不识别个人。"
        ),
    },
    "es": {
        "k_h": "Conocimiento referenciado",
        "k_note": "Estos hechos ya habían sido establecidos por informes anteriores y se reutilizaron en lugar de investigarse de nuevo. Cada id enlaza con el átomo almacenado.",
        "k_sub": "base de conocimiento",
        "map_admin": "Región",
        "map_block": "Manzana",
        "map_metro": "Área metropolitana",
        "map_urban": "Tejido urbano",
        "maps_h": "Dónde está esto",
        "maps_open": "Abrir en Google Maps",
        "maps_sub": "el mismo punto en cuatro escalas",
        "narrow_cand": "Candidatos",
        "narrow_disc": "Por qué descarta",
        "narrow_h": "Cómo se acotó",
        "narrow_in": "Sobrevive",
        "narrow_nocut": "No se pudo distinguir a partir de la imagen en este nivel.",
        "narrow_note": "Nombrar el país es la parte fácil. Lo que sigue es la cadena que recorta desde ahí hasta el lugar real, y lo que cada observación descartó.",
        "narrow_obs": "Observado",
        "narrow_out": "Descartado",
        "narrow_q": "Pregunta",
        "narrow_sub": "cada paso debe eliminar un candidato vigente",
        "scenes_dropped": "Se seleccionaron {n} captura(s) adicionales, pero excedieron el límite de análisis, por lo que no se muestran aquí ni se utilizaron como evidencia.",
        "script_by": "Redactado / verificado por",
        "script_h": "Lectura",
        "script_sub": "redactado con rapidez, verificado por fidelidad",
        "syn_atoms": "Átomos utilizados",
        "syn_basis": "Elaborado únicamente a partir del conocimiento almacenado; no se realizó ninguna investigación web nueva.",
        "syn_conn": "Conexiones entre lugares",
        "syn_open": "Preguntas abiertas",
        "syn_places": "Lugares consultados",
        "syn_thesis": "Qué conecta estos lugares",
        "syn_word": "Síntesis",
        "tr_failed": "Este documento no se pudo traducir — el texto siguiente está en el idioma original ({src}). Se tradujeron {done}/{total} cadenas.",
        "report_word": "Informe",
        "unknown": "Desconocido",
        "scenes_h": "Escenas capturadas",
        "scenes_meta": "{n} tomas · Street View▲ / mapa▼",
        "no_embed": "No hay capturas incrustadas.",
        "conf_label": "Confianza de la mejor estimación {conf}%",
        "coord_prefix": "Coordenadas est. ~",
        "driving_tpl": "Conduce por la {side}",
        "hemi_tpl": "Hemisferio {hemi}",
        "cues_h": "Pistas de identificación",
        "cues_sub": "cues",
        "col_category": "Categoría",
        "col_observation": "Observación",
        "col_weight": "Peso",
        "langs_h": "Idioma · Escritura",
        "col_language": "Idioma",
        "col_evidence": "Evidencia",
        "col_conf": "Conf.",
        "landmarks_h": "Monumentos · Relieve",
        "culture_h": "Cultura · Economía",
        "alts_label": "Alternativas:",
        "meta_model": "Modelo",
        "meta_cost": "Coste ~$",
        "meta_images": "{n} imágenes",
        "foot": (
            "GeoGuessHelper · informe de estudio — usarlo durante rondas en vivo de GeoGuessr es hacer trampa.<br>"
            "Conforme a los ToS de Google Maps Platform: las imágenes de Street View no se redistribuyen, "
            "solo para revisión personal; los identificadores guardados son lat/lng · pano_id. Las caras y "
            "matrículas están difuminadas — no se identifica a personas."
        ),
    },
    "fr": {
        "k_h": "Connaissances référencées",
        "k_note": "Ces faits avaient déjà été établis par des rapports antérieurs et ont été réutilisés plutôt que de faire l'objet de nouvelles recherches. Chaque identifiant renvoie à l'atome stocké.",
        "k_sub": "base de connaissances",
        "map_admin": "Région",
        "map_block": "Pâté de maisons",
        "map_metro": "Aire métropolitaine",
        "map_urban": "Tissu urbain",
        "maps_h": "Où cela se trouve",
        "maps_open": "Ouvrir dans Google Maps",
        "maps_sub": "le même point à quatre échelles",
        "narrow_cand": "Candidats",
        "narrow_disc": "Pourquoi cela élimine",
        "narrow_h": "Comment cela a été circonscrit",
        "narrow_in": "Survit",
        "narrow_nocut": "N'a pas pu être distingué de l'image à ce niveau.",
        "narrow_note": "Nommer le pays est la partie facile. Ce qui suit est la chaîne qui permet de descendre jusqu'au lieu réel — et ce que chaque observation a permis d'exclure.",
        "narrow_obs": "Observé",
        "narrow_out": "Écarté",
        "narrow_q": "Question",
        "narrow_sub": "chaque étape doit éliminer un candidat encore en lice",
        "scenes_dropped": "{n} capture(s) supplémentaire(s) ont été sélectionnées mais dépassaient la limite d'analyse ; elles ne sont donc pas montrées ici et n'ont pas été utilisées comme preuve.",
        "script_by": "Rédigé / vérifié par",
        "script_h": "Lecture",
        "script_sub": "rédigé rapidement, vérifié pour sa fidélité",
        "syn_atoms": "Atomes utilisés",
        "syn_basis": "Construit uniquement à partir de connaissances stockées — aucune nouvelle recherche web n'a été effectuée.",
        "syn_conn": "Connexions entre lieux",
        "syn_open": "Questions ouvertes",
        "syn_places": "Lieux mobilisés",
        "syn_thesis": "Ce qui relie ces lieux",
        "syn_word": "Synthèse",
        "tr_failed": "Ce document n'a pas pu être traduit — le texte ci-dessous est dans la langue d'origine ({src}). {done}/{total} chaînes traduites.",
        "report_word": "Rapport",
        "unknown": "Inconnu",
        "scenes_h": "Scènes capturées",
        "scenes_meta": "{n} prises · Street View▲ / carte▼",
        "no_embed": "Aucune capture intégrée.",
        "conf_label": "Confiance de la meilleure estimation {conf}%",
        "coord_prefix": "Coordonnées est. ~",
        "driving_tpl": "Conduite à {side}",
        "hemi_tpl": "Hémisphère {hemi}",
        "cues_h": "Indices d'identification",
        "cues_sub": "cues",
        "col_category": "Catégorie",
        "col_observation": "Observation",
        "col_weight": "Poids",
        "langs_h": "Langue · Écriture",
        "col_language": "Langue",
        "col_evidence": "Preuve",
        "col_conf": "Conf.",
        "landmarks_h": "Monuments · Relief",
        "culture_h": "Culture · Économie",
        "alts_label": "Alternatives :",
        "meta_model": "Modèle",
        "meta_cost": "Coût ~$",
        "meta_images": "{n} images",
        "foot": (
            "GeoGuessHelper · rapport d'étude — l'utiliser pendant une partie GeoGuessr en direct est de la triche.<br>"
            "Conforme aux CGU de Google Maps Platform : les images Street View ne sont pas redistribuées, "
            "conservées pour révision personnelle uniquement ; les identifiants stockés sont lat/lng · pano_id. "
            "Visages et plaques sont floutés — aucune personne n'est identifiée."
        ),
    },
    "de": {
        "k_h": "Referenziertes Wissen",
        "k_note": "Diese Fakten wurden bereits in früheren Berichten festgestellt und wiederverwendet, statt erneut recherchiert zu werden. Jede ID verweist auf das gespeicherte Atom.",
        "k_sub": "Wissensdatenbank",
        "map_admin": "Region",
        "map_block": "Block",
        "map_metro": "Metropolregion",
        "map_urban": "Urbanes Gefüge",
        "maps_h": "Wo sich das befindet",
        "maps_open": "In Google Maps öffnen",
        "maps_sub": "derselbe Punkt in vier Maßstäben",
        "narrow_cand": "Kandidaten",
        "narrow_disc": "Warum es eingrenzt",
        "narrow_h": "Wie es eingegrenzt wurde",
        "narrow_in": "Bleibt bestehen",
        "narrow_nocut": "Konnte auf dieser Ebene nicht aus dem Bild herausgelöst werden.",
        "narrow_note": "Das Land zu benennen ist der einfache Teil. Was folgt, ist die Kette, die von dort aus bis zum tatsächlichen Ort eingrenzt — und was jede Beobachtung ausgeschlossen hat.",
        "narrow_obs": "Beobachtet",
        "narrow_out": "Ausgeschlossen",
        "narrow_q": "Frage",
        "narrow_sub": "jeder Schritt muss einen verbleibenden Kandidaten eliminieren",
        "scenes_dropped": "{n} weitere Aufnahme(n) wurden ausgewählt, überschritten jedoch das Analyselimit und werden daher hier nicht angezeigt und nicht als Beleg verwendet.",
        "script_by": "Entworfen / geprüft von",
        "script_h": "Lesart",
        "script_sub": "schnell entworfen, auf Genauigkeit geprüft",
        "syn_atoms": "Verwendete Atome",
        "syn_basis": "Ausschließlich aus gespeichertem Wissen erstellt — es wurde keine neue Webrecherche durchgeführt.",
        "syn_conn": "Ortsübergreifende Verbindungen",
        "syn_open": "Offene Fragen",
        "syn_places": "Herangezogene Orte",
        "syn_thesis": "Was diese Orte verbindet",
        "syn_word": "Synthese",
        "tr_failed": "Dieses Dokument konnte nicht übersetzt werden — der Text unten ist im Original ({src}). {done}/{total} Zeichenketten übersetzt.",
        "report_word": "Bericht",
        "unknown": "Unbekannt",
        "scenes_h": "Erfasste Szenen",
        "scenes_meta": "{n} Aufnahmen · Street View▲ / Karte▼",
        "no_embed": "Keine eingebetteten Aufnahmen.",
        "conf_label": "Konfidenz der besten Schätzung {conf}%",
        "coord_prefix": "Geschätzte Koordinaten ~",
        "driving_tpl": "Fährt {side}",
        "hemi_tpl": "{hemi} Hemisphäre",
        "cues_h": "Identifikationshinweise",
        "cues_sub": "cues",
        "col_category": "Kategorie",
        "col_observation": "Beobachtung",
        "col_weight": "Gewicht",
        "langs_h": "Sprache · Schrift",
        "col_language": "Sprache",
        "col_evidence": "Beleg",
        "col_conf": "Konf.",
        "landmarks_h": "Wahrzeichen · Gelände",
        "culture_h": "Kultur · Wirtschaft",
        "alts_label": "Alternativen:",
        "meta_model": "Modell",
        "meta_cost": "Kosten ~$",
        "meta_images": "{n} Bilder",
        "foot": (
            "GeoGuessHelper · Studienbericht — die Nutzung während laufender GeoGuessr-Runden ist Betrug.<br>"
            "Konform mit den Google Maps Platform ToS: Street-View-Bilder werden nicht weiterverbreitet, nur zur "
            "persönlichen Nachbereitung; gespeicherte Kennungen sind lat/lng · pano_id. Gesichter und "
            "Kennzeichen sind unkenntlich gemacht — es werden keine Personen identifiziert."
        ),
    },
}

# 웹 리서치(도시 프로파일) 섹션 라벨. 누락 키는 영어로 폴백.
_RESEARCH: dict[str, dict[str, str]] = {
    "en": {"r_profile_h": "Location profile", "r_profile_sub": "web research",
           "r_summary": "Overview", "r_economy": "Economy", "r_industry": "Industry",
           "r_culture": "Culture & history", "r_tourism": "Tourism", "r_people": "Notable people",
           "r_neighbor": "Vs. neighboring cities", "r_pros": "Pros", "r_cons": "Cons",
           "r_residents": "Residents", "r_sources": "Sources",
           "r_note": "Generated from web search — may contain errors; verify important facts."},
    "ko": {"r_profile_h": "장소 프로파일", "r_profile_sub": "웹 리서치",
           "r_summary": "개요", "r_economy": "경제", "r_industry": "산업",
           "r_culture": "문화 · 역사", "r_tourism": "관광", "r_people": "주요 인물",
           "r_neighbor": "인접 도시와의 비교 · 경쟁", "r_pros": "장점", "r_cons": "단점",
           "r_residents": "주민 특징", "r_sources": "출처",
           "r_note": "웹 검색으로 생성 — 오류가 있을 수 있으니 중요한 사실은 확인하세요."},
    "ja": {"r_profile_h": "都市プロファイル", "r_profile_sub": "ウェブ調査",
           "r_summary": "概要", "r_economy": "経済", "r_industry": "産業",
           "r_culture": "文化 · 歴史", "r_tourism": "観光", "r_people": "著名な人物",
           "r_neighbor": "近隣都市との比較 · 競合", "r_pros": "長所", "r_cons": "短所",
           "r_residents": "住民の特徴", "r_sources": "出典",
           "r_note": "ウェブ検索により生成 — 誤りを含む可能性があります。重要な事実は確認してください。"},
    "zh": {"r_profile_h": "城市概况", "r_profile_sub": "网络调研",
           "r_summary": "概述", "r_economy": "经济", "r_industry": "产业",
           "r_culture": "文化 · 历史", "r_tourism": "旅游", "r_people": "知名人物",
           "r_neighbor": "与邻近城市的比较 · 竞争", "r_pros": "优点", "r_cons": "缺点",
           "r_residents": "居民特征", "r_sources": "来源",
           "r_note": "由网络搜索生成 — 可能包含错误；重要事实请核实。"},
    "es": {"r_profile_h": "Perfil del lugar", "r_profile_sub": "investigación web",
           "r_summary": "Resumen", "r_economy": "Economía", "r_industry": "Industria",
           "r_culture": "Cultura e historia", "r_tourism": "Turismo", "r_people": "Personas destacadas",
           "r_neighbor": "Frente a ciudades vecinas", "r_pros": "Ventajas", "r_cons": "Desventajas",
           "r_residents": "Residentes", "r_sources": "Fuentes",
           "r_note": "Generado por búsqueda web — puede contener errores; verifique los datos importantes."},
    "fr": {"r_profile_h": "Profil du lieu", "r_profile_sub": "recherche web",
           "r_summary": "Aperçu", "r_economy": "Économie", "r_industry": "Industrie",
           "r_culture": "Culture et histoire", "r_tourism": "Tourisme", "r_people": "Personnalités",
           "r_neighbor": "Face aux villes voisines", "r_pros": "Avantages", "r_cons": "Inconvénients",
           "r_residents": "Habitants", "r_sources": "Sources",
           "r_note": "Généré par recherche web — peut contenir des erreurs ; vérifiez les faits importants."},
    "de": {"r_profile_h": "Ortsprofil", "r_profile_sub": "Web-Recherche",
           "r_summary": "Überblick", "r_economy": "Wirtschaft", "r_industry": "Industrie",
           "r_culture": "Kultur & Geschichte", "r_tourism": "Tourismus", "r_people": "Bekannte Personen",
           "r_neighbor": "Vs. Nachbarstädte", "r_pros": "Vorteile", "r_cons": "Nachteile",
           "r_residents": "Einwohner", "r_sources": "Quellen",
           "r_note": "Per Websuche erstellt — kann Fehler enthalten; wichtige Fakten prüfen."},
}


# driving_side / hemisphere / weight 영어 enum → 언어별 표기
_ENUMS: dict[str, dict[str, dict[str, str]]] = {
    "driving_side": {
        "en": {"left": "on the left", "right": "on the right", "unknown": "unknown"},
        "ko": {"left": "좌측", "right": "우측", "unknown": "미상"},
        "ja": {"left": "左側", "right": "右側", "unknown": "不明"},
        "zh": {"left": "靠左", "right": "靠右", "unknown": "未知"},
        "es": {"left": "izquierda", "right": "derecha", "unknown": "desconocido"},
        "fr": {"left": "gauche", "right": "droite", "unknown": "inconnu"},
        "de": {"left": "links", "right": "rechts", "unknown": "unbekannt"},
    },
    "hemisphere": {
        "en": {"northern": "Northern", "southern": "Southern", "unknown": "unknown"},
        "ko": {"northern": "북", "southern": "남", "unknown": "미상"},
        "ja": {"northern": "北", "southern": "南", "unknown": "不明"},
        "zh": {"northern": "北", "southern": "南", "unknown": "未知"},
        "es": {"northern": "norte", "southern": "sur", "unknown": "desconocido"},
        "fr": {"northern": "nord", "southern": "sud", "unknown": "inconnu"},
        "de": {"northern": "Nördliche", "southern": "Südliche", "unknown": "unbekannte"},
    },
    # 판별 사슬의 레벨 — 코드/CSS 는 영어 enum 을 쓰고 표시만 언어별로 바꾼다.
    "level": {
        "en": {"continent": "Continent", "country": "Country", "macro_region": "Macro-region",
               "cultural_sphere": "Cultural sphere", "admin_region": "Admin region",
               "metro_area": "Metro area", "district": "District"},
        "ko": {"continent": "대륙", "country": "국가", "macro_region": "광역권",
               "cultural_sphere": "문화·민족권", "admin_region": "행정구역",
               "metro_area": "도시권", "district": "지구"},
        "ja": {"continent": "大陸", "country": "国", "macro_region": "広域圏",
               "cultural_sphere": "文化・民族圏", "admin_region": "行政区域",
               "metro_area": "都市圏", "district": "地区"},
        "zh": {"continent": "大洲", "country": "国家", "macro_region": "大区",
               "cultural_sphere": "文化·民族圈", "admin_region": "行政区",
               "metro_area": "都市圈", "district": "地段"},
        "es": {"continent": "Continente", "country": "País", "macro_region": "Macrorregión",
               "cultural_sphere": "Ámbito cultural", "admin_region": "Región administrativa",
               "metro_area": "Área metropolitana", "district": "Distrito"},
        "fr": {"continent": "Continent", "country": "Pays", "macro_region": "Macro-région",
               "cultural_sphere": "Aire culturelle", "admin_region": "Région administrative",
               "metro_area": "Aire urbaine", "district": "Quartier"},
        "de": {"continent": "Kontinent", "country": "Land", "macro_region": "Großregion",
               "cultural_sphere": "Kulturraum", "admin_region": "Verwaltungsregion",
               "metro_area": "Metropolregion", "district": "Bezirk"},
    },
    "weight": {
        "en": {"high": "high", "medium": "medium", "low": "low"},
        "ko": {"high": "높음", "medium": "중간", "low": "낮음"},
        "ja": {"high": "高", "medium": "中", "low": "低"},
        "zh": {"high": "高", "medium": "中", "low": "低"},
        "es": {"high": "alto", "medium": "medio", "low": "bajo"},
        "fr": {"high": "élevé", "medium": "moyen", "low": "faible"},
        "de": {"high": "hoch", "medium": "mittel", "low": "niedrig"},
    },
}


def report_strings(lang: str) -> dict[str, str]:
    """lang 의 리포트 라벨(영어 폴백 병합) — 기본 라벨 + 리서치 라벨."""
    lang = normalize(lang)
    base = dict(_STRINGS["en"])
    base.update(_RESEARCH["en"])
    base.update(_STRINGS.get(lang, {}))
    base.update(_RESEARCH.get(lang, {}))
    return base


# ── 라벨 완전성 검사 ──────────────────────────────────────────────
# 왜 여기서 검사하나 — report_strings() 는 en 을 바탕에 깔고 그 위에 언어별 값을 덮는다.
# 편리하지만 **키가 빠져도 조용히 영어가 나간다**. 실제로 그렇게 31/57 개(54%)가
# fr·ja·zh·es·de 에서 영어로 렌더되고 있었고, 화면만 봐서는 알 수 없었다
# (판별 사슬 제목·지도 축척 이름·지식 절이 통째로 영어였다).
# 이제는 import 시점에 드러난다 — 라벨을 새로 추가하면 그 자리에서 걸린다.
def _assert_label_parity() -> list[str]:
    base = set(_STRINGS["en"])
    bad = []
    for lg, d in _STRINGS.items():
        miss = base - set(d)
        if miss:
            bad.append(f"{lg}: {len(miss)}개 누락 ({', '.join(sorted(miss)[:6])}…)")
    return bad


_LABEL_GAPS = _assert_label_parity()
if _LABEL_GAPS:   # 죽이지는 않는다 — 보고서 생성이 라벨 때문에 멈추면 그게 더 나쁘다
    import warnings

    warnings.warn("i18n 라벨 누락: " + " / ".join(_LABEL_GAPS), stacklevel=2)


def enum_label(lang: str, kind: str, value) -> str:
    """driving_side/hemisphere/weight 의 영어 enum → 언어 표기. 미지값은 원문 그대로."""
    lang = normalize(lang)
    table = _ENUMS.get(kind, {})
    per = table.get(lang) or table.get("en") or {}
    key = str(value or "").strip().lower()
    return per.get(key, str(value or ""))
