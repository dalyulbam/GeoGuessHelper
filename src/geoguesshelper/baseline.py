"""평가 기준선 — blind(로드뷰 패널만) vs aided(하단 지도 포함) 전후 분석과 추론 계층.

문제(docs/plan/atom-dialogue_260906.html §8.1): 캡처 하단 지도는 zoom 15 + 정답 좌표 빨간
마커라, 비전 모델의 narrowing/discriminator 가 정답을 본 뒤의 사후 서술일 수 있다. 그래서
같은 캡처를 두 번 분석한다.

  1-1  blind — 합성 이미지의 위쪽(로드뷰 패널)만 잘라 넣는다. 지도 없이 무엇이 보이고 어디까지
       추론되는지 최대한 뽑는다.
  1-2  aided — 원본 합성 이미지(하단 지도 포함)를 넣는다. 보조 지식이 있을 때 추론이 어떻게
       달라지는지 본다.

두 결과를 각각 **샌드박스 지식 저장소**(docs/knowledge/baseline/store_blind, store_aided)에
적재하고(P1 지점 단서 + P2 장소 사실), 원자를 대조해 추론 계층(tier)을 매긴다.

  unaided    — blind 에서도 나온 사실 = 로드뷰만으로 추론되는 일반 추론
  aided      — aided 에서만 나온 사실 = 보조 지식(지도)이 있어야만 추론되는 사실
  blind-only — blind 에서만 나온 관찰 = 지도가 있으면 사라지는 관찰(주의 분산·억제)

산출물(docs/knowledge/baseline/):
  runs/<job>.json        두 모드의 원문 분석·비용·지표
  <job>.md               전후 대조(추론 사슬·단서·원자·지표) + 로직 변화 서술
  tiers.json             전체 원자의 계층 판정
  README.md              집계표

지표: 좌표 오차 km(coordinate_estimate vs 캡처 pano 평균), 국가 적중, 도달 level,
      ruled_out 이 비어 있지 않은 단계 수, cue 수.
본 저장소(docs/knowledge/atoms)는 건드리지 않는다.
"""
from __future__ import annotations

import dataclasses
import difflib
import json
import re
import time
from pathlib import Path
from typing import Any

from .config import Settings

_LEVELS = ["continent", "country", "macro_region", "cultural_sphere", "admin_region", "metro_area", "district"]
_COORD_RE = re.compile(r"report_([a-z0-9]+)_.*?_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)_(\d{6})_(\d{6})_")


# ── 입력 수집 ─────────────────────────────────────────────────────
def load_jobs(settings: Settings) -> list[dict]:
    """jobs.jsonl 에서 image_panos 가 있는 잡만. 뒤 항목이 더 최신이라 같은 id 는 덮어쓴다."""
    log = settings.jobs_dir / "jobs.jsonl"
    if not log.exists():
        return []
    by_id: dict[str, dict] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        res = d.get("result") or {}
        pa = res.get("primaryAnalysis") or {}
        if not (isinstance(pa, dict) and pa.get("image_panos") and pa.get("images")):
            continue
        rep = next((r for r in (res.get("reports") or []) if isinstance(r, dict) and r.get("file")), {})
        by_id[d.get("id") or ""] = {
            "job_id": d.get("id"), "label": d.get("label"), "report_file": rep.get("file"),
            "images": list(pa.get("images") or []), "image_panos": dict(pa.get("image_panos") or {}),
            "aided_prior": pa.get("analysis") or {}, "prior_lang": pa.get("lang"),
        }
    return [v for k, v in by_id.items() if k]


def _truth(job: dict) -> dict:
    panos = [p for p in job["image_panos"].values() if isinstance(p, dict) and p.get("lat") is not None]
    lat = sum(float(p["lat"]) for p in panos) / len(panos) if panos else None
    lng = sum(float(p["lng"]) for p in panos) / len(panos) if panos else None
    iso, f_lat, f_lng = None, None, None
    m = _COORD_RE.search(job.get("report_file") or "")
    if m:
        iso = m.group(1).upper()
        iso = iso if len(iso) == 2 else None
        f_lat, f_lng = float(m.group(2)), float(m.group(3))
    return {"lat": lat, "lng": lng, "iso": iso, "file_lat": f_lat, "file_lng": f_lng, "n_panos": len(panos)}


