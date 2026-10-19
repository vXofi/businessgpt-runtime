from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from telegram import Update
from telegram.ext import ExtBot


MESSAGE_UPDATE_TYPES = frozenset({"message", "edited_message"})
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateMetadata:
    update_id: int | None
    update_type: str
    chat_id: int | None
    message_id: int | None
    user_id: int | None
    sender_is_bot: bool | None
    message_date: str | None
    edit_date: str | None
    reply_to_message_id: int | None
    reply_to_user_id: int | None
    media_kind: str


@dataclass(frozen=True)
class RepeatedMessageObservation:
    first_update_id: int | None
    first_update_type: str
    previous_update_id: int | None
    previous_update_type: str
    elapsed_ms: int


@dataclass
class _SeenMessage:
    first_update_id: int | None
    first_update_type: str
    last_update_id: int | None
    last_update_type: str
    first_seen: float
    last_seen: float


def _iso_timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def update_type(update: Update) -> str:
    for name in (
        "message",
        "edited_message",
        "callback_query",
        "message_reaction",
    ):
        if getattr(update, name, None) is not None:
            return name
    return "other"


def _media_kind(message: Any) -> str:
    if message is None:
        return "none"
    if getattr(message, "sticker", None) is not None:
        return "sticker"
    if getattr(message, "video", None) is not None:
        return "video"
    if getattr(message, "animation", None) is not None:
        return "animation"
    if getattr(message, "photo", None):
        return "photo"
    document = getattr(message, "document", None)
    if document is not None:
        mime_type = str(getattr(document, "mime_type", "") or "")
        return "image_document" if mime_type.startswith("image/") else "document"
    if getattr(message, "text", None) is not None:
        return "text"
    if getattr(message, "caption", None) is not None:
        return "caption"
    return "other"


def describe_update(update: Update) -> UpdateMetadata:
    kind = update_type(update)
    message = getattr(update, "effective_message", None)
    chat = getattr(update, "effective_chat", None)
    user = getattr(update, "effective_user", None)

    message_id = getattr(message, "message_id", None)
    if kind == "message_reaction":
        reaction = getattr(update, "message_reaction", None)
        message_id = getattr(reaction, "message_id", message_id)

    sender = getattr(message, "from_user", None)
    reply = getattr(message, "reply_to_message", None)
    reply_user = getattr(reply, "from_user", None)
    return UpdateMetadata(
        update_id=getattr(update, "update_id", None),
        update_type=kind,
        chat_id=getattr(chat, "id", getattr(message, "chat_id", None)),
        message_id=message_id,
        user_id=getattr(user, "id", None),
        sender_is_bot=getattr(sender, "is_bot", None),
        message_date=_iso_timestamp(getattr(message, "date", None)),
        edit_date=_iso_timestamp(getattr(message, "edit_date", None)),
        reply_to_message_id=getattr(reply, "message_id", None),
        reply_to_user_id=getattr(reply_user, "id", None),
        media_kind=_media_kind(message),
    )


def trace_id(runtime_id: str, metadata: UpdateMetadata) -> str:
    return ":".join(
        (
            runtime_id,
            str(metadata.update_id) if metadata.update_id is not None else "no-update",
            str(metadata.chat_id) if metadata.chat_id is not None else "no-chat",
            str(metadata.message_id) if metadata.message_id is not None else "no-message",
        )
    )


def _update_summary(update: Update) -> str:
    try:
        metadata = describe_update(update)
        return ":".join(
            (
                str(metadata.update_id),
                metadata.update_type,
                str(metadata.chat_id),
                str(metadata.message_id),
            )
        )
    except Exception:
        return f"{getattr(update, 'update_id', None)}:trace-error:None:None"


