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

    # ── LLM 백엔드 ────────────────────────────────────────────────
    # "api"          — ANTHROPIC_API_KEY / 회원이 넣은 키로 부른다(기본).
    # "subscription" — 이 PC 의 Claude 구독(OAuth)으로 부른다. **단독 소유자 모드 전용**이다.
    #
    # 왜 전용인가: 내 구독으로 남의 보고서를 만들어 주는 것은 계정 공유다. quota.py 의
    # "무료 1건" 은 *운영자의 API 키가 돈을 낸다* 는 전제 위에 있고, 구독 모드에서는 그
    # 전제가 "운영자의 개인 구독이 낸다" 로 바뀐다. 그래서 load_settings() 가 다중 사용자
    # 모드를 감지하면 이 값을 **코드로** 되돌린다. 문서로 적어 두는 것으로는 부족하다.
    llm_backend: str = "api"
    # 구독 클라이언트 수. 하나는 한 번에 한 호출만 받는다(동시에 물리면 응답이 뒤바뀐다 —
    # subscription.py 머리말 ①). 실측 헤드리스 1개당 ≈210MB.
    subscription_pool: int = 2
    # 누적 토큰이 이만큼 넘은 클라이언트는 버리고 새로 만든다. 문맥이 쌓이면 쿼터가 새고,
    # 더 나쁘게는 **호출들이 서로를 보게 된다**(번역이 직전 리서치 답을 문맥으로 갖는다).
    subscription_recycle_tokens: int = 8000
    # 5시간 창 사용률이 이 값을 넘으면 잡을 시작 전에 막는다. 넘치면 넘어갈 밸브가 없다
    # (overageStatus=rejected 실측) — 시작해 놓고 중간에 멈추는 것이 제일 나쁘다.
    subscription_quota_stop: float = 0.85
    # 구독을 요청했지만 되돌린 사유(다중 사용자). 비어 있으면 되돌린 적 없다.
    llm_backend_forced: str = ""

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

    # ── 브라우저 렌더 타이밍 ──────────────────────────────────────
    # 예전에는 브라우저 안에서 2600ms 를 **무조건** 기다렸다(타일이 이미 다 와도 2.6초 소모).
    # 이제 JS 는 최소 대기만 하고, 파이썬이 타일 응답이 멎었는지로 정착을 판정한다.
    render_settle_ms: int = 250        # JS 측 최소 대기(파노 setPano 직후 페인트 여유)
    render_quiet_ms: int = 350         # 타일 응답이 이만큼 조용하면 정착으로 본다
    render_settle_max_ms: int = 3000   # 타일이 계속 흘러도 여기서 끊는다(예전 고정값 상한)
    # 헤드리스 Chromium 을 프로세스 수명 동안 재사용한다. 캡처마다 close 하면 Windows +
    # 실시간 백신 환경에서 close 하나가 16~66초씩 걸렸다(실측). 끄면 예전처럼 매번 띄우고 닫는다.
    render_reuse_browser: bool = True
    # 하단 지도(hybrid)의 tilesloaded 폴백. 예전 7000ms 는 ready 조건에 그대로 얹혀 있어
    # 지도가 느리면 캡처 전체가 7초를 기다렸다. 이제 지도는 getPanorama 와 **동시에** 만들고
    # 이 값은 그 병렬 경로의 상한일 뿐이다.
    render_map_wait_ms: int = 2500
    # 캡처 한 건의 벽시계 상한. 예전에는 상한이 없어서 막힌 렌더 하나가 뒤의 모든 캡처를
    # 표시 없이 세웠다(실측 236.78초). 서버가 이 시간에 끊고 CAPTURE_TIMEOUT 을 돌려준다.
    capture_timeout_s: float = 75.0
    # 렌더 워커(단일 스레드) 쪽 상한. 서버 상한보다 짧아야 워커가 먼저 풀린다 —
    # 넘으면 그 브라우저를 버리고 다시 띄운다(다음 캡처가 살아나는 유일한 길).
    render_worker_timeout_s: float = 60.0

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
    # 적재는 세 갈래다(docs/plan/atom-density-map-detail_260829.html §density):
    #   P1 지점 단서 — 캡처의 pano 좌표에 point 스코프로. 예산 밖(백그라운드/사후 적재)에서만 돈다.
    #   P2 장소 사실 — 지금까지의 단일 패스와 같은 재료(narrowing·cultural_economic_read).
    #                  유일하게 잡 예산 안에서 동기 호출된다.
    #   P3 프로파일 사실 — 리서치 프로파일 절마다. P1 과 같이 예산 밖에서만 돈다.
    knowledge_enabled: bool = True
    knowledge_effort: str | None = "medium"
    knowledge_max_tokens: int = 8000
    knowledge_max_atoms: int = 12       # P2 상한(과거 8 — 단일 패스 시절의 값)
    knowledge_p1_max_per_image: int = 5
    knowledge_p1_max_atoms: int = 24
    knowledge_p3_max_per_section: int = 4
    knowledge_p3_max_atoms: int = 20
    knowledge_recall_limit: int = 12
    knowledge_recall_chars: int = 6000
    synthesis_max_tokens: int = 12000

    # ── 자동 정정 루프 (260907 goal · docs/plan/impl-spec_260907.md) ─────────────
    # 보고서 잡이 끝나면 같은 캡처를 지도 없이(blind) 다시 판단 → 실측 pano 좌표·aided 분석으로
    # "X 는 사실 X2" 정정 → 판별자 원자 적재 → 다음 분석 프롬프트에 회상 주입. 사람 승인 없음.
    correction_enabled: bool = True
    correction_effort: str | None = "medium"     # 정정 호출(비전·추론)의 effort
    correction_timeout_s: float = 150.0
    correction_max_atoms: int = 8                # 정정 1건이 남길 판별자 원자 상한
    correction_recall_limit: int = 10            # blind 2패스에 주입할 회상 원자 상한

    # ── 원자 대화 (docs/plan/atom-dialogue_260906.html — 승인 관문 없음, 인용 관문만) ─────
    dialogue_effort: str | None = "medium"
    dialogue_max_tokens: int = 6000
    dialogue_timeout_s: float = 120.0
    dialogue_max_context_atoms: int = 24
    dialogue_max_images: int = 4

    captures_dir: Path = field(default_factory=lambda: project_root() / "captures")
    reports_dir: Path = field(default_factory=lambda: project_root() / "docs" / "report")
    # 어드바이저(아틀라스) 보고서 — docs/report **밖**이다. docs/report/* 는 용량(수십 MB×수백)
    # 때문에 git 이 무시하지만, 아틀라스 보고서는 원자만으로 만든 수백 KB 문서라 추적한다.
    atlas_dir: Path = field(default_factory=lambda: project_root() / "docs" / "atlas")
    knowledge_dir: Path = field(default_factory=lambda: project_root() / "docs" / "knowledge")
    jobs_dir: Path = field(default_factory=lambda: project_root() / "docs" / "jobs")

    # ── 다중 사용자 서버 ──────────────────────────────────────────
    # 비어 있으면 지금까지처럼 **단독 소유자 모드**다 — 인증도, 사용자별 분리도 없다.
    # 로컬에서 매일 쓰는 흐름을 깨지 않기 위한 기본값이다.
    database_url: str = ""               # 있으면 다중 사용자 모드
    key_enc_secret: str = ""             # 사용자 API 키 암호화(없으면 키 보관 자체를 거부한다)
    google_client_id: str = ""
    google_client_secret: str = ""
    public_base_url: str = ""            # OAuth 리디렉션에 쓰는 외부 주소
    admin_emails: list[str] = field(default_factory=list)
    # 과금 단위는 **보고서**다(원자가 아니다). 가입 전 방문자와 무료 회원이 받을 수 있는
    # 보고서 수. 원자는 공용 지식이라 세어서 막을 대상이 아니다 — 260913 정정.
    free_reports: int = 1
    # 사용자별 데이터가 쌓이는 뿌리(회원별 캡처·보고서·작업 기록). 지식은 여기가 아니다.
    data_dir: Path = field(default_factory=lambda: project_root() / "data")

    @property
    def multi_user(self) -> bool:
        return bool(self.database_url)

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
        """Claude 를 부를 수 있는가. 구독 모드에서는 키가 없는 것이 정상이다 —
        예전처럼 키 유무만 보면 분석·리서치가 통째로 꺼진다(server.py:231)."""
        return bool(self.anthropic_api_key) or self.llm_backend == "subscription"

    @property
    def uses_subscription(self) -> bool:
        return self.llm_backend == "subscription"

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
    # 포트 — PaaS(Railway·Render·Fly…)는 $PORT 로 알려 주고 그 포트로만 트래픽을 보낸다.
    # GEOHELPER_PORT 가 더 구체적이므로 우선한다.
    for env in ("GEOHELPER_PORT", "PORT"):
        raw = os.environ.get(env, "").strip()
        if raw:
            try:
                s.port = int(raw)
                break
            except ValueError:
                continue
    # 호스트 — 컨테이너 안에서 127.0.0.1 에 묶으면 **밖에서 도달할 수 없다**(헬스체크 실패).
    # $PORT 가 있다는 것은 곧 PaaS 라는 뜻이므로 0.0.0.0 으로 연다. 로컬 기본은 그대로
    # 127.0.0.1 이다 — 개인 도구가 실수로 LAN 에 열리는 일이 없어야 한다.
    s.host = os.environ.get("GEOHELPER_HOST", "").strip() or (
        "0.0.0.0" if os.environ.get("PORT", "").strip() else s.host  # noqa: S104
    )
    if os.environ.get("GEOHELPER_REPORT_LANG"):
        s.report_lang = os.environ["GEOHELPER_REPORT_LANG"].strip()
    if os.environ.get("GEOHELPER_CAPTURES"):
        s.captures_dir = Path(os.environ["GEOHELPER_CAPTURES"])
    if os.environ.get("GEOHELPER_KNOWLEDGE"):
        s.knowledge_dir = Path(os.environ["GEOHELPER_KNOWLEDGE"])
    if os.environ.get("GEOHELPER_DATA_DIR"):
        s.data_dir = Path(os.environ["GEOHELPER_DATA_DIR"])
    # Railway 등은 DATABASE_URL 을 자동 주입한다. postgres:// 는 SQLAlchemy 가 모르는
    # 옛 스킴이라 여기서 한 번만 바로잡는다.
    db = os.environ.get("DATABASE_URL", "").strip()
    if db.startswith("postgres://"):
        db = "postgresql+psycopg://" + db[len("postgres://"):]
    elif db.startswith("postgresql://"):
        db = "postgresql+psycopg://" + db[len("postgresql://"):]
    s.database_url = db
    s.key_enc_secret = os.environ.get("GEOHELPER_KEY_SECRET", "").strip()
    s.google_client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    s.google_client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    s.public_base_url = os.environ.get("GEOHELPER_PUBLIC_URL", "").strip().rstrip("/")
    s.admin_emails = [e.strip().lower() for e in
                      os.environ.get("GEOHELPER_ADMIN_EMAILS", "").split(",") if e.strip()]
    _env_int(s, "GEOHELPER_FREE_REPORTS", "free_reports", lo=0, hi=1000)
    _env_int(s, "GEOHELPER_JOB_CONCURRENCY", "job_concurrency", lo=1, hi=8)
    _env_int(s, "GEOHELPER_CAPTURE_CONCURRENCY", "capture_concurrency", lo=1, hi=4)
    _env_int(s, "GEOHELPER_WEB_SEARCH_MAX", "web_search_max_uses", lo=0, hi=10)
    _env_int(s, "GEOHELPER_CAPTURE_QUALITY", "capture_quality", lo=40, hi=100)
    _env_int(s, "GEOHELPER_RENDER_SETTLE_MS", "render_settle_ms", lo=0, hi=10000)
    _env_int(s, "GEOHELPER_RENDER_QUIET_MS", "render_quiet_ms", lo=50, hi=5000)
    _env_int(s, "GEOHELPER_RENDER_SETTLE_MAX_MS", "render_settle_max_ms", lo=200, hi=20000)
    _env_int(s, "GEOHELPER_RENDER_MAP_WAIT_MS", "render_map_wait_ms", lo=0, hi=20000)
    _env_float(s, "GEOHELPER_CAPTURE_TIMEOUT_S", "capture_timeout_s", lo=10.0, hi=600.0)
    _env_float(s, "GEOHELPER_RENDER_WORKER_TIMEOUT_S", "render_worker_timeout_s", lo=10.0, hi=600.0)
    if os.environ.get("GEOHELPER_RENDER_REUSE_BROWSER", "").strip().lower() in ("0", "false", "no", "off"):
        s.render_reuse_browser = False
    if os.environ.get("GEOHELPER_CAPTURE_FORMAT", "").strip().lower() in ("png", "jpeg", "jpg"):
        fmt = os.environ["GEOHELPER_CAPTURE_FORMAT"].strip().lower()
        s.capture_format = "jpeg" if fmt in ("jpeg", "jpg") else "png"
    if os.environ.get("GEOHELPER_KNOWLEDGE_OFF", "").strip().lower() in ("1", "true", "yes", "on"):
        s.knowledge_enabled = False
    if os.environ.get("GEOHELPER_CORRECTION_OFF", "").strip().lower() in ("1", "true", "yes", "on"):
        s.correction_enabled = False
    # ── LLM 백엔드 ────────────────────────────────────────────────
    if os.environ.get("GEOHELPER_LLM_BACKEND", "").strip().lower() in ("subscription", "sub"):
        s.llm_backend = "subscription"
    _env_int(s, "GEOHELPER_SUBSCRIPTION_POOL", "subscription_pool", lo=1, hi=6)
    _env_int(s, "GEOHELPER_SUBSCRIPTION_RECYCLE", "subscription_recycle_tokens",
             lo=2000, hi=200_000)
    _env_float(s, "GEOHELPER_SUBSCRIPTION_QUOTA_STOP", "subscription_quota_stop",
               lo=0.1, hi=1.0)
    # 다중 사용자에서는 구독 모드가 존재할 수 없다 — 계정 공유가 되기 때문이다(위 주석).
    # 조용히 되돌리지 않고 배너에 찍을 사유를 남긴다.
    s.llm_backend_forced = ""
    if s.llm_backend == "subscription" and s.multi_user:
        s.llm_backend = "api"
        s.llm_backend_forced = (
            "다중 사용자 모드(DATABASE_URL)에서는 구독 백엔드를 쓸 수 없습니다 — "
            "개인 구독으로 남의 요청을 처리하는 것은 계정 공유입니다. API 키 모드로 되돌렸습니다."
        )

    for d in (s.captures_dir, s.reports_dir, s.atlas_dir, s.knowledge_dir, s.jobs_dir):
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