# ── blind 크롭 ─────────────────────────────────────────────────────
def blind_crop(src: Path, dst_dir: Path, settings: Settings) -> tuple[Path, int]:
    """합성 이미지의 로드뷰 패널만 남긴다. 경계 = h·viewport_h/(viewport_h+map_h)
    (render_google._panel_brightness 와 같은 식). 반환 (경로, 잘린 높이 px)."""
    from PIL import Image  # type: ignore

    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    with Image.open(src) as im:
        w, h = im.size
        cut = max(1, int(h * settings.viewport_h / max(1, settings.viewport_h + settings.map_h)))
        if dst.exists():
            return dst, cut
        top = im.convert("RGB").crop((0, 0, w, cut))
        if src.suffix.lower() in (".jpg", ".jpeg"):
            top.save(dst, "JPEG", quality=settings.capture_quality, optimize=True)
        else:
            top.save(dst, "PNG")
    return dst, cut


# ── 지표 ───────────────────────────────────────────────────────────
def metrics(analysis: dict | None, truth: dict) -> dict:
    from .knowledge import haversine_km

    a = analysis or {}
    g = a.get("best_guess") or {}
    est = g.get("coordinate_estimate") or {}
    err = None
    if truth.get("lat") is not None and isinstance(est, dict) and est.get("lat") is not None:
        try:
            err = round(haversine_km(truth["lat"], truth["lng"], float(est["lat"]), float(est["lng"])), 2)
        except (TypeError, ValueError):
            err = None
    chain = [s for s in (a.get("narrowing") or []) if isinstance(s, dict)]
    levels = [s.get("level") for s in chain if s.get("level") in _LEVELS]
    reached = max(levels, key=_LEVELS.index) if levels else None
    return {
        "country": g.get("country"), "country_iso": g.get("country_iso"),
        "country_correct": (bool(truth.get("iso")) and (g.get("country_iso") or "").upper() == truth["iso"]) or None
        if truth.get("iso") else None,
        "region": g.get("region_or_state"), "city": g.get("city"),
        "confidence": g.get("confidence"), "overall_confidence": a.get("overall_confidence"),
        "error_km": err, "reached_level": reached, "n_steps": len(chain),
        "n_steps_with_cut": sum(1 for s in chain if s.get("ruled_out")),
        "n_cues": len(a.get("cues") or []), "n_landmarks": len(a.get("landmarks") or []),
        "cue_categories": sorted({str(c.get("category")) for c in (a.get("cues") or []) if isinstance(c, dict)}),
    }


# ── 대조 ───────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", " ", (s or "").lower()).strip()


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


_STOP = set("the a an and of with on in at to for from by is are was were this that these those its it as or "
            "not no into over under near across which who whose than then here there very also only".split())


