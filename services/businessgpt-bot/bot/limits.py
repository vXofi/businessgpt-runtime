from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


logger = logging.getLogger(__name__)
GenerationJob = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class GenerationAdmission:
    allowed: bool
    reason: str | None = None
    queued: bool = False
    position: int | None = None


@dataclass(frozen=True)
class _QueuedGeneration:
    chat_id: int
    user_id: int
    run: GenerationJob


class GenerationQueue:
    """Bounded FIFO generation queue with enqueue-time rate limiting."""

    def __init__(
        self,
        *,
        max_concurrent: int,
        max_queue_size: int,
        user_requests: int,
        chat_requests: int,
        window_seconds: int,
        max_keys: int = 512,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.max_queue_size = max_queue_size
        self.user_requests = user_requests
        self.chat_requests = chat_requests
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._capacity = max_concurrent + max_queue_size
        self._queue: asyncio.Queue[_QueuedGeneration] = asyncio.Queue(
            maxsize=self._capacity
        )
        self._active = 0
        self._outstanding = 0
        self._accepting = False
        self._workers: list[asyncio.Task[None]] = []
        self._user_events: OrderedDict[int, deque[float]] = OrderedDict()
        self._chat_events: OrderedDict[int, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    @property
    def pending(self) -> int:
        return max(0, self._outstanding - self._active)

    def start(self) -> None:
        if self._workers:
            return
        self._accepting = True
        self._workers = [
            asyncio.create_task(
                self._worker(),
                name=f"businessgpt-generation-{index + 1}",
            )
            for index in range(self.max_concurrent)
        ]

    def _events_for_key(
        self,
        mapping: OrderedDict[int, deque[float]],
        key: int,
        now: float,
    ) -> deque[float]:
        events = mapping.setdefault(key, deque())
        threshold = now - self.window_seconds
        while events and events[0] <= threshold:
            events.popleft()
        mapping.move_to_end(key)
        while len(mapping) > self.max_keys:
            mapping.popitem(last=False)
        return events

    async def submit(
        self,
        *,
        chat_id: int,
        user_id: int,
        run: GenerationJob,
    ) -> GenerationAdmission:
        async with self._lock:
            if not self._accepting:
                return GenerationAdmission(False, "generation queue is not running")
            now = time.monotonic()
            user_events = self._events_for_key(self._user_events, user_id, now)
            chat_events = self._events_for_key(self._chat_events, chat_id, now)
            if len(user_events) >= self.user_requests:
                return GenerationAdmission(False, "user rate limit reached")
            if len(chat_events) >= self.chat_requests:
                return GenerationAdmission(False, "chat rate limit reached")
            if self._outstanding >= self._capacity:
                return GenerationAdmission(False, "generation queue is full")

            queued = self._outstanding >= self.max_concurrent
            position = (
                self._outstanding - self.max_concurrent + 1 if queued else None
            )
            user_events.append(now)
            chat_events.append(now)
            self._outstanding += 1
            self._queue.put_nowait(
                _QueuedGeneration(chat_id=chat_id, user_id=user_id, run=run)
            )
            return GenerationAdmission(
                True,
                queued=queued,
                position=position,
            )

    async def _worker(self) -> None:
        while True:
            queued = await self._queue.get()
            marked_active = False
            try:
                async with self._lock:
                    self._active += 1
                    marked_active = True
                await queued.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "queued generation failed chat_id=%s user_id=%s",
                    queued.chat_id,
                    queued.user_id,
                )
            finally:
                async with self._lock:
                    if marked_active:
                        self._active -= 1
                    self._outstanding = max(0, self._outstanding - 1)
                self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()

    async def shutdown(self) -> None:
        async with self._lock:
            self._accepting = False
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

        discarded = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                discarded += 1
                self._queue.task_done()
        if discarded:
            async with self._lock:
                self._outstanding = max(0, self._outstanding - discarded)
