"""사용자 API 키의 저장 — 평문은 디스크에도 로그에도 남기지 않는다.

이 앱은 이제 **남의 키**를 들고 있다. 그 키가 새면 그 사람의 돈이 나간다. 그래서
규칙을 코드로 박는다:

  · 저장은 암호문만(AES-GCM, `GEOHELPER_KEY_SECRET` 에서 유도한 키).
  · 비밀이 설정돼 있지 않으면 **보관 자체를 거부한다** — "일단 평문으로" 를 막는다.
  · 밖으로 나가는 것은 mask() 결과뿐(`sk-ant-…a91f`). 복호화한 값은 호출 직후 버린다.

왜 Fernet 이 아니라 AES-GCM 인가 — Fernet 도 충분하지만 AEAD 를 직접 쓰면 연관 데이터
(user_id·provider)를 태그에 묶을 수 있다. 그러면 A 회원의 암호문을 B 회원 행에 옮겨
붙여도 복호화가 실패한다.
"""
from __future__ import annotations

import base64
import hashlib
import os

from .config import Settings


class SecretUnavailable(RuntimeError):
    """암호화 비밀이 없다 — 키를 받지도, 읽지도 않는다."""


def _aead(settings: Settings):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ModuleNotFoundError as exc:      # pragma: no cover - 배포 이미지엔 항상 있다
        raise SecretUnavailable(
            "cryptography 미설치 — `uv sync --extra server` 후 다시 시도하세요."
        ) from exc
    secret = (settings.key_enc_secret or "").strip()
    if len(secret) < 16:
        raise SecretUnavailable(
            "GEOHELPER_KEY_SECRET 이 없거나 너무 짧습니다(16자 이상). "
            "설정하기 전에는 사용자 API 키를 보관하지 않습니다."
        )
    # 비밀 문자열 → 32바이트. 사용자가 아무 문장이나 넣어도 되게 한다.
    return AESGCM(hashlib.sha256(secret.encode("utf-8")).digest())


def _aad(user_id: str, provider: str) -> bytes:
    return f"{user_id}:{provider}".encode("utf-8")


def seal(settings: Settings, plaintext: str, *, user_id: str, provider: str) -> str:
    """평문 키 → 저장 가능한 문자열. 연관 데이터로 소유자를 묶는다."""
    if not plaintext or not plaintext.strip():
        raise ValueError("빈 키는 저장하지 않는다.")
    nonce = os.urandom(12)
    ct = _aead(settings).encrypt(nonce, plaintext.strip().encode("utf-8"), _aad(user_id, provider))
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def open_(settings: Settings, sealed: str, *, user_id: str, provider: str) -> str:
    """저장된 문자열 → 평문 키. 호출부는 **쓰고 즉시 버려야** 한다."""
    raw = base64.urlsafe_b64decode(sealed.encode("ascii"))
    return _aead(settings).decrypt(raw[:12], raw[12:], _aad(user_id, provider)).decode("utf-8")


def mask(plaintext: str) -> str:
    """화면·로그에 나가는 유일한 형태. 앞 접두와 꼬리 네 글자만 남긴다."""
    k = (plaintext or "").strip()
    if not k:
        return ""
    head = k[:7] if k.startswith(("sk-ant-", "sk-proj")) else k[:3]
    return f"{head}…{k[-4:]}" if len(k) > 12 else "…" + k[-2:]


def provider_of(plaintext: str) -> str:
    """키 모양으로 제공자를 알아본다 — 사용자가 고르지 않아도 되게.

    사용자가 "ChatGPT 든 Claude 든" 넣는다고 했으므로, 넣는 사람이 종류를 고르는 단계를
    없앤다. 모르겠으면 빈 문자열을 돌려주고 호출부가 물어본다.
    """
    k = (plaintext or "").strip()
    if k.startswith("sk-ant-"):
        return "anthropic"
    if k.startswith(("sk-proj-", "sk-svcacct-")) or (k.startswith("sk-") and len(k) > 40):
        return "openai"
    return ""


__all__ = ["SecretUnavailable", "seal", "open_", "mask", "provider_of"]