def _toks(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out |= {w for w in re.findall(r"[a-z0-9가-힣]+", (t or "").lower()) if len(w) >= 3 and w not in _STOP}
    return out


def _pair_score(title_a: str, title_b: str, toks_a: set[str], toks_b: set[str], same_cat: bool) -> float:
    """같은 사실인가 — 제목 문자열 유사도 · 토큰 자카드 · 같은 범주 보정 중 최대.

    같은 사실이 두 실행에서 다른 제목으로 나오는 일이 흔해(실측: 'Red HÓTEL HVAMMSTANGI sign' vs
    'Hótel Hvammstangi signage' 자카드 0.84·제목 0.62, 'shelterbelt' vs 'windbreak' 자카드 0.16·제목
    0.21·같은 범주) 제목 하나로는 못 잡는다. 임계 0.40 은 store_blind/store_aided 첫 실행(16×16)으로
    맞춘 값이다 — 교회↔무지개 횡단보도(0.17/0.35, 같은 범주 → 0.50)처럼 헷갈리는 쌍은 **전역 탐욕**
    매칭(점수 내림차순, 양쪽 미사용)으로 진짜 짝(0.60)이 먼저 잡히게 해서 걸러낸다.
    """
    tr = difflib.SequenceMatcher(None, _norm(title_a), _norm(title_b)).ratio()
    tj = len(toks_a & toks_b) / len(toks_a | toks_b) if toks_a and toks_b else 0.0
    s = max(tr, tj * 2.2)
    if same_cat:
        s = max(s, tj * 1.5 + tr * 0.7)
    return s


def _greedy_match(left: list, right: list, score, thr: float) -> tuple[list[tuple], list, list]:
    """전역 탐욕 1:1 매칭. 반환 (짝[(l, r, score)], 왼쪽 미매칭, 오른콽 미매칭)."""
    pairs = sorted(((score(l, r), i, j) for i, l in enumerate(left) for j, r in enumerate(right)),
                   reverse=True)
    used_l: set[int] = set()
    used_r: set[int] = set()
    out: list[tuple] = []
    for s, i, j in pairs:
        if s < thr:
            break
        if i in used_l or j in used_r:
            continue
        used_l.add(i)
        used_r.add(j)
        out.append((left[i], right[j], round(min(1.0, s), 2)))
    return (out, [l for i, l in enumerate(left) if i not in used_l],
            [r for j, r in enumerate(right) if j not in used_r])


def diff_cues(blind: dict, aided: dict, thr: float = 0.40) -> dict:
    """단서를 관찰 문장·토큰·범주로 짝짓는다. 반환 {unaided:[…], aided_only:[…], blind_only:[…]}"""
    bc = [c for c in (blind.get("cues") or []) if isinstance(c, dict)]
    ac = [c for c in (aided.get("cues") or []) if isinstance(c, dict)]

    def score(c, d):
        return _pair_score(c.get("observation") or "", d.get("observation") or "",
                           _toks(c.get("observation"), c.get("category")),
                           _toks(d.get("observation"), d.get("category")),
                           _norm(str(c.get("category"))) == _norm(str(d.get("category"))))

    pairs, blind_only, aided_only = _greedy_match(bc, ac, score, thr)
    return {"unaided": [{"blind": c, "aided": d, "sim": s} for c, d, s in pairs],
            "aided_only": aided_only, "blind_only": blind_only}


def diff_atoms(store_b, store_a, thr: float = 0.40) -> dict:
    """샌드박스 두 저장소의 원자를 제목·본문·태그·범주로 대조 → tier."""
    B = store_b.all_atoms()
    A = store_a.all_atoms()

    def score(b, a):
        return _pair_score(b.title, a.title, _toks(b.title, b.body, *b.tags), _toks(a.title, a.body, *a.tags),
                           (b.category or "other") == (a.category or "other"))

    pairs, blind_only, aided_only = _greedy_match(B, A, score, thr)
    return {"unaided": pairs, "aided_only": aided_only, "blind_only": blind_only}


# ── 실행 ───────────────────────────────────────────────────────────
def _sandbox(settings: Settings, name: str) -> Settings:
    return dataclasses.replace(settings, knowledge_dir=settings.knowledge_dir / "baseline" / f"store_{name}")


def run_job(job: dict, settings: Settings, *, lang: str = "en", ingest: bool = True,
            narrate: bool = True, log=print) -> dict:
    from . import knowledge, llm
    from .analyze import analyze_captures

    base = settings.knowledge_dir / "baseline"
    runs = base / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    out_path = runs / f"{job['job_id']}.json"
    truth = _truth(job)

    originals = [settings.captures_dir / Path(n).name for n in job["images"]]
    originals = [p for p in originals if p.exists()][: settings.max_analyze_images]
    if not originals:
        return {"job_id": job["job_id"], "status": "NO_IMAGES"}
    blind_dir = settings.captures_dir / "blind"
    blinds, cuts = [], []
    for p in originals:
        bp, cut = blind_crop(p, blind_dir, settings)
        blinds.append(bp)
        cuts.append(cut)

    # 두 모드를 동시에 — 서로 독립이라 임계경로가 절반이 된다.
    log(f"  [{job['job_id']}] 분석 {len(originals)}장 × 2모드 (blind cut={cuts[0]}px)")
    rb, ra = llm.gather(
        [lambda: analyze_captures(blinds, settings, lang), lambda: analyze_captures(originals, settings, lang)],
        settings, limit=2,
    )
    if isinstance(rb, Exception) or isinstance(ra, Exception):
        return {"job_id": job["job_id"], "status": "API_ERROR",
                "message": str(rb if isinstance(rb, Exception) else ra)}
    cost = (rb.get("cost_usd") or 0) + (ra.get("cost_usd") or 0)
    ab, aa = rb.get("analysis") or {}, ra.get("analysis") or {}
    rec: dict[str, Any] = {
        "job_id": job["job_id"], "report_file": job.get("report_file"), "label": job.get("label"),
        "lang": lang, "images": [p.name for p in originals], "blind_cut_px": cuts,
        "image_panos": {Path(k).name: v for k, v in job["image_panos"].items()},
        "truth": truth, "created": time.time(),
        "blind": {"status": rb.get("status"), "analysis": ab, "cost_usd": rb.get("cost_usd"),
                  "model": rb.get("used_model"), "metrics": metrics(ab, truth)},
        "aided": {"status": ra.get("status"), "analysis": aa, "cost_usd": ra.get("cost_usd"),
                  "model": ra.get("used_model"), "metrics": metrics(aa, truth)},
        "aided_prior": {"lang": job.get("prior_lang"), "metrics": metrics(job.get("aided_prior"), truth),
                        "best_guess": (job.get("aided_prior") or {}).get("best_guess")},
        "cues": diff_cues(ab, aa),
    }

    # 샌드박스 적재 — P1(캡처 pano 좌표) + P2(장소 사실). 본 저장소는 건드리지 않는다.
    if ingest and ab and aa:
        panos = rec["image_panos"]
        for mode, res in (("blind", rb), ("aided", ra)):
            sb = _sandbox(settings, mode)
            payload = dict(res)
            payload["image_panos"] = panos
            kb = knowledge.ingest(
                sb, analysis=payload, research=None, lat=truth.get("lat"), lng=truth.get("lng"),
                report_file=f"baseline_{job['job_id']}_{mode}", known=[], image_panos=panos,
                passes=("p1", "p2"),
            )
            cost += kb.get("cost_usd") or 0.0
            rec[mode]["atoms"] = kb.get("atoms") or []
            rec[mode]["ingest"] = {k: v for k, v in kb.items() if k != "atoms"}
            log(f"    {mode}: 원자 {kb.get('created', 0)} 신규 · {kb.get('merged', 0)} 병합 · ${kb.get('cost_usd', 0):.3f}")

    # 로직 변화 서술 — 사슬 두 개를 나란히 주고 무엇이 달라졈는지 짧게(비용 소액).
    if narrate and ab and aa:
        try:
            rec["narrative"] = _narrate(ab, aa, truth, settings)
            cost += rec["narrative"].get("cost_usd") or 0.0
        except Exception as exc:  # noqa: BLE001
            rec["narrative"] = {"error": str(exc)}

    rec["cost_usd"] = round(cost, 4)
    rec["status"] = "OK"
    out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return rec


_NARRATE_TOOL = {
    "name": "logic_delta",
    "description": "Compare two reasoning chains (blind vs aided) and state what changed.",
    "input_schema": {
        "type": "object",
        "required": ["blind_logic", "aided_logic", "delta", "map_dependent_steps", "verdict"],
        "properties": {
            "blind_logic": {"type": "string", "description": "3-4 sentences: how the blind chain reached its conclusion, which cues carried it."},
            "aided_logic": {"type": "string", "description": "3-4 sentences: how the aided chain reached its conclusion."},
            "delta": {"type": "string", "description": "What changed between them — which steps/cues appear only with the map, which discriminators became post-hoc."},
            "map_dependent_steps": {"type": "array", "items": {"type": "string"},
                                    "description": "Narrowing steps (level: conclusion) in the aided chain that could not have been made without the map panel."},
            "verdict": {"type": "string", "enum": ["blind-sufficient", "map-helped", "map-leaked-answer", "inconclusive"]},
        },
    },
}


def _narrate(blind: dict, aided: dict, truth: dict, settings: Settings) -> dict:
    from . import llm

    def chain(a: dict) -> str:
        rows = []
        for s in (a.get("narrowing") or []):
            if isinstance(s, dict):
                rows.append(f"- [{s.get('level')}] Q: {s.get('question')} | obs: {s.get('observation')} | "
                            f"disc: {s.get('discriminator')} | out: {', '.join(map(str, s.get('ruled_out') or []))} "
                            f"| => {s.get('conclusion')}")
        g = a.get("best_guess") or {}
        return (f"best_guess: {g.get('country')} / {g.get('region_or_state')} / {g.get('city')} "
                f"(conf {g.get('confidence')})\n" + "\n".join(rows))

    user = (
        "GROUND TRUTH (from capture pano coordinates, NOT shown to the analyst): "
        f"lat {truth.get('lat')}, lng {truth.get('lng')}, iso {truth.get('iso')}\n\n"
        f"BLIND CHAIN (street-view panel only):\n{chain(blind)}\n\n"
        f"AIDED CHAIN (same images + bottom map panel with a red marker at the answer):\n{chain(aided)}\n\n"
        "Call logic_delta once. Be specific: name the steps. If the aided chain's fine-grained steps "
        "(admin_region/metro_area/district) rest on map labels rather than street-level evidence, say "
        "the map leaked the answer."
    )
    # role="fact" 는 thinking 을 끄는데(llm.py), 그 상태의 도구 입력에서 다음 파라미터의 XML 태그가
    # 문자열 값 안으로 새어 들어온 실측이 있다(delta 끝에 '</delta><parameter name=…>'). 모델은
    # 그대로 sonnet 을 쓰되 role 을 비워 thinking 을 기본값으로 두고, 그래도 새면 아래 _clean 이 걷어낸다.
    resp = llm.call(
        settings, system=("You audit geographic reasoning chains for a research tool. You compare a "
                          "blind analysis with an aided one and say plainly what the aid changed."),
        messages=[{"role": "user", "content": user}], tools=[_NARRATE_TOOL],
        tool_choice={"type": "tool", "name": "logic_delta"}, max_tokens=3000, effort="low",
        deadline_s=90.0, model=settings.model_fact,
    )
    out = _clean_narrative(llm.tool_input(resp, "logic_delta") or {})
    out["cost_usd"] = round(llm.spend(resp), 4)
    out["model"] = llm.used_model(resp)
    return out


_LEAK_RE = re.compile(r"</?(?:delta|blind_logic|aided_logic|verdict|map_dependent_steps|parameter)[^>]*>")
_PARAM_RE = re.compile(r'<parameter name="(\w+)">(.*?)(?=<parameter name=|</parameter>|$)', re.S)


def _clean_narrative(n: dict) -> dict:
    """도구 입력 문자열에 새어 든 XML 파라미터 조각을 걷어내고, 잃은 필드는 되살린다."""
    out = dict(n)
    leaked: dict[str, str] = {}
    for k, v in list(out.items()):
        if not isinstance(v, str):
            continue
        for name, body in _PARAM_RE.findall(v):
            leaked[name] = body.strip()
        cut = _LEAK_RE.search(v)
        if cut:
            out[k] = v[: cut.start()].rstrip()
    for name, body in leaked.items():
        if out.get(name) in (None, "", []):
            if name == "map_dependent_steps":
                try:
                    val = json.loads(body)
                    out[name] = val if isinstance(val, list) else [str(val)]
                except ValueError:
                    out[name] = [s.strip(" -•\"'") for s in re.split(r"\n|\",\s*\"", body) if s.strip()]
            else:
                out[name] = _LEAK_RE.sub("", body).strip()
    return out


def run(settings: Settings, *, limit: int = 0, only: list[str] | None = None, skip_existing: bool = True,
        lang: str = "en", ingest: bool = True, narrate: bool = True, log=print) -> dict:
    jobs = load_jobs(settings)
    if only:
        jobs = [j for j in jobs if j["job_id"] in set(only)]
    runs = settings.knowledge_dir / "baseline" / "runs"
    if skip_existing:
        jobs = [j for j in jobs if not (runs / f"{j['job_id']}.json").exists()]
    if limit:
        jobs = jobs[:limit]
    log(f"[baseline] 대상 잡 {len(jobs)}건 (lang={lang}, ingest={ingest})")
    done, total = [], 0.0
    for j in jobs:
        rec = run_job(j, settings, lang=lang, ingest=ingest, narrate=narrate, log=log)
        done.append({k: rec.get(k) for k in ("job_id", "status", "cost_usd", "message")})
        total += rec.get("cost_usd") or 0.0
        if rec.get("status") == "OK":
            mb, ma = rec["blind"]["metrics"], rec["aided"]["metrics"]
            log(f"    blind  → {mb['country']} · {mb['reached_level']} · err {mb['error_km']} km · cues {mb['n_cues']}")
            log(f"    aided  → {ma['country']} · {ma['reached_level']} · err {ma['error_km']} km · cues {ma['n_cues']}")
    log(f"[baseline] 완료 {sum(1 for d in done if d['status'] == 'OK')}/{len(done)} · ${total:.3f}")
    return {"done": done, "cost_usd": round(total, 4)}


# ── 보고 ───────────────────────────────────────────────────────────
def _load_runs(settings: Settings) -> list[dict]:
    runs = settings.knowledge_dir / "baseline" / "runs"
    out = []
    for p in sorted(runs.glob("*.json")) if runs.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if d.get("status") == "OK":
            out.append(d)
    return out


def _tag_tiers(settings: Settings, log=print) -> dict:
    """샌드박스 원자에 tier 를 써 넣고 tiers.json 을 만든다."""
    from .knowledge import Store

    sb = Store(_sandbox(settings, "blind").knowledge_dir)
    sa = Store(_sandbox(settings, "aided").knowledge_dir)
    d = diff_atoms(sb, sa)
    tiers: dict[str, list[dict]] = {"unaided": [], "aided": [], "blind-only": []}
    for b, a, sim in d["unaided"]:
        for st, atom in ((sb, b), (sa, a)):
            atom.tier = "unaided"
            st.save(atom)
        tiers["unaided"].append({"blind": b.id, "aided": a.id, "title": b.title, "layer": b.layer,
                                 "category": b.category, "scope": b.scope, "sim": sim})
    for a in d["aided_only"]:
        a.tier = "aided"
        sa.save(a)
        tiers["aided"].append({"aided": a.id, "title": a.title, "layer": a.layer, "category": a.category,
                               "scope": a.scope, "reports": a.reports})
    for b in d["blind_only"]:
        b.tier = "blind-only"
        sb.save(b)
        tiers["blind-only"].append({"blind": b.id, "title": b.title, "layer": b.layer, "category": b.category,
                                    "scope": b.scope, "reports": b.reports})
    summary = {k: len(v) for k, v in tiers.items()}
    by_layer: dict[str, dict[str, int]] = {}
    for k, rows in tiers.items():
        for r in rows:
            by_layer.setdefault(r["layer"], {}).setdefault(k, 0)
            by_layer[r["layer"]][k] += 1
    out = {"created": time.time(), "summary": summary, "by_layer": by_layer, "tiers": tiers,
           "method": "same layer + title similarity >= 0.72 (difflib) between store_blind and store_aided"}
    (settings.knowledge_dir / "baseline" / "tiers.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"[baseline] tiers: {summary}")
    return out


def _fmt(v) -> str:
    return "—" if v is None else str(v)


def _job_md(rec: dict, tiers: dict) -> str:
    mb, ma, mp = rec["blind"]["metrics"], rec["aided"]["metrics"], rec["aided_prior"]["metrics"]
    t = rec["truth"]
    lines = [
        "---",
        json.dumps({"job": rec["job_id"], "report": rec.get("report_file"), "truth": t, "lang": rec.get("lang"),
                    "cost_usd": rec.get("cost_usd"), "created": rec.get("created")}, ensure_ascii=False),
        "---", "",
        f"# {rec.get('label') or rec['job_id']} — blind vs aided",
        "",
        f"보고서 `{rec.get('report_file')}` · 캡처 {len(rec['images'])}장 · 정답(pano 평균) {t.get('lat')}, {t.get('lng')} · ISO {t.get('iso')}",
        "",
        "## 지표",
        "",
        "| | blind (로드뷰만) | aided (지도 포함) | aided_prior (원 보고서) |",
        "|---|---|---|---|",
        f"| 국가 | {_fmt(mb['country'])} ({_fmt(mb['country_iso'])}) | {_fmt(ma['country'])} ({_fmt(ma['country_iso'])}) | {_fmt(mp['country'])} |",
        f"| 국가 적중 | {_fmt(mb['country_correct'])} | {_fmt(ma['country_correct'])} | {_fmt(mp['country_correct'])} |",
        f"| 지역 / 도시 | {_fmt(mb['region'])} / {_fmt(mb['city'])} | {_fmt(ma['region'])} / {_fmt(ma['city'])} | {_fmt(mp['region'])} / {_fmt(mp['city'])} |",
        f"| 좌표 오차 km | **{_fmt(mb['error_km'])}** | **{_fmt(ma['error_km'])}** | {_fmt(mp['error_km'])} |",
        f"| 도달 level | {_fmt(mb['reached_level'])} | {_fmt(ma['reached_level'])} | {_fmt(mp['reached_level'])} |",
        f"| 단계 수 (ruled_out 있는 단계) | {mb['n_steps']} ({mb['n_steps_with_cut']}) | {ma['n_steps']} ({ma['n_steps_with_cut']}) | {mp['n_steps']} ({mp['n_steps_with_cut']}) |",
        f"| 단서 수 | {mb['n_cues']} | {ma['n_cues']} | {mp['n_cues']} |",
        f"| 확신 | {_fmt(mb['confidence'])} | {_fmt(ma['confidence'])} | {_fmt(mp['confidence'])} |",
        "",
    ]
    n = _clean_narrative(rec.get("narrative") or {})
    if n and not n.get("error"):
        lines += ["## 로직 변화 (감사 서술)", "",
                  f"**판정**: `{n.get('verdict')}`", "",
                  f"**blind 의 추론**: {n.get('blind_logic')}", "",
                  f"**aided 의 추론**: {n.get('aided_logic')}", "",
                  f"**달라진 것**: {n.get('delta')}", ""]
        if n.get("map_dependent_steps"):
            lines += ["**지도가 있어야만 가능했던 단계**:"] + [f"- {s}" for s in n["map_dependent_steps"]] + [""]

    def chain_rows(a: dict) -> list[str]:
        rows = []
        for s in (a.get("narrowing") or []):
            if isinstance(s, dict):
                rows.append(f"| `{s.get('level')}` | {s.get('question')} | {s.get('observation')} | "
                            f"{s.get('discriminator')} | {', '.join(map(str, s.get('ruled_out') or []))} | "
                            f"**{s.get('conclusion')}** |")
        return rows

    for mode, title in (("blind", "blind 추론 사슬"), ("aided", "aided 추론 사슬")):
        lines += [f"## {title}", "", "| level | 질문 | 관찰 | 판별자 | 배제 | 결론 |", "|---|---|---|---|---|---|"]
        lines += chain_rows(rec[mode]["analysis"]) or ["| — | | | | | |"]
        lines.append("")

    c = rec.get("cues") or {}
    lines += ["## 단서 대조", "",
              f"- 공통(unaided) {len(c.get('unaided', []))} · aided 에만 {len(c.get('aided_only', []))} · blind 에만 {len(c.get('blind_only', []))}", ""]
    if c.get("aided_only"):
        lines += ["### aided 에서만 나온 단서 (보조 지식 의존 가능성)"] + [
            f"- [{x.get('category')}/{x.get('level')}] {x.get('observation')}" for x in c["aided_only"]] + [""]
    if c.get("blind_only"):
        lines += ["### blind 에서만 나온 단서 (지도가 있으면 사라진 관찰)"] + [
            f"- [{x.get('category')}/{x.get('level')}] {x.get('observation')}" for x in c["blind_only"]] + [""]
    if c.get("unaided"):
        lines += ["### 공통 단서 (일반 추론)"] + [
            f"- [{x['blind'].get('category')}] {x['blind'].get('observation')} ⇄ {x['aided'].get('observation')} (sim {x['sim']})"
            for x in c["unaided"]] + [""]

    # 이 잡에서 나온 원자의 계층
    rf_b, rf_a = f"baseline_{rec['job_id']}_blind", f"baseline_{rec['job_id']}_aided"
    mine = {"unaided": [], "aided": [], "blind-only": []}
    for k, rows in (tiers.get("tiers") or {}).items():
        for r in rows:
            reps = set(r.get("reports") or [])
            if r.get("blind") in set(rec["blind"].get("atoms") or []) or r.get("aided") in set(rec["aided"].get("atoms") or []) \
                    or rf_b in reps or rf_a in reps:
                mine[k].append(r)
    lines += ["## 원자 계층 (샌드박스 적재)", "",
              "| 계층 | 의미 | 개수 |", "|---|---|---|",
              f"| unaided | 로드뷰만으로 추론되는 일반 사실 | {len(mine['unaided'])} |",
              f"| aided | 지도(보조 지식)가 있어야만 나온 사실 | {len(mine['aided'])} |",
              f"| blind-only | 지도 없이 볼 때만 나온 관찰 | {len(mine['blind-only'])} |", ""]
    for k, label in (("unaided", "unaided"), ("aided", "aided"), ("blind-only", "blind-only")):
        if mine[k]:
            lines += [f"### {label}"] + [
                f"- `{r.get('blind') or r.get('aided')}` [{r['layer']}/{r.get('category') or 'other'}/{r['scope']}] {r['title']}"
                for r in mine[k]] + [""]
    return "\n".join(lines)


def _readme(recs: list[dict], tiers: dict, settings: Settings) -> str:
    def avg(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(sum(xs) / len(xs), 2) if xs else None

    def med(xs):
        xs = sorted(x for x in xs if isinstance(x, (int, float)))
        return xs[len(xs) // 2] if xs else None

    rows_m = {m: [r[m]["metrics"] for r in recs] for m in ("blind", "aided")}
    rows_m["aided_prior"] = [r["aided_prior"]["metrics"] for r in recs]
    lvl_dist = {m: {} for m in rows_m}
    for m, ms in rows_m.items():
        for x in ms:
            lvl_dist[m][x["reached_level"] or "—"] = lvl_dist[m].get(x["reached_level"] or "—", 0) + 1
    verdicts: dict[str, int] = {}
    for r in recs:
        v = (r.get("narrative") or {}).get("verdict") or "—"
        verdicts[v] = verdicts.get(v, 0) + 1
    s = tiers.get("summary") or {}
    lines = [
        "# 평가 기준선 — blind(로드뷰만) vs aided(하단 지도 포함)",
        "",
        f"생성 {time.strftime('%Y-%m-%d %H:%M')} · 잡 {len(recs)}건 · 총비용 ${round(sum(r.get('cost_usd') or 0 for r in recs), 3)} · "
        f"기획: docs/plan/atom-dialogue_260906.html §8.1 (평가 기준선 오염)",
        "",
        "캡처 하단 지도는 zoom 15 + 정답 좌표 빨간 마커다. 같은 캡처를 (1-1) 로드뷰 패널만 잘라서, (1-2) 원본(지도 포함)으로 두 번 분석하고 "
        "추론 사슬·단서·원자를 대조했다. 두 모드의 원자는 각각 `store_blind/`, `store_aided/` 샌드박스에 적재됐고(본 저장소 무변경), "
        "계층은 `tiers.json` 에 있다.",
        "",
        "## 집계",
        "",
        "| 지표 | blind | aided | aided_prior(원 보고서) |",
        "|---|---|---|---|",
        f"| 국가 적중률 | {avg([1 if x['country_correct'] else 0 for x in rows_m['blind'] if x['country_correct'] is not None])} | "
        f"{avg([1 if x['country_correct'] else 0 for x in rows_m['aided'] if x['country_correct'] is not None])} | "
        f"{avg([1 if x['country_correct'] else 0 for x in rows_m['aided_prior'] if x['country_correct'] is not None])} |",
        f"| 좌표 오차 km 중앙값 | {med([x['error_km'] for x in rows_m['blind']])} | {med([x['error_km'] for x in rows_m['aided']])} | {med([x['error_km'] for x in rows_m['aided_prior']])} |",
        f"| 좌표 오차 km 평균 | {avg([x['error_km'] for x in rows_m['blind']])} | {avg([x['error_km'] for x in rows_m['aided']])} | {avg([x['error_km'] for x in rows_m['aided_prior']])} |",
        f"| 도달 level 분포 | {lvl_dist['blind']} | {lvl_dist['aided']} | {lvl_dist['aided_prior']} |",
        f"| ruled_out 있는 단계 평균 | {avg([x['n_steps_with_cut'] for x in rows_m['blind']])} | {avg([x['n_steps_with_cut'] for x in rows_m['aided']])} | {avg([x['n_steps_with_cut'] for x in rows_m['aided_prior']])} |",
        f"| 단서 수 평균 | {avg([x['n_cues'] for x in rows_m['blind']])} | {avg([x['n_cues'] for x in rows_m['aided']])} | {avg([x['n_cues'] for x in rows_m['aided_prior']])} |",
        "",
        f"감사 판정 분포(logic_delta.verdict): {verdicts}",
        "",
        "## 추론 계층 (원자)",
        "",
        "| 계층 | 정의 | 개수 |", "|---|---|---|",
        f"| **unaided** | blind 에서도 나온 사실 — 로드뷰만으로 추론되는 일반 추론 | {s.get('unaided', 0)} |",
        f"| **aided** | aided 에서만 나온 사실 — 보조 지식(지도)이 있어야만 추론 | {s.get('aided', 0)} |",
        f"| **blind-only** | blind 에서만 나온 관찰 — 지도가 있으면 사라지는 관찰 | {s.get('blind-only', 0)} |",
        "",
        "레이어별: " + json.dumps(tiers.get("by_layer") or {}, ensure_ascii=False),
        "",
        "## 잡별 문서",
        "",
    ]
    for r in recs:
        mb, ma = r["blind"]["metrics"], r["aided"]["metrics"]
        v = (r.get("narrative") or {}).get("verdict") or "—"
        lines.append(f"- [{r.get('label') or r['job_id']}]({r['job_id']}.md) — blind {mb['country']}/{mb['reached_level']}/{mb['error_km']}km · "
                     f"aided {ma['country']}/{ma['reached_level']}/{ma['error_km']}km · `{v}`")
    lines += ["", "## 재실행", "",
              "```", "uv run geoguesshelper baseline run            # image_panos 있는 잡 전부(이미 있는 결과는 건너뜀)",
              "uv run geoguesshelper baseline run --limit 2  # 소규모", "uv run geoguesshelper baseline report         # 문서·tiers 재생성(LLM 호출 없음)", "```", ""]
    return "\n".join(lines)


def report(settings: Settings, log=print) -> dict:
    base = settings.knowledge_dir / "baseline"
    recs = _load_runs(settings)
    if not recs:
        log("[baseline] 실행 결과가 없습니다 — 먼저 `baseline run`")
        return {"jobs": 0}
    tiers = _tag_tiers(settings, log=log)
    for r in recs:
        (base / f"{r['job_id']}.md").write_text(_job_md(r, tiers), encoding="utf-8")
    (base / "README.md").write_text(_readme(recs, tiers, settings), encoding="utf-8")
    log(f"[baseline] 문서 {len(recs)}건 + README.md + tiers.json → {base}")
    return {"jobs": len(recs), "tiers": tiers.get("summary")}