def _env_float(s: Settings, env: str, attr: str, *, lo: float, hi: float) -> None:
    raw = os.environ.get(env, "").strip()
    if not raw:
        return
    try:
        setattr(s, attr, max(lo, min(hi, float(raw))))
    except ValueError:
        pass


def report_subdir(settings: Settings, country_tag: str) -> Path:
    """보고서가 놓이는 국가 폴더 — docs/report/country/{iso2}/.

    보고서가 60건을 넘자 한 폴더가 읽히지 않았다(260829). 파일명의 국가 조각(alpha-2,
    없으면 국가명 해시)을 그대로 폴더 이름으로 쓴다 — 파일명과 폴더가 같은 규칙이라
    사람이 봐도, 코드가 봐도 어긋나지 않는다. 종합(synthesis) 보고서처럼 한 나라에
    속하지 않는 것은 최상위(docs/report/)에 남는다.

    260907: 국가 폴더를 country/ 한 단계 아래로 내렸다 — 최상위에 국가 코드 폴더 40개와
    문서 폴더(atlas/, civilzation/)·시장조사 문서가 섞여 무엇이 보고서인지 구분되지
    않았다. 아틀라스 보고서는 docs/atlas/(settings.atlas_dir)로 나갔다.
    """
    tag = (country_tag or "").strip().lower()
    return settings.reports_dir / "country" / tag if tag else settings.reports_dir


def find_report(settings: Settings, name: str) -> Path | None:
    """보고서 파일명(basename) → 실제 경로. 국가 하위 폴더, 그다음 docs/atlas 까지 뒤진다.

    지식 저장소·잡 로그·뷰어는 전부 basename 만 기억한다(폴더는 정리 규칙일 뿐 정체성이
    아니다). 그래서 어디에 있든 이름으로 찾는다 — 최상위를 먼저, 그다음 하위 폴더
    (country/{iso2}/), 마지막으로 docs/report 밖의 아틀라스 폴더. 그래서 아틀라스 보고서도
    같은 /reports/{name} URL 로 열린다.
    """
    base = Path(name).name
    if not base:
        return None
    direct = settings.reports_dir / base
    if direct.is_file():
        return direct
    for p in settings.reports_dir.rglob(base):
        if p.is_file():
            return p
    atlas = getattr(settings, "atlas_dir", None)
    if atlas is not None and atlas.exists():
        direct = atlas / base
        if direct.is_file():
            return direct
        for p in atlas.rglob(base):
            if p.is_file():
                return p
    return None
