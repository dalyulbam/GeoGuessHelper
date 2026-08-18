"""행위자 분류·테마 가중 규칙 — 휴리스틱 + 수동 큐레이션.

LLM 정밀 추출(기획서 패스 A·B) 전까지의 정직한 근사다:
  - 타입: 큐레이션 오버라이드 → 슬러그 키워드 규칙 → 기본값 place
  - 테마: 원자 레이어 → 테마 가중 행렬 (docs/plan/knowledge-atlas_260816.html §7테마)
관계 유형은 지어내지 않는다 — 엣지는 "동시 서술"이고 색은 출처 원자의 레이어다.
"""
from __future__ import annotations

# ── 행위자 타입 ──────────────────────────────────────────────────
# place 거점 · company 기업 · org 조직/단체 · person 인물 · infra 인프라
# polity 정치체 · culture 문화권 · nature 자연
TYPES = ("place", "company", "org", "person", "infra", "polity", "culture", "nature")

TYPE_OVERRIDES: dict[str, str] = {
    # 인물
    "german-titov": "person", "mikhail-kalashnikov": "person",
    "ismail-qemali": "person", "juraj-dalmatinac": "person",
    "miroslav-hirtz": "person",
    # 기업
    "gedeon-richter": "company", "janaf": "company", "solana-pag": "company",
    "rapska-plovidba": "company", "oxford-university-press": "company",
    # 조직·단체
    "bektashi-order": "org", "bodleian-library": "org",
    "university-of-oxford": "org", "universidad-nacional-de-cordoba": "org",
    "christ-church-college": "org", "oxbridge": "org",
    "frankopan": "org", "demidov-dynasty": "org",
    "eastern-orthodox-church": "org", "sziget-festival": "org",
    "president-of-iceland": "org", "manzana-jesuitica": "org",
    # 정치체
    "soviet-union": "polity", "ottoman-empire": "polity",
    "austro-hungarian-empire": "polity", "mughal-empire": "polity",
    "republic-of-ragusa": "polity", "roman-dalmatia": "polity",
    "iapodes": "polity",
    # 인프라
    "krk-bridge": "infra", "trans-adriatic-pipeline": "infra",
    "mini-plant-cowley": "infra",
    # 문화
    "collegiate-gothic": "culture", "cuarteto": "culture",
    "ruin-bars": "culture", "thermal-baths": "culture",
    "victorian-terraced-housing": "culture", "cotswold-limestone": "culture",
    "university-reform-1918": "culture", "pag-sheep": "culture",
    # 자연 (키워드로 안 잡히는 것)
    "icelandic-nature": "nature", "velebit": "nature",
}

# (부분 문자열, 타입) — 위에서부터 첫 일치 적용
TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("-empire", "polity"), ("republic-of-", "polity"), ("-union", "polity"),
    ("university", "org"), ("college", "org"), ("cathedral", "place"),
    ("library", "org"), ("monastery", "org"), ("-order", "org"),
    ("-dynasty", "org"), ("festival", "org"),
    ("-language", "culture"), ("-script", "culture"), ("dialect", "culture"),
    ("-culture", "culture"), ("cuisine", "culture"),
    ("bridge", "infra"), ("pipeline", "infra"), ("railway", "infra"),
    ("-port", "infra"), ("terminal", "infra"), ("plant-", "infra"),
    ("-sea", "nature"), ("river-", "nature"), ("-river", "nature"),
    ("mountain", "nature"), ("-alps", "nature"), ("plateau", "nature"),
    ("-park", "nature"), ("lagoon", "nature"), ("-channel", "nature"),
    ("-bay", "nature"), ("-lake", "nature"), ("lago-", "nature"),
    ("-hraun", "nature"), ("volcano", "nature"), ("glacier", "nature"),
    ("-forest", "nature"), ("chapada", "nature"),
]


def classify(slug: str) -> str:
    if slug in TYPE_OVERRIDES:
        return TYPE_OVERRIDES[slug]
    for kw, t in TYPE_KEYWORDS:
        if kw in slug:
            return t
    return "place"


# ── 라벨 표기 보정 ───────────────────────────────────────────────
LABEL_FIX = {
    "janaf": "JANAF",
    "mini-plant-cowley": "MINI Plant Cowley",
    "trans-adriatic-pipeline": "Trans-Adriatic Pipeline",
    "gruz": "Gruž",
    "pag-sheep": "Pag Sheep (치즈)",
}


def label_of(slug: str) -> str:
    if slug in LABEL_FIX:
        return LABEL_FIX[slug]
    return slug.replace("-", " ").title()


# ── 테마 ← 레이어 가중 행렬 ──────────────────────────────────────
THEMES = ("stock", "realty", "trade", "corporate", "talent", "travel", "heritage")

THEME_MATRIX: dict[str, dict[str, float]] = {
    "stock":     {"economy": 1.0, "history": 0.3, "geography": 0.2},
    "realty":    {"economy": 0.8, "geography": 0.8, "architecture": 0.5, "nature": 0.4, "culture": 0.2},
    "trade":     {"economy": 1.0, "geography": 0.7, "language": 0.2, "nature": 0.2},
    "corporate": {"economy": 1.0, "geography": 0.5, "language": 0.4, "culture": 0.3, "history": 0.2},
    "talent":    {"culture": 0.8, "language": 0.8, "economy": 0.4, "history": 0.4},
    "travel":    {"culture": 1.0, "nature": 1.0, "architecture": 0.9, "geography": 0.5, "history": 0.5, "language": 0.2},
    "heritage":  {"history": 1.0, "architecture": 0.6, "language": 0.6, "culture": 0.5, "geography": 0.2},
}

# 타입이 테마에 주는 보정 — "기업 마커는 주식·법인 렌즈에서 무조건 유의미" 같은 상식
TYPE_THEME_BOOST: dict[str, dict[str, float]] = {
    "company": {"stock": 1.5, "corporate": 1.5, "trade": 0.8},
    "infra":   {"trade": 1.2, "corporate": 0.8, "realty": 0.5},
    "org":     {"talent": 1.0, "heritage": 0.5},
    "person":  {"heritage": 0.8, "talent": 0.4},
    "polity":  {"heritage": 1.5},
    "culture": {"travel": 0.8, "heritage": 0.6, "talent": 0.5},
    "nature":  {"travel": 1.0},
}

# 엣지가 테마 렌즈에 남으려면 출처 원자 레이어의 가중이 이 값 이상이어야 한다
EDGE_THEME_MIN = 0.4
# 행위자가 테마 렌즈에 남는 최소 점수
ACTOR_THEME_MIN = 0.5
