"""작업 큐 — 보고서 생성을 서버에서 **순차적으로** 처리한다.

기존 구조의 문제: 동시성 제어가 프론트의 탭별 불리언(`t.busy`)뿐이라
  · 브라우저 새로고침/두 번째 창이면 가드가 통째로 우회되고,
  · 탭 N개를 누르면 Chromium N개 + Claude 호출 N개가 동시에 떠서
    기본 ThreadPoolExecutor(20슬롯)를 채우고 21번째부터는 영원히 대기했다.

여기서는 서버가 유일한 진실이다. 모든 보고서 작업은 큐에 쌓이고
worker(기본 1개)가 하나씩 꺼내 실행한다. 클라이언트는 job id 로 상태를
폴링하거나 SSE 로 진행 상황을 구독한다.

핵심 성질
  · 순차 실행    — concurrency 기본 1. 여러 탭에서 눌러도 한 번에 하나씩.
  · 멱등성       — client_key 가 같으면 기존 job 을 그대로 돌려준다(중복 과금 방지).
  · 취소 가능    — 대기 중이면 즉시, 실행 중이면 협조적 취소(단계 경계에서 확인).
  · 유실 없음    — 상태 전이를 jsonl 로 남긴다. 새로고침해도 job id 로 재접속 가능.
  · 진행 보고    — emit() 한 줄이 폴링/SSE 양쪽에 동시에 반영된다.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

# ── 상태 ─────────────────────────────────────────────────────────
QUEUED = "QUEUED"
RUNNING = "RUNNING"
DONE = "DONE"
FAILED = "FAILED"
CANCELED = "CANCELED"
TERMINAL = (DONE, FAILED, CANCELED)


class JobCanceled(Exception):
    """협조적 취소 — 핸들러가 단계 경계에서 raise 한다."""


class JobFailure(Exception):
    """작업은 끝까지 돌았지만 결과가 실패 — 결과 본문은 보존한 채 FAILED 로 표시한다.

    (예: 캡처 실패, 분석 실패. 예외로 날려버리면 왜 실패했는지 UI 가 보여줄 수 없다.)
    """

    def __init__(self, message: str, result: dict | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass
class Job:
    id: str
    kind: str
    payload: dict
    label: str = ""
    client_key: str = ""
    status: str = QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    stage: str = ""
    pct: int = 0
    progress: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    cancel_requested: bool = False
    # 비공개(직렬화 제외)
    _subs: list[asyncio.Queue] = field(default_factory=list, repr=False)
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    # ── 진행 보고 ────────────────────────────────────────────────
    def emit(self, stage: str, message: str = "", pct: int | None = None) -> None:
        """단계 전환을 기록하고 구독자에게 밀어준다. 핸들러가 호출."""
        self.stage = stage
        if pct is not None:
            self.pct = max(0, min(100, int(pct)))
        entry = {"t": time.time(), "stage": stage, "message": message, "pct": self.pct}
        self.progress.append(entry)
        if len(self.progress) > 200:            # 폭주 방지
            del self.progress[:-200]
        self._push({"type": "progress", "job": self.public(), "entry": entry})

    def raise_if_canceled(self) -> None:
        if self.cancel_requested:
            raise JobCanceled(self.id)

    def _push(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:           # 느린 구독자는 버린다
                pass

    # ── 직렬화 ───────────────────────────────────────────────────
    def public(self, *, with_result: bool = False) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "stage": self.stage,
            "pct": self.pct,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "elapsed": round((self.finished_at or time.time()) - (self.started_at or self.created_at), 1),
            "error": self.error,
            "canceling": self.cancel_requested and self.status == RUNNING,
            "clientKey": self.client_key,
            "tabId": self.payload.get("tabId"),
        }
        if with_result:
            d["result"] = self.result
            d["progress"] = self.progress[-40:]
        return d


JobHandler = Callable[[Job], Awaitable[dict]]


class JobQueue:
    """단일(기본) worker 로 job 을 순차 처리하는 인메모리 큐 + jsonl 로그."""

    def __init__(
        self,
        *,
        concurrency: int = 1,
        max_pending: int = 64,
        history: int = 200,
        log_path: Path | None = None,
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self.max_pending = max(1, int(max_pending))
        self.history = max(20, int(history))
        self.log_path = log_path
        self._handlers: dict[str, JobHandler] = {}
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []             # 생성 순서(최신이 뒤)
        self._pending: asyncio.Queue[str] | None = None
        self._workers: list[asyncio.Task] = []
        self._running: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._started = False

    # ── 수명주기 ─────────────────────────────────────────────────
    def register(self, kind: str, handler: JobHandler) -> None:
        self._handlers[kind] = handler

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._pending = asyncio.Queue()
        for i in range(self.concurrency):
            self._workers.append(asyncio.create_task(self._worker(i), name=f"jobworker-{i}"))

    async def shutdown(self) -> None:
        for t in self._workers:
            t.cancel()
        for t in list(self._running.values()):
            t.cancel()
        self._workers.clear()
        self._started = False

    # ── 제출 ─────────────────────────────────────────────────────
    async def submit(
        self, kind: str, payload: dict, *, label: str = "", client_key: str = ""
    ) -> Job:
        """job 을 큐에 넣고 반환. client_key 가 이미 있으면 그 job 을 그대로 돌려준다."""
        if kind not in self._handlers:
            raise ValueError(f"알 수 없는 작업 종류: {kind}")
        await self.start()
        async with self._lock:
            if client_key:
                for jid in reversed(self._order):
                    j = self._jobs.get(jid)
                    # 종료된 지 오래된 건 재사용하지 않는다(같은 키로 재요청 = 새 작업)
                    if j and j.client_key == client_key and j.status not in TERMINAL:
                        return j
            pending = sum(1 for j in self._jobs.values() if j.status == QUEUED)
            if pending >= self.max_pending:
                raise RuntimeError(f"대기열이 가득 찼습니다({pending}/{self.max_pending}). 잠시 후 다시 시도하세요.")
            job = Job(
                id="job_" + uuid.uuid4().hex[:12],
                kind=kind,
                payload=payload,
                label=label or kind,
                client_key=client_key,
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._trim()
        assert self._pending is not None
        self._pending.put_nowait(job.id)
        self._log(job, "queued")
        self._broadcast_queue()
        return job

    def _trim(self) -> None:
        """종료된 오래된 job 만 정리(대기/실행 중은 절대 버리지 않는다)."""
        if len(self._order) <= self.history:
            return
        keep: list[str] = []
        drop = len(self._order) - self.history
        for jid in self._order:
            j = self._jobs.get(jid)
            if drop > 0 and j is not None and j.status in TERMINAL and not j._subs:
                self._jobs.pop(jid, None)
                drop -= 1
                continue
            keep.append(jid)
        self._order = keep

    # ── 조회 / 취소 ──────────────────────────────────────────────
    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, *, limit: int = 50) -> list[dict]:
        out = [self._jobs[j].public() for j in reversed(self._order) if j in self._jobs]
        return out[:limit]

    def stats(self) -> dict:
        vals = list(self._jobs.values())
        return {
            "queued": sum(1 for j in vals if j.status == QUEUED),
            "running": sum(1 for j in vals if j.status == RUNNING),
            "done": sum(1 for j in vals if j.status == DONE),
            "failed": sum(1 for j in vals if j.status == FAILED),
            "canceled": sum(1 for j in vals if j.status == CANCELED),
            "concurrency": self.concurrency,
            "maxPending": self.max_pending,
        }

    def position(self, job_id: str) -> int:
        """대기열에서 앞에 몇 개 남았는지(실행 중이면 0)."""
        job = self._jobs.get(job_id)
        if not job or job.status != QUEUED:
            return 0
        n = 0
        for jid in self._order:
            if jid == job_id:
                break
            j = self._jobs.get(jid)
            if j and j.status == QUEUED:
                n += 1
        return n

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status in TERMINAL:
            return False
        job.cancel_requested = True
        if job.status == QUEUED:
            # 아직 안 꺼냈으면 즉시 종료 처리(worker 가 꺼내도 건너뛴다)
            self._finish(job, CANCELED, error="사용자가 취소했습니다.")
        else:
            job.emit("canceling", "취소 요청됨 — 현재 단계가 끝나면 중단합니다.")
        self._broadcast_queue()
        return True

    # ── 구독(SSE) ────────────────────────────────────────────────
    def subscribe(self, job_id: str) -> asyncio.Queue | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        job._subs.append(q)
        q.put_nowait({"type": "snapshot", "job": job.public(with_result=True)})
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        job = self._jobs.get(job_id)
        if job and q in job._subs:
            job._subs.remove(q)

    def _broadcast_queue(self) -> None:
        """큐 상태가 바뀌면 대기 중인 job 들의 순번을 갱신해 알린다."""
        for jid in self._order:
            j = self._jobs.get(jid)
            if j and j.status == QUEUED and j._subs:
                j._push({"type": "queue", "job": j.public(), "position": self.position(jid)})

    # ── worker ───────────────────────────────────────────────────
    async def _worker(self, idx: int) -> None:
        assert self._pending is not None
        while True:
            job_id = await self._pending.get()
            job = self._jobs.get(job_id)
            if job is None or job.status in TERMINAL:
                self._pending.task_done()
                continue
            if job.cancel_requested:
                self._finish(job, CANCELED, error="사용자가 취소했습니다.")
                self._pending.task_done()
                continue

            job.status = RUNNING
            job.started_at = time.time()
            job.emit("start", "작업 시작", 1)
            self._log(job, "start")
            self._broadcast_queue()

            handler = self._handlers[job.kind]
            task = asyncio.create_task(handler(job), name=f"job-{job.id}")
            self._running[job.id] = task
            try:
                result = await task
                if job.cancel_requested:
                    self._finish(job, CANCELED, error="사용자가 취소했습니다.")
                else:
                    self._finish(job, DONE, result=result)
            except JobFailure as exc:
                self._finish(job, FAILED, result=exc.result, error=str(exc))
            except JobCanceled:
                self._finish(job, CANCELED, error="사용자가 취소했습니다.")
            except asyncio.CancelledError:
                self._finish(job, CANCELED, error="작업이 중단되었습니다.")
            except Exception as exc:  # noqa: BLE001 — 어떤 실패도 큐를 죽이면 안 된다
                # 취소 요청 뒤에 터진 예외는 실패가 아니라 취소의 결과다.
                # (예: 스트리밍 중이던 LLM 호출을 끊으면 SDK 가 예외를 던진다)
                if job.cancel_requested:
                    self._finish(job, CANCELED, error="사용자가 취소했습니다.")
                else:
                    self._finish(job, FAILED, error=f"{type(exc).__name__}: {exc}")
            finally:
                self._running.pop(job.id, None)
                self._pending.task_done()
                self._broadcast_queue()

    def _finish(self, job: Job, status: str, *, result: dict | None = None, error: str | None = None) -> None:
        job.status = status
        job.result = result
        job.error = error
        job.finished_at = time.time()
        job.pct = 100 if status == DONE else job.pct
        job.stage = status.lower()
        job._push({"type": "done", "job": job.public(with_result=True)})
        job._done.set()
        self._log(job, status.lower())

    async def wait(self, job_id: str, timeout: float | None = None) -> Job | None:
        """작업이 끝날 때까지 기다린다(구 동기 API 호환용)."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status in TERMINAL:
            return job
        try:
            await asyncio.wait_for(job._done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return job
        return job

    # ── 로그 ─────────────────────────────────────────────────────
    def _log(self, job: Job, event: str) -> None:
        if not self.log_path:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            rec = job.public(with_result=(job.status in TERMINAL))
            rec["event"] = event
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:  # noqa: BLE001 — 로깅 실패가 작업을 막으면 안 된다
            pass


async def run_blocking(job: Job, fn: Callable[..., Any], *args: Any) -> Any:
    """블로킹 호출을 스레드로 넘기되, 그 전에 취소 여부를 확인한다."""
    job.raise_if_canceled()
    return await asyncio.to_thread(fn, *args)
