"""이미 만들어진 보고서 HTML 의 번역을 사후에 고친다.

왜 필요한가
    번역이 통째로 실패해도 조용히 원문(영어)이 남는 버그가 있었다(v0.3.3 에서 수정).
    그 시기에 나온 보고서들은 한국어/프랑스어 탭이 영어인 채로 디스크에 남아 있고,
    분석 JSON 은 저장하지 않으므로 다시 렌더할 수가 없다. 남은 것은 HTML 뿐이다.

무엇을 하는가
    문서(`<div class="wrap doc" id="doc-XX">`) 안의 **텍스트 노드만** 골라 번역하고
    제자리에 되돌린다. 태그·속성·data: URI 는 건드리지 않는다 —
    이미지가 4MB 씩 base64 로 박혀 있어서, 속성을 건드리면 파일이 깨진다.

안전장치
    · 텍스트 노드는 `>` 와 `<` 사이만 본다. 속성값은 구조상 들어올 수 없다.
    · <script>/<style> 구간은 통째로 건너뛴다.
    · 이미 대상 언어인 문자열(한국어면 한글 포함)은 보내지 않는다.
    · 숫자·기호·짧은 토큰은 보내지 않는다(번역해도 달라질 것이 없다).
    · 되돌릴 때는 **뒤에서 앞으로** 치환해 앞쪽 위치가 밀리지 않게 한다.
    · 원본은 `.bak` 으로 남긴다.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import i18n, translate
from .config import Settings

_DOC_RE = re.compile(r'<div class="wrap doc" id="doc-([a-z-]+)"[^>]*>', re.I)
_SKIP_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
# 텍스트 노드: 태그 사이의 내용
_TEXT_RE = re.compile(r">([^<>]+)<")

# 대상 언어에 이미 들어맞는 글자가 있으면 번역할 필요가 없다
_HAS = {
    "ko": re.compile(r"[가-힣]"),
    "ja": re.compile(r"[ぁ-んァ-ン一-龥]"),
    "zh": re.compile(r"[一-龥]"),
}


def _doc_spans(html: str) -> list[tuple[str, int, int]]:
    """(lang, 시작, 끝) 목록. 각 언어 문서가 HTML 안에서 차지하는 구간."""
    marks = [(m.group(1), m.start()) for m in _DOC_RE.finditer(html)]
    out = []
    for i, (lang, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(html)
        out.append((lang, start, end))
    return out


def _skip_ranges(html: str, lo: int, hi: int) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _SKIP_RE.finditer(html, lo, hi)]


# 코드처럼 생긴 토큰 — 번역하면 안 되는 것들(모델 id, 파일명, 해시 등)
_CODEISH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")


def _worth(s: str) -> bool:
    """번역할 가치가 있는 문자열인가.

    처음엔 '8자 이상 · 라틴 낱말 2개 이상'을 요구했는데 그러면 후보 칩의
    'Europe' 'Croatia' 'Serbia' 같은 **한 낱말짜리 고유명사가 통째로 빠진다**
    (실측: 복구 후에도 판별 사슬의 후보 칩만 영어로 남았다).
    이제는 낱말 하나여도 보낸다. 무엇을 그대로 둘지는 모델이 판단한다 —
    시스템 프롬프트가 '정착된 대응어가 없으면 원문 유지'를 지시하고 있어서
    Konzum·Studenac 같은 상표는 실제로 그대로 남았다.
    다만 모델 id(claude-opus-5)나 파일명처럼 **코드로 보이는 토큰**은 제외한다.
    """
    t = s.strip()
    if len(t) < 3:
        return False
    if not re.search(r"[A-Za-z]{3}", t):
        return False
    if _CODEISH.match(t) and (re.search(r"\d", t) or "." in t or "_" in t):
        return False
    return True


def collect(html: str, lang: str) -> list[tuple[int, int, str]]:
    """해당 언어 문서에서 아직 번역되지 않은 텍스트 노드를 (시작, 끝, 값) 으로 모은다."""
    target = i18n.normalize(lang)
    has = _HAS.get(target)
    for dl, lo, hi in _doc_spans(html):
        if i18n.normalize(dl) != target:
            continue
        skips = _skip_ranges(html, lo, hi)

        def blocked(a: int) -> bool:
            return any(s <= a < e for s, e in skips)

        out = []
        for m in _TEXT_RE.finditer(html, lo, hi):
            if blocked(m.start()):
                continue
            raw = m.group(1)
            if has and has.search(raw):
                continue                     # 이미 대상 언어다
            if not _worth(raw):
                continue
            out.append((m.start(1), m.end(1), raw))
        return out
    return []


def repair_file(path: Path, settings: Settings, *, base_lang: str = "en",
                langs: list[str] | None = None, dry_run: bool = False) -> dict:
    """보고서 하나를 고친다. 반환 {file, per_lang:{lang:{found,translated}}, cost_usd}."""
    html = path.read_text(encoding="utf-8")
    present = [dl for dl, _, _ in _doc_spans(html)]
    todo = [l for l in (langs or present) if i18n.normalize(l) != i18n.normalize(base_lang)]

    edits: list[tuple[int, int, str]] = []
    per: dict[str, dict] = {}
    cost = 0.0
    for lang in todo:
        slots = collect(html, lang)
        per[lang] = {"found": len(slots), "translated": 0}
        if not slots or dry_run:
            continue
        texts = {f"t{i}": v for i, (_, _, v) in enumerate(slots)}
        st: dict = {}
        done, c = translate.translate_texts(texts, lang, settings, base_lang=base_lang, stats=st)
        cost += c
        per[lang].update({"translated": st.get("translated", 0),
                          "chunks": st.get("chunks", 0),
                          "failed_chunks": st.get("failed_chunks", 0)})
        for i, (a, b, v) in enumerate(slots):
            new = done.get(f"t{i}", v)
            if new != v:
                # 텍스트 노드이므로 `<` `>` 만 막으면 구조가 깨지지 않는다
                edits.append((a, b, new.replace("<", "&lt;").replace(">", "&gt;")))

    if edits and not dry_run:
        edits.sort(key=lambda e: e[0], reverse=True)   # 뒤에서 앞으로
        buf = html
        for a, b, new in edits:
            buf = buf[:a] + new + buf[b:]
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(path, bak)
        path.write_text(buf, encoding="utf-8")

    return {"file": path.name, "per_lang": per, "edits": len(edits), "cost_usd": round(cost, 4)}
