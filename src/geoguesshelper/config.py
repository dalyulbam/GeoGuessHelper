"""환경변수(.env) + 기본 설정. YCollector config.py 패턴의 경량 버전."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def project_root() -> Path:
    """프로젝트 루트 추정 (editable 설치 기준 src/geoguesshelper/config.py → ../../..)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


@dataclass
class Settings:
    js_api_key: str = ""
    static_api_key: str = ""
    anthropic_api_key: str = ""

    host: str = "127.0.0.1"
    port: int = 8799  # YCollector(8765)와 충돌 회피. 사용 중이면 자동으로 다음 빈 포트 선택.

    viewport_w: int = 640
    viewport_h: int = 480
    map_h: int = 260
    capture_radius_m: int = 50
    default_fov: int = 90

    # 캡처 저장 형식 — PNG(2.5MB)는 언어마다 다시 업로드되고 보고서에도 base64 로 박혀
    # 파일을 13MB 로 만들었다. 해상도(1280×1480)는 Opus 고해상도 상한(2576px) 안이라
    # JPEG 로 바꿔도 토큰 수는 그대로고 바이트만 ~6배 줄어든다.
    capture_format: str = "jpeg"     # "jpeg" | "png"
    capture_quality: int = 85

    # ── 모델 라우팅 ───────────────────────────────────────────────
    # 한 모델로 전부 돌리지 않는다. 일의 성격에 맞는 모델을 쓴다:
    #   opus   — 이미지를 보고 해석·추론하는 일 (비전, 판별 사슬)
    #   sonnet — 사실/현상을 찾아오는 일 (웹 검색, 번역). 되도록 많이 쓴다.
    #   fable  — 무관해 보이는 것들의 연관성, 깊은 논리적 추론 (종합 보고서)
    #   haiku  — 빠른 초안·스캐폴딩 (보고서 스크립트 1차)
    # opus 는 sonnet 결과를 **검증**하는 데도 쓰되, 검증량은 최소로 유지한다.
    model_vision: str = "claude-opus-5"      # 이미지 분석·해석·추론
    model_fact: str = "claude-sonnet-5"      # 사실 검색·번역
    model_reason: str = "claude-fable-5"     # 심층 연관·종합
    model_draft: str = "claude-haiku-4-5"    # 빠른 초안
    model_verify: str = "claude-opus-5"      # 검증(최소량)

    # 하위호환 — 역할이 지정되지 않은 호출의 기본값.
    model: str = "claude-opus-5"
    # Opus 5 는 thinking 이 **기본 ON** 이다(Opus 4.8 은 미지정 = 끔). 그냥 모델만 바꾸면
    # 동작·토큰이 조용히 달라지므로 여기서 명시한다.
    #   "adaptive" — 권장. thinking 을 끈 Opus 5 는 tool_use 블록 대신 도구 호출을 본문
    #                텍스트로 써버리는 경우가 있는데(호출이 조용히 실행되지 않는다),
    #                이 앱은 강제 tool_choice 로 구조화 출력을 받으므로 치명적이다.
    #   "disabled" — 이전(Opus 4.8) 동작을 그대로 원할 때. effort 가 high 이하일 때만 유효.
    thinking_mode: str = "adaptive"
    budget_usd: float = 1.0
    max_analyze_images: int = 6
    report_lang: str = "ko"  # 분석/리포트 기본 출력 언어(헤더에서 변경 가능)

    # ── 작업 큐 ────────────────────────────────────────────────────
    # 1 = 완전 순차. 여러 탭에서 눌러도 서버가 한 번에 하나만 실행한다.
    job_concurrency: int = 1
    job_max_pending: int = 64
    job_history: int = 200
    capture_concurrency: int = 1     # 헤드리스 Chromium 동시 실행 상한

    # ── Claude 호출 ───────────────────────────────────────────────
    # 모델과 thinking 은 현행 유지. effort 만 단계별로 조절한다.
    # httpx 의 청크 간격 타임아웃(총 소요가 아니다).
    llm_timeout_s: float = 600.0
    # 호출별 **벽시계** 상한. 스트리밍은 keepalive 때문에 위 값으로는 절대 안 끊긴다 —
    # 리서치 한 건이 22분 넘게 걸려 큐 전체를 막은 실제 사례가 있어 넣었다.
    # 목표: 보고서 1건 300초 이내. 아래 값들은 **임계경로 합이 예산 안에 들어오도록** 잡았다.
    #   analyze 90 + research(병렬) 100 + merge 40 + translate(병렬) 60 = 290s (최악)
    # 기대값은 훨씬 짧다(각각 ~30/~70/~20/~40 = 약 160초).
    analyze_timeout_s: float = 90.0
    research_timeout_s: float = 100.0    # 병렬 샤드 **각각**의 상한
    research_merge_timeout_s: float = 40.0
    # 덩이 하나(최대 30개·6000자) 기준 마감. 실측: 139개를 통째로 넣으면 131초가 걸려
    # 60초 마감에 걸렸고, 그 실패가 조용히 영어 보고서로 나갔다. 지금은 나눠 보내므로
    # 덩이당 25초 안팎이면 끝나지만, 느린 회선을 위해 여유를 둔다.
    translate_timeout_s: float = 90.0
    knowledge_timeout_s: float = 60.0
    draft_timeout_s: float = 45.0
    verify_timeout_s: float = 60.0

    # 보고서 1건의 전체 예산(초). 넘으면 **선택적** 단계(지식 적재 등)를 건너뛴다 —
    # 보고서 자체는 반드시 나온다.
    job_budget_s: float = 300.0

    # ── 잡 내부 병렬성 ────────────────────────────────────────────
    # 큐 자체는 순차(concurrency=1)로 두고, **한 작업 안에서** 독립 단계를 겹쳐 돌린다.
    # 그래야 여러 탭에서 눌러도 순서가 보장되면서 개별 보고서는 빨라진다.
    parallel_inside_job: bool = True
    research_shards: int = 4             # 리서치를 주제별로 쪼개 동시 실행
    max_parallel_llm: int = 6            # 동시 LLM 호출 상한(레이트리밋 방어)
    analyze_effort: str | None = None      # None = API 기본값
    research_effort: str | None = "medium"
    translate_effort: str | None = "low"
    # max_tokens 는 thinking + 응답 텍스트를 **함께** 덮는 상한이다. Opus 5 에서 thinking 이
    # 켜지므로 예전 값 그대로 두면 답변이 중간에 잘린다 → 전 구간에 여유를 뒀다.
    translate_max_tokens: int = 12000
    web_search_max_uses: int = 3

    # ── 스크립트 우선 생성 ────────────────────────────────────────
    # HTML 을 만들기 전에 '무엇을 어떤 순서로 말할지' 스크립트를 먼저 만든다.
    # 초안은 빠른 모델(haiku), 검증은 opus 가 **좁게**(사실 정합성만) 본다.
    script_first: bool = True
    script_verify: bool = True
    script_max_tokens: int = 6000
    script_verify_max_tokens: int = 3000
    script_draft_effort: str | None = None      # haiku 는 effort 미지원 → None
    script_verify_effort: str | None = "low"    # 검증량 최소화

    # ── 리포트 위치 지도 ──────────────────────────────────────────
    # 보고서 hero 아래에 "이게 어디인가"를 줌 단계별로 보여준다. 정적 지도를 base64 로
    # 임베드하므로 서버가 꺼져도, 인터넷이 없어도 열린다(캡처와 같은 방식).
    report_maps: bool = True
    # Static Maps 가 전부 실패하면(리퍼러 제한 키 → 403) 브라우저 렌더로 폴백.
    # 브라우저 1회 기동이 붙으므로 느리다 — GOOGLE_MAPS_STATIC_KEY 를 따로 두면 안 탄다.
    report_maps_fallback: bool = True
    report_map_w: int = 420
    report_map_h: int = 300
    report_map_levels: list = field(default_factory=lambda: [
        {"zoom": 6,  "maptype": "terrain", "label": "map_admin"},   # 광역 행정구역·지형
        {"zoom": 10, "maptype": "roadmap", "label": "map_metro"},   # 도시권
        {"zoom": 14, "maptype": "hybrid",  "label": "map_urban"},   # 시가
        {"zoom": 17, "maptype": "hybrid",  "label": "map_block"},   # 블록
    ])

    # ── 지식 축적 ─────────────────────────────────────────────────
    knowledge_enabled: bool = True
    knowledge_effort: str | None = "medium"
    knowledge_max_tokens: int = 8000
    knowledge_max_atoms: int = 8
    knowledge_recall_limit: int = 12
    knowledge_recall_chars: int = 6000
    synthesis_max_tokens: int = 12000

    captures_dir: Path = field(default_factory=lambda: project_root() / "captures")
    reports_dir: Path = field(default_factory=lambda: project_root() / "docs" / "report")
    knowledge_dir: Path = field(default_factory=lambda: project_root() / "docs" / "knowledge")
    jobs_dir: Path = field(default_factory=lambda: project_root() / "docs" / "jobs")

    # ── 파생 플래그 (프론트로 내려보내는 안전 요약) ──────────────────
    @property
    def has_js_key(self) -> bool:
        return bool(self.js_api_key)

    @property
    def effective_static_key(self) -> str:
        # 별도 static 키가 없으면 JS 키로 폴백 (개발 편의)
        return self.static_api_key or self.js_api_key

    @property
    def has_static_key(self) -> bool:
        return bool(self.effective_static_key)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    def public_config(self) -> dict:
        """브라우저로 안전하게 내려보낼 설정. (JS 키는 리퍼러 제한 브라우저 키라 노출 OK)"""
        from . import i18n

        return {
            "hasJsKey": self.has_js_key,
            "jsApiKey": self.js_api_key,  # 리퍼러 제한된 브라우저 키
            "hasStaticKey": self.has_static_key,
            "hasAnthropic": self.has_anthropic,
            "languages": i18n.languages_public(),
            "defaultLang": i18n.normalize(self.report_lang),
            "defaults": {
                "fov": self.default_fov,
                "radius": self.capture_radius_m,
                "viewportW": self.viewport_w,
                "viewportH": self.viewport_h,
                "mapH": self.map_h,
            },
            "queue": {
                "concurrency": self.job_concurrency,
                "maxPending": self.job_max_pending,
            },
            "knowledgeEnabled": self.knowledge_enabled,
            "baseLangPriority": _base_lang_priority(),
        }


