from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class GenerationAdmission:
    allowed: bool
    reason: str | None = None


class GenerationLimiter:
    def __init__(
        self,
        *,
        max_concurrent: int,
        user_requests: int,
        chat_requests: int,
        window_seconds: int,
        max_keys: int = 512,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.user_requests = user_requests
        self.chat_requests = chat_requests
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._active = 0
        self._user_events: OrderedDict[int, deque[float]] = OrderedDict()
        self._chat_events: OrderedDict[int, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

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

    async def try_start(self, *, chat_id: int, user_id: int) -> GenerationAdmission:
        async with self._lock:
            now = time.monotonic()
            user_events = self._events_for_key(self._user_events, user_id, now)
            chat_events = self._events_for_key(self._chat_events, chat_id, now)
            if len(user_events) >= self.user_requests:
                return GenerationAdmission(False, "user rate limit reached")
            if len(chat_events) >= self.chat_requests:
                return GenerationAdmission(False, "chat rate limit reached")
            if self._active >= self.max_concurrent:
                return GenerationAdmission(False, "model is busy")
            user_events.append(now)
            chat_events.append(now)
            self._active += 1
            return GenerationAdmission(True)

    async def finish(self) -> None:
        async with self._lock:
            if self._active > 0:
                self._active -= 1
