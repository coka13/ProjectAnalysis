"""Lightweight background job runner (thread pool based, no external broker)."""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

log = logging.getLogger("aai.jobs")


@dataclass
class JobHandle:
    job_id: str
    kind: str
    future: Future
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def done(self) -> bool:
        return self.future.done()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.future.cancel()


class JobManager:
    """Runs analysis jobs off the request thread."""

    def __init__(self, max_workers: int | None = None) -> None:
        self._max_workers = max_workers or settings.worker_threads
        self._executor: ThreadPoolExecutor | None = None
        self._jobs: dict[str, JobHandle] = {}
        self._lock = threading.RLock()

    def _pool(self) -> ThreadPoolExecutor:
        # Created lazily and recreated after a shutdown so the manager can be
        # reused across application lifespans (notably in tests).
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="aai-job",
                )
            return self._executor

    def submit(self, job_id: str, kind: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> JobHandle:
        cancel_event = threading.Event()

        def _runner() -> Any:
            try:
                return fn(*args, cancel_event=cancel_event, **kwargs)
            except Exception:  # pragma: no cover - defensive logging
                log.error("job %s (%s) failed:\n%s", job_id, kind, traceback.format_exc())
                raise

        future = self._pool().submit(_runner)
        handle = JobHandle(job_id=job_id, kind=kind, future=future, cancel_event=cancel_event)
        with self._lock:
            self._jobs[job_id] = handle
            self._prune()
        return handle

    def get(self, job_id: str) -> JobHandle | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        handle = self.get(job_id)
        if not handle:
            return False
        handle.cancel()
        return True

    def _prune(self) -> None:
        finished = [jid for jid, h in self._jobs.items() if h.done]
        if len(finished) > 100:
            for jid in finished[:-50]:
                self._jobs.pop(jid, None)

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


job_manager = JobManager()
