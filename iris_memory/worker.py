"""Serial background worker for derived memory maintenance off the reply hot path."""

from __future__ import annotations

import logging
import sqlite3
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition, Event, Thread
from time import monotonic
from typing import Literal

from iris_chat_engines.messages import ChatError
from iris_memory.context import MemoryContext
from iris_memory.controls import MemoryPolicyError
from iris_memory.embeddings import EmbeddingError
from iris_turn import CURRENT_STOP, TurnExpired, turn_budget

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Job:
    kind: Literal["hierarchy", "summary", "embeddings"]
    owner: str
    conversation: str = ""
    exchange_id: str = ""
    user_text: str = ""
    selected_topic: str = ""
    expected_epoch: int = 0
    force: bool = False


class MemoryMaintenanceWorker:
    """Drain derived memory jobs serially without blocking spoken replies.

    Every write checks owner policy epoch inside SQLite before committing, so
    concurrent private-mode toggles or forgetting safely discard stale work.
    """

    def __init__(self, memory_context: MemoryContext) -> None:
        self._memory_context = memory_context
        self._condition = Condition()
        self._stopping = Event()
        self._jobs: deque[_Job] = deque()
        self._busy = False
        self._foreground_active = 0
        self._closed = False
        self._thread = Thread(target=self._run, name="iris-memory-maintenance", daemon=True)
        self._thread.start()

    @contextmanager
    def foreground_turn(self) -> Iterator[None]:
        """Yield background maintenance while a foreground turn is active."""
        with self._condition:
            self._foreground_active += 1
        try:
            yield
        finally:
            with self._condition:
                self._foreground_active -= 1
                self._condition.notify_all()

    def enqueue_hierarchy(
        self,
        owner: str,
        conversation: str,
        exchange_id: str,
        user_text: str,
        selected_topic: str,
        expected_epoch: int,
    ) -> None:
        if self._memory_context.hierarchy is None:
            return
        self._enqueue(
            _Job(
                kind="hierarchy",
                owner=owner,
                conversation=conversation,
                exchange_id=exchange_id,
                user_text=user_text,
                selected_topic=selected_topic,
                expected_epoch=expected_epoch,
            )
        )

    def enqueue_summary(self, owner: str, conversation: str, *, force: bool = False) -> None:
        self._enqueue(_Job(kind="summary", owner=owner, conversation=conversation, force=force))

    def enqueue_embeddings(self, owner: str) -> None:
        if self._memory_context.index.semantic is None:
            return
        self._enqueue(_Job(kind="embeddings", owner=owner))

    def _enqueue(self, job: _Job) -> None:
        with self._condition:
            if self._stopping.is_set() or self._closed:
                return
            # Coalesce duplicate summary/embedding requests to keep the queue bounded.
            if job.kind in {"summary", "embeddings"}:
                for existing in self._jobs:
                    if (
                        existing.kind == job.kind
                        and existing.owner == job.owner
                        and existing.conversation == job.conversation
                        and (not job.force or existing.force)
                    ):
                        return
            if len(self._jobs) >= 128:
                # Never drop source data; summaries/embeddings can always catch up later.
                if job.kind != "hierarchy":
                    return
                self._jobs.popleft()
            self._jobs.append(job)
            self._condition.notify()

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait for currently queued maintenance jobs to finish (used by tests/shutdown)."""
        deadline = monotonic() + timeout
        with self._condition:
            while (self._jobs or self._busy) and not self._closed:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return not self._jobs and not self._busy

    def close(self, timeout: float = 5.0) -> None:
        with self._condition:
            self._stopping.set()
            self._jobs.clear()
            self._condition.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _run(self) -> None:
        token = CURRENT_STOP.set(self._stopping)
        try:
            while True:
                with self._condition:
                    while (
                        not self._jobs or self._foreground_active > 0
                    ) and not self._stopping.is_set():
                        self._condition.wait()
                    if self._stopping.is_set():
                        return
                    job = self._jobs.popleft()
                    self._busy = True
                try:
                    with turn_budget(self._memory_context.settings.turn_timeout):
                        self._execute(job)
                except (
                    TurnExpired,
                    MemoryPolicyError,
                    ChatError,
                    EmbeddingError,
                    sqlite3.Error,
                    ValueError,
                ) as error:
                    LOGGER.warning(
                        "Background memory %s deferred (%s).", job.kind, type(error).__name__
                    )
                except Exception as error:
                    LOGGER.warning(
                        "Background memory %s failed (%s).", job.kind, type(error).__name__
                    )
                finally:
                    with self._condition:
                        self._busy = False
                        self._condition.notify_all()
        finally:
            CURRENT_STOP.reset(token)

    def _execute(self, job: _Job) -> None:
        if job.kind == "hierarchy":
            hierarchy = self._memory_context.hierarchy
            if hierarchy is None:
                return
            prepared = hierarchy.prepare_for_exchange(
                job.owner,
                job.conversation,
                job.exchange_id,
                job.user_text,
                job.selected_topic,
                expected_epoch=job.expected_epoch,
            )
            if prepared is not None:
                hierarchy.commit_for_exchange(job.owner, job.exchange_id, prepared)
        elif job.kind == "summary":
            ran = False
            if job.conversation:
                ran = self._memory_context.checkpoint(job.owner, job.conversation, force=job.force)
            if not ran and not self._stopping.is_set():
                backlog = self._memory_context.index.next_conversation(job.owner, job.conversation)
                if backlog is not None:
                    self._memory_context.checkpoint(job.owner, backlog, force=True)
        elif job.kind == "embeddings":
            semantic = self._memory_context.index.semantic
            if semantic is None:
                return
            # Index bounded chunks per wakeup so shutdown stays responsive.
            for _ in range(4):
                if self._stopping.is_set() or semantic.index_batch(job.owner) == 0:
                    break
