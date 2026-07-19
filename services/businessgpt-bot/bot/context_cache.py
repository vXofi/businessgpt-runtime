from __future__ import annotations

from collections import defaultdict, deque
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

    def __init__(self, max_messages_per_chat: int = 20) -> None:
        self._max = max_messages_per_chat
        self._messages: dict[int, deque[CachedMessage]] = defaultdict(
            lambda: deque(maxlen=self._max)
        )
        self._lock = RLock()

    def add(self, chat_id: int, message: CachedMessage) -> None:
        if not message.content.strip():
            return
        with self._lock:
            queue = self._messages[chat_id]
            if any(item.message_id == message.message_id for item in queue):
                return
            queue.append(message)

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
            eligible = [
                message
                for message in self._messages.get(chat_id, ())
                if message.message_id not in excluded
            ]
        return eligible[-min(limit, self._max) :]