class TracingExtBot(ExtBot):
    """Logs getUpdates batch boundaries without recording Telegram content."""

    __slots__ = ("_trace_runtime_id", "_trace_poll_sequence", "_readiness")

    def __init__(self, *args: Any, runtime_id: str, readiness=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_trace_runtime_id", runtime_id)
        object.__setattr__(self, "_trace_poll_sequence", 0)
        object.__setattr__(self, "_readiness", readiness)

    async def get_updates(
        self,
        offset: int | None = None,
        limit: int | None = None,
        timeout: int | timedelta | None = None,
        allowed_updates: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> tuple[Update, ...]:
        poll_id = self._trace_poll_sequence + 1
        object.__setattr__(self, "_trace_poll_sequence", poll_id)
        started = time.monotonic()
        try:
            updates = await super().get_updates(
                offset=offset,
                limit=limit,
                timeout=timeout,
                allowed_updates=allowed_updates,
                **kwargs,
            )
        except Exception as exc:
            if self._readiness is not None:
                self._readiness.poll(False)
            logger.warning(
                "telegram_poll_failed runtime_id=%s poll_id=%s requested_offset=%s "
                "error_type=%s elapsed_ms=%s",
                self._trace_runtime_id,
                poll_id,
                offset,
                type(exc).__name__,
                int((time.monotonic() - started) * 1000),
            )
            raise

        if self._readiness is not None:
            self._readiness.poll(True)
        if updates:
            try:
                logger.info(
                    "telegram_poll_batch runtime_id=%s poll_id=%s requested_offset=%s "
                    "returned_count=%s first_update_id=%s last_update_id=%s updates=%s "
                    "elapsed_ms=%s",
                    self._trace_runtime_id,
                    poll_id,
                    offset,
                    len(updates),
                    updates[0].update_id,
                    updates[-1].update_id,
                    ",".join(_update_summary(update) for update in updates),
                    int((time.monotonic() - started) * 1000),
                )
            except Exception as exc:
                logger.warning(
                    "telegram_poll_trace_failed runtime_id=%s poll_id=%s "
                    "requested_offset=%s error_type=%s",
                    self._trace_runtime_id,
                    poll_id,
                    offset,
                    type(exc).__name__,
                )
        return updates


class UpdateTraceTracker:
    """Observes repeated Telegram message identities without suppressing updates."""

    def __init__(self, *, max_messages: int = 4096, ttl_seconds: int = 24 * 60 * 60) -> None:
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds
        self._seen: OrderedDict[tuple[int, int], _SeenMessage] = OrderedDict()

    def observe(
        self,
        metadata: UpdateMetadata,
        *,
        now: float | None = None,
    ) -> RepeatedMessageObservation | None:
        if (
            metadata.update_type not in MESSAGE_UPDATE_TYPES
            or metadata.chat_id is None
            or metadata.message_id is None
        ):
            return None

        observed_at = time.monotonic() if now is None else now
        cutoff = observed_at - self.ttl_seconds
        while self._seen:
            _, oldest = next(iter(self._seen.items()))
            if oldest.last_seen > cutoff:
                break
            self._seen.popitem(last=False)

        key = (metadata.chat_id, metadata.message_id)
        previous = self._seen.get(key)
        if previous is None:
            self._seen[key] = _SeenMessage(
                first_update_id=metadata.update_id,
                first_update_type=metadata.update_type,
                last_update_id=metadata.update_id,
                last_update_type=metadata.update_type,
                first_seen=observed_at,
                last_seen=observed_at,
            )
            self._seen.move_to_end(key)
            while len(self._seen) > self.max_messages:
                self._seen.popitem(last=False)
            return None

        repeated = RepeatedMessageObservation(
            first_update_id=previous.first_update_id,
            first_update_type=previous.first_update_type,
            previous_update_id=previous.last_update_id,
            previous_update_type=previous.last_update_type,
            elapsed_ms=max(0, int((observed_at - previous.last_seen) * 1000)),
        )
        previous.last_update_id = metadata.update_id
        previous.last_update_type = metadata.update_type
        previous.last_seen = observed_at
        self._seen.move_to_end(key)
        return repeated
