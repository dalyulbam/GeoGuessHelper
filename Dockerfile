# GeoGuessHelper — 배포 이미지.
#
# 왜 Dockerfile 인가 — railpack 은 이 저장소에서 시작 명령을 못 찾아 prepare 단계에서
# 죽었다(`--error-missing-start`, exit 1). 진입점이 pyproject 의 [project.scripts] 에만
# 있고 루트에 main.py/app.py 가 없기 때문이다. 게다가 우리는 **extra 를 골라 설치**해야
# 하는데(기본 `uv sync` 는 analyze/capture 를 안 넣는다) 그 통제를 railpack 에 맡길 수 없다.
#
# 헤드리스 Chromium(playwright) 은 **일부러 넣지 않는다**. 이미지가 1GB 넘게 커지고,
# 서버에서는 Street View Static API 경로가 정답이다(브라우저 렌더는 로컬 도구의 폴백이다).
# 배포 인스턴스에서 캡처를 쓰려면 GOOGLE_MAPS_STATIC_KEY(IP 제한 서버 키)를 넣어야 한다.

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# curl 은 위키 인제스트가 쓴다(wiki.py — 이 환경의 파이썬 ssl 로는 못 받는 경로가 있다).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# 의존성만 먼저 — 소스가 바뀌어도 이 레이어는 캐시된다.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --extra all --extra server

COPY src ./src
RUN uv sync --locked --extra all --extra server

ENV PATH="/opt/venv/bin:${PATH}"

# 앱 데이터(지식 저장소·캡처·큐 로그). 볼륨을 붙이지 않으면 배포마다 날아간다 —
# 회원 원자는 DB 에 쌓이므로 살아남지만, 파일 저장소는 그렇지 않다.
ENV GEOHELPER_DATA_DIR=/data
RUN mkdir -p /data

# $PORT 는 플랫폼이 준다. config.load_settings() 가 그것을 읽고 호스트를 0.0.0.0 으로 연다.
EXPOSE 8799
CMD ["python", "-m", "geoguesshelper.server"]
