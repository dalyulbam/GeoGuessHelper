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

    model: str = "claude-opus-4-8"
    budget_usd: float = 1.0
    max_analyze_images: int = 6
    report_lang: str = "ko"  # 분석/리포트 기본 출력 언어(헤더에서 변경 가능)

    captures_dir: Path = field(default_factory=lambda: project_root() / "captures")
    reports_dir: Path = field(default_factory=lambda: project_root() / "docs" / "report")

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
        }


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
    s.captures_dir.mkdir(parents=True, exist_ok=True)
    s.reports_dir.mkdir(parents=True, exist_ok=True)
    return s