def _base_lang_priority() -> list[str]:
    from . import i18n

    return list(i18n.BASE_LANG_PRIORITY)


def load_settings() -> Settings:
    s = Settings()
    s.js_api_key = os.environ.get("GOOGLE_MAPS_JS_API_KEY", "").strip()
    s.static_api_key = os.environ.get("GOOGLE_MAPS_STATIC_KEY", "").strip()
    s.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if os.environ.get("GEOHELPER_PORT"):
        try:
            s.port = int(os.environ["GEOHELPER_PORT"])
        except ValueError:
            pass
    if os.environ.get("GEOHELPER_REPORT_LANG"):
        s.report_lang = os.environ["GEOHELPER_REPORT_LANG"].strip()
    if os.environ.get("GEOHELPER_CAPTURES"):
        s.captures_dir = Path(os.environ["GEOHELPER_CAPTURES"])
    if os.environ.get("GEOHELPER_KNOWLEDGE"):
        s.knowledge_dir = Path(os.environ["GEOHELPER_KNOWLEDGE"])
    _env_int(s, "GEOHELPER_JOB_CONCURRENCY", "job_concurrency", lo=1, hi=8)
    _env_int(s, "GEOHELPER_CAPTURE_CONCURRENCY", "capture_concurrency", lo=1, hi=4)
    _env_int(s, "GEOHELPER_WEB_SEARCH_MAX", "web_search_max_uses", lo=0, hi=10)
    _env_int(s, "GEOHELPER_CAPTURE_QUALITY", "capture_quality", lo=40, hi=100)
    if os.environ.get("GEOHELPER_CAPTURE_FORMAT", "").strip().lower() in ("png", "jpeg", "jpg"):
        fmt = os.environ["GEOHELPER_CAPTURE_FORMAT"].strip().lower()
        s.capture_format = "jpeg" if fmt in ("jpeg", "jpg") else "png"
    if os.environ.get("GEOHELPER_KNOWLEDGE_OFF", "").strip().lower() in ("1", "true", "yes", "on"):
        s.knowledge_enabled = False
    for d in (s.captures_dir, s.reports_dir, s.knowledge_dir, s.jobs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


def _env_int(s: Settings, env: str, attr: str, *, lo: int, hi: int) -> None:
    raw = os.environ.get(env, "").strip()
    if not raw:
        return
    try:
        setattr(s, attr, max(lo, min(hi, int(raw))))
    except ValueError:
        pass
