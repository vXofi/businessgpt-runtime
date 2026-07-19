from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class CachedMessage:
    message_id: int
    role: str
    content: str
    name: str | None = None


class ContextCache:
    """Bounded, process-local chat context. Restarting the bot clears it."""

    def __init__(
        self,
        max_messages_per_chat: int = 20,
        *,
        max_chats: int = 64,
        ttl_seconds: int = 21600,
    ) -> None:
        self._max = max_messages_per_chat
        self._max_chats = max_chats
        self._ttl_seconds = ttl_seconds
        self._messages: OrderedDict[
            int, tuple[deque[CachedMessage], float]
        ] = OrderedDict()
        self._lock = RLock()

    def _prune(self, now: float) -> None:
        expired = [
            chat_id
            for chat_id, (_, touched_at) in self._messages.items()
            if touched_at <= now - self._ttl_seconds
        ]
        for chat_id in expired:
            self._messages.pop(chat_id, None)
        while len(self._messages) > self._max_chats:
            self._messages.popitem(last=False)

    def add(self, chat_id: int, message: CachedMessage) -> None:
        if not message.content.strip():
            return
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            queue, _ = self._messages.get(chat_id, (deque(maxlen=self._max), now))
            if any(item.message_id == message.message_id for item in queue):
                return
            queue.append(message)
            self._messages[chat_id] = (queue, now)
            self._messages.move_to_end(chat_id)
            self._prune(now)

    def recent(
        self,
        chat_id: int,
        limit: int,
        *,
        exclude_message_ids: set[int] | None = None,
    ) -> list[CachedMessage]:
        if limit <= 0:
            return []
        excluded = exclude_message_ids or set()
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            entry = self._messages.get(chat_id)
            if entry is None:
                return []
            queue, _ = entry
            self._messages[chat_id] = (queue, now)
            self._messages.move_to_end(chat_id)
            eligible = [
                message
                for message in queue
                if message.message_id not in excluded
            ]
        return eligible[-min(limit, self._max) :]
