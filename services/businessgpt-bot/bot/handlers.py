from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import shlex
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Message,
    Update,
    User,
)
from telegram.constants import ChatAction, ChatType
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .businessgpt_client import BusinessGPTClient
from .config import Config
from .context_cache import CachedMessage, ContextCache
from .crypto import FeedbackEncryptor
from .db import BotDB, validate_name
from .image_generation_client import ImageGenerationClient, ImageGenerationError
from .limits import GenerationQueue
from .media_cache import EncryptedMediaCache
from .runpod_client import validate_prompt
from .tools import TOOL_REGISTRY
from .update_trace import UpdateTraceTracker, describe_update, trace_id
from .validation import (
    is_runpod_url,
    validate_allowed_api_url,
    validate_generation_settings,
    validate_image_generation_settings,
)


MAX_TELEGRAM_TEXT = 3900
MAX_IMAGE_GENERATION_PROMPT_CHARS = 3900
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
SETTING_OPTIONS: dict[str, tuple[int | float, ...]] = {
    "recent_context_messages": (0, 5, 10, 20),
    "max_tokens": (64, 128, 256, 512),
    "temperature": (0.2, 0.5, 0.7, 0.9, 1.1, 1.3),
    "top_p": (0.7, 0.8, 0.9, 0.95, 1.0),
    "top_k": (0, 20, 40, 50, 80, 100),
    "repetition_penalty": (1.0, 1.05, 1.1, 1.2, 1.3),
    "rate_limit_user_requests": (1, 3, 5, 10, 20),
    "rate_limit_chat_requests": (5, 10, 20, 30, 50),
    "image_generation_enabled": (0, 1),
    "image_width": (256, 320, 384, 448, 512),
    "image_height": (256, 320, 384, 448, 512),
    "image_steps": tuple(range(2, 17)),
    "image_guidance_scale": (
        0.0,
        0.5,
        1.0,
        1.5,
        2.0,
        2.5,
        3.0,
        3.5,
        4.0,
        4.5,
        5.0,
    ),
    "image_auto_translate": (0, 1),
}
ADMISSION_SETTING_FIELDS = frozenset(
    {"rate_limit_user_requests", "rate_limit_chat_requests"}
)
IMAGE_SETTING_FIELDS = frozenset(
    {
        "image_generation_enabled",
        "image_width",
        "image_height",
        "image_steps",
        "image_guidance_scale",
        "image_auto_translate",
    }
)
SETTING_LABELS = {
    "recent_context_messages": "Context",
    "max_tokens": "Max tokens",
    "temperature": "Temperature",
    "top_p": "Top P",
    "top_k": "Top K",
    "repetition_penalty": "Repeat penalty",
    "rate_limit_user_requests": "Requests per user",
    "rate_limit_chat_requests": "Requests per chat",
    "image_generation_enabled": "Image generation",
    "image_width": "Image width",
    "image_height": "Image height",
    "image_steps": "Image steps",
    "image_guidance_scale": "Image guidance",
    "image_auto_translate": "Automatic translation",
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadedImage:
    data: bytes
    mime_type: str
    filename: str | None
    media_metadata: dict[str, Any]


def _services(
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[
    Config,
    BotDB,
    BusinessGPTClient,
    FeedbackEncryptor,
    ContextCache,
    EncryptedMediaCache,
]:
    data = context.application.bot_data
    return (
        data["config"],
        data["db"],
        data["businessgpt"],
        data["feedback_encryptor"],
        data["context_cache"],
        data["media_cache"],
    )


def _trace_authorized_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    try:
        metadata = describe_update(update)
        tracker: UpdateTraceTracker | None = context.application.bot_data.get(
            "update_trace_tracker"
        )
        repeated = tracker.observe(metadata) if tracker else None
        log = logger.warning if repeated else logger.info
        log(
            "telegram_update_dispatched runtime_id=%s update_id=%s update_type=%s "
            "chat_id=%s message_id=%s user_id=%s sender_is_bot=%s "
            "message_date=%s edit_date=%s reply_to_message_id=%s "
            "reply_to_user_id=%s media_kind=%s repeated_message=%s "
            "first_update_id=%s first_update_type=%s previous_update_id=%s "
            "previous_update_type=%s repeat_elapsed_ms=%s",
            context.application.bot_data.get("runtime_id", "unknown"),
            metadata.update_id,
            metadata.update_type,
            metadata.chat_id,
            metadata.message_id,
            metadata.user_id,
            metadata.sender_is_bot,
            metadata.message_date,
            metadata.edit_date,
            metadata.reply_to_message_id,
            metadata.reply_to_user_id,
            metadata.media_kind,
            repeated is not None,
            repeated.first_update_id if repeated else None,
            repeated.first_update_type if repeated else None,
            repeated.previous_update_id if repeated else None,
            repeated.previous_update_type if repeated else None,
            repeated.elapsed_ms if repeated else None,
        )
    except Exception as exc:
        logger.warning(
            "telegram_dispatch_trace_failed runtime_id=%s update_id=%s "
            "error_type=%s",
            context.application.bot_data.get("runtime_id", "unknown"),
            getattr(update, "update_id", None),
            type(exc).__name__,
        )


def _user_label(user: Optional[User]) -> str:
    if not user:
        return "unknown"
    return user.username or user.full_name or str(user.id)


def _message_content(message: Message | None) -> str:
    if not message:
        return ""
    return (message.text or message.caption or "").strip()


def _disable_telegram_mentions(text: str) -> str:
    return text.replace("@", "")


def _to_dict_list(values: Any) -> Any:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    return [value.to_dict() if hasattr(value, "to_dict") else str(value) for value in values]


async def _reply(message: Message, text: str) -> None:
    chunks = [text[i : i + MAX_TELEGRAM_TEXT] for i in range(0, len(text), MAX_TELEGRAM_TEXT)]
    for chunk in chunks or [""]:
        await message.reply_text(chunk)


def _is_admin(config: Config, user: Optional[User]) -> bool:
    return bool(user and user.id in config.owner_user_ids)


def _is_owner(config: Config, user: Optional[User]) -> bool:
    return _is_admin(config, user)


async def authorize_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop untrusted updates before they reach cache, command, or reaction handlers."""
    config, db, *_ = _services(context)
    chat = update.effective_chat
    user = update.effective_user
    if chat and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        if chat.id in config.allowed_chat_ids:
            _trace_authorized_update(update, context)
            return
        raise ApplicationHandlerStop
    if chat and chat.type == ChatType.PRIVATE and user:
        if _is_owner(config, user):
            _trace_authorized_update(update, context)
            return
        if update.callback_query is not None:
            _trace_authorized_update(update, context)
            return
        message = update.effective_message
        text = _message_content(message)
        if text.startswith("/start settings_"):
            try:
                target = _decode_chat_id(text.split("settings_", 1)[1].split()[0])
            except (IndexError, ValueError):
                target = None
            if target in config.allowed_chat_ids:
                _trace_authorized_update(update, context)
                return
        session_chat_id = db.get_admin_session_chat(user.id)
        if session_chat_id in config.allowed_chat_ids:
            _trace_authorized_update(update, context)
            return
    raise ApplicationHandlerStop


def _member_status_is_admin(status: str) -> bool:
    return status in {"administrator", "creator"}


async def _is_authorized_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    target_chat_id: int | None = None,
) -> bool:
    config, db, *_ = _services(context)
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return False
    if target_chat_id is None:
        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            target_chat_id = message.chat_id
        elif message.chat.type == ChatType.PRIVATE:
            target_chat_id = db.get_admin_session_chat(user.id)
            if (
                target_chat_id is None
                and _is_owner(config, user)
                and len(config.allowed_chat_ids) == 1
            ):
                target_chat_id = next(iter(config.allowed_chat_ids))
    if target_chat_id not in config.allowed_chat_ids:
        return False
    if _is_owner(config, user):
        return True
    try:
        member = await context.bot.get_chat_member(target_chat_id, user.id)
    except TelegramError:
        return False
    return _member_status_is_admin(member.status)


async def _require_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    target_chat_id: int | None = None,
) -> bool:
    if await _is_authorized_admin(
        update,
        context,
        target_chat_id=target_chat_id,
    ):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("not allowed")
    return False


async def _require_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    config, *_ = _services(context)
    if _is_owner(config, update.effective_user):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("owner access required")
    return False


def _target_chat_id(
    message: Message,
    user: User | None,
    db: BotDB,
    config: Config | None = None,
) -> int:
    if message.chat.type == ChatType.PRIVATE and user:
        session = db.get_admin_session_chat(user.id)
        if session is not None:
            return session
        if config and _is_owner(config, user) and len(config.allowed_chat_ids) == 1:
            return next(iter(config.allowed_chat_ids))
    return message.chat_id


async def _bot_identity(context: ContextTypes.DEFAULT_TYPE) -> tuple[int, str | None]:
    bot_id = context.application.bot_data.get("bot_id")
    username = context.application.bot_data.get("bot_username")
    if bot_id:
        return int(bot_id), username
    me = await context.bot.get_me()
    context.application.bot_data.update(bot_id=me.id, bot_username=me.username)
    return me.id, me.username


async def _is_reply_to_bot(message: Message, context: ContextTypes.DEFAULT_TYPE) -> bool:
    replied_user = message.reply_to_message.from_user if message.reply_to_message else None
    if not replied_user:
        return False
    bot_id, _ = await _bot_identity(context)
    return replied_user.id == bot_id


async def _is_bot_mentioned(message: Message, context: ContextTypes.DEFAULT_TYPE) -> bool:
    _, username = await _bot_identity(context)
    content = _message_content(message)
    return bool(username and f"@{username.lower()}" in content.lower())


async def _strip_bot_mention(text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    _, username = await _bot_identity(context)
    if not username:
        return text.strip()
    return re.sub(rf"@{re.escape(username)}\b", "", text, flags=re.I).strip()


def _has_image(message: Message | None) -> bool:
    if not message:
        return False
    if message.photo:
        return True
    if message.document and message.document.mime_type in ALLOWED_IMAGE_MIME_TYPES:
        return True
    return any(
        getattr(message, field, None) is not None
        for field in ("sticker", "video", "animation")
    )


def _image_source(message: Message) -> Message | None:
    if _has_image(message):
        return message
    if _has_image(message.reply_to_message):
        return message.reply_to_message
    return None


async def _media_message_is_trigger(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    return bool(await _media_message_trigger_reasons(message, context))


async def _media_message_trigger_reasons(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[str, ...]:
    content = _message_content(message)
    reasons: list[str] = []
    if content.lower().startswith("/gpt"):
        reasons.append("gpt_caption")
    if await _is_reply_to_bot(message, context):
        reasons.append("reply_to_bot")
    if await _is_bot_mentioned(message, context):
        reasons.append("bot_mention")
    return tuple(reasons)


async def _text_message_is_trigger(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    return bool(await _text_message_trigger_reasons(message, context))


async def _text_message_trigger_reasons(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if _has_image(message.reply_to_message):
        reasons.append("reply_to_media")
    if await _is_reply_to_bot(message, context):
        reasons.append("reply_to_bot")
    if await _is_bot_mentioned(message, context):
        reasons.append("bot_mention")
    return tuple(reasons)


async def _download_image(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
) -> DownloadedImage:
    config, *_ = _services(context)
    sticker = getattr(message, "sticker", None)
    video = getattr(message, "video", None)
    animation = getattr(message, "animation", None)
    sticker_format: str | None = None
    sticker_representation: str | None = None
    if message.photo:
        media = message.photo[-1]
        mime_type = "image/jpeg"
        filename = f"telegram-{media.file_unique_id}.jpg"
        media_kind = "telegram_photo"
    elif message.document and message.document.mime_type in ALLOWED_IMAGE_MIME_TYPES:
        media = message.document
        mime_type = message.document.mime_type
        filename = message.document.file_name
        media_kind = "telegram_document"
    elif sticker is not None:
        media_kind = "telegram_sticker"
        sticker_format = (
            "video"
            if sticker.is_video
            else "animated"
            if sticker.is_animated
            else "static"
        )
        if sticker_format == "static":
            media = sticker
            mime_type = "image/webp"
            filename = f"telegram-sticker-{sticker.file_unique_id}.webp"
            sticker_representation = "original"
        else:
            media = sticker.thumbnail
            if media is None:
                raise ValueError(f"{sticker_format} sticker preview is unavailable")
            mime_type = ""
            filename = None
            sticker_representation = "thumbnail"
    elif video is not None or animation is not None:
        source = video if video is not None else animation
        source_kind = "video" if video is not None else "animation"
        media = source.thumbnail
        if media is None:
            raise ValueError(f"{source_kind} preview is unavailable")
        mime_type = ""
        filename = None
        media_kind = f"telegram_{source_kind}_preview"
    else:
        raise ValueError(
            "supported media types: JPEG, PNG, WebP, sticker, video, animation"
        )
    if media.file_size and media.file_size > config.image_max_bytes:
        raise ValueError("media exceeds the configured size limit")
    telegram_file = await context.bot.get_file(media.file_id)
    data = bytes(await telegram_file.download_as_bytearray())
    if len(data) > config.image_max_bytes:
        raise ValueError("media exceeds the configured size limit")
    if sticker is not None or video is not None or animation is not None:
        detected_mime_type = _detect_preview_image_mime(data)
    if sticker is not None:
        if sticker_format == "static" and detected_mime_type != "image/webp":
            raise ValueError("static sticker file is not WebP")
        if sticker_representation == "thumbnail":
            mime_type = detected_mime_type
            extension = "jpg" if mime_type == "image/jpeg" else "webp"
            filename = f"telegram-sticker-{sticker.file_unique_id}-preview.{extension}"
    elif video is not None or animation is not None:
        source = video if video is not None else animation
        source_kind = "video" if video is not None else "animation"
        mime_type = detected_mime_type
        extension = "jpg" if mime_type == "image/jpeg" else "webp"
        filename = f"telegram-{source_kind}-{source.file_unique_id}-preview.{extension}"
    media_metadata: dict[str, Any] = {
        "kind": media_kind,
        "mime_type": mime_type,
        "filename": filename,
    }
    if sticker is not None:
        media_metadata["sticker"] = {
            "emoji": sticker.emoji,
            "set_name": sticker.set_name,
            "type": str(getattr(sticker.type, "value", sticker.type)),
            "format": sticker_format,
            "representation": sticker_representation,
        }
    elif video is not None or animation is not None:
        source = video if video is not None else animation
        source_kind = "video" if video is not None else "animation"
        media_metadata[source_kind] = {
            "duration": source.duration,
            "width": source.width,
            "height": source.height,
            "representation": "telegram_thumbnail",
        }
    return DownloadedImage(
        data=data,
        mime_type=mime_type,
        filename=filename,
        media_metadata=media_metadata,
    )


def _detect_preview_image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("media preview must be JPEG or WebP")


async def remember_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.from_user or message.from_user.is_bot:
        return
    content = _message_content(message)
    if not content or content.startswith("/"):
        return
    *_, cache, _ = _services(context)
    cache.add(
        message.chat_id,
        CachedMessage(
            message_id=message.message_id,
            role="user",
            content=content,
            name=_user_label(message.from_user),
        ),
    )


async def _build_messages(
    *,
    trigger_message: Message,
    user_text: str,
    image: DownloadedImage | None,
    context_depth: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> list[dict[str, Any]]:
    *_, cache, _ = _services(context)
    excluded = {trigger_message.message_id}
    if trigger_message.reply_to_message:
        excluded.add(trigger_message.reply_to_message.message_id)
    messages: list[dict[str, Any]] = []
    for cached in cache.recent(
        trigger_message.chat_id,
        context_depth,
        exclude_message_ids=excluded,
    ):
        item: dict[str, Any] = {"role": cached.role, "content": cached.content}
        if cached.name:
            item["name"] = cached.name
        messages.append(item)

    reply = trigger_message.reply_to_message
    if reply:
        reply_content = _message_content(reply)
        if reply_content:
            role = "assistant" if await _is_reply_to_bot(trigger_message, context) else "user"
            item = {"role": role, "content": reply_content}
            if role == "user":
                item["name"] = _user_label(reply.from_user)
            messages.append(item)

    text = user_text.strip() or ("Опиши изображение и ответь на запрос." if image else "Ответь на сообщение выше.")
    content: str | list[dict[str, Any]] = text
    if image:
        encoded = base64.b64encode(image.data).decode("ascii")
        content = [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"},
            },
        ]
    messages.append({"role": "user", "name": _user_label(trigger_message.from_user), "content": content})
    return messages


def _feedback_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for message in messages:
        copy = {key: value for key, value in message.items() if key != "content"}
        if isinstance(message["content"], str):
            copy["content"] = message["content"]
        else:
            parts = []
            for part in message["content"]:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    parts.append({"type": "image", "data_url_sha256": hashlib.sha256(url.encode()).hexdigest()})
                else:
                    parts.append(part)
            copy["content"] = parts
        safe.append(copy)
    return safe


async def _typing(message: Message, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await message.chat.send_action(ChatAction.TYPING)
        except Exception:
            return
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=4.0)


async def _uploading_photo(message: Message, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await message.chat.send_action(ChatAction.UPLOAD_PHOTO)
        except Exception:
            return
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=4.0)


async def _generate_and_reply(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
    prompt_override_name: str | None = None,
    settings_chat_id: int | None = None,
    trigger_reasons: tuple[str, ...] = ("unspecified",),
) -> None:
    config, db, *_ = _services(context)
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    target_chat_id = settings_chat_id or _target_chat_id(message, user, db, config)
    if target_chat_id not in config.allowed_chat_ids:
        await message.reply_text("not allowed")
        return
    generation_queue: GenerationQueue = context.application.bot_data["generation_queue"]
    rate_limits = db.get_effective_admission_settings(target_chat_id)
    metadata = describe_update(update)
    runtime_id = context.application.bot_data.get("runtime_id", "unknown")
    request_trace_id = trace_id(runtime_id, metadata)
    submitted_at = time.monotonic()
    logger.info(
        "generation_trigger runtime_id=%s trace_id=%s update_id=%s "
        "update_type=%s source_chat_id=%s source_message_id=%s user_id=%s "
        "settings_chat_id=%s trigger_reasons=%s reply_to_message_id=%s",
        runtime_id,
        request_trace_id,
        metadata.update_id,
        metadata.update_type,
        metadata.chat_id,
        metadata.message_id,
        metadata.user_id,
        target_chat_id,
        ",".join(trigger_reasons),
        metadata.reply_to_message_id,
    )

    async def run_generation() -> None:
        started_at = time.monotonic()
        outcome = "completed"
        logger.info(
            "generation_job_started runtime_id=%s trace_id=%s update_id=%s "
            "source_chat_id=%s source_message_id=%s queue_wait_ms=%s",
            runtime_id,
            request_trace_id,
            metadata.update_id,
            metadata.chat_id,
            metadata.message_id,
            int((started_at - submitted_at) * 1000),
        )
        try:
            await _generate_and_reply_inner(
                update=update,
                context=context,
                user_text=user_text,
                prompt_override_name=prompt_override_name,
                settings_chat_id=target_chat_id,
                request_trace_id=request_trace_id,
            )
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            outcome = "internal_error"
            logger.exception(
                "generation job failed runtime_id=%s trace_id=%s chat_id=%s "
                "message_id=%s user_id=%s",
                runtime_id,
                request_trace_id,
                target_chat_id,
                metadata.message_id,
                user.id,
            )
            with suppress(TelegramError):
                await message.reply_text("generation failed: internal error")
        finally:
            logger.info(
                "generation_job_finished runtime_id=%s trace_id=%s update_id=%s "
                "source_chat_id=%s source_message_id=%s outcome=%s "
                "queue_wait_ms=%s run_ms=%s total_ms=%s",
                runtime_id,
                request_trace_id,
                metadata.update_id,
                metadata.chat_id,
                metadata.message_id,
                outcome,
                int((started_at - submitted_at) * 1000),
                int((time.monotonic() - started_at) * 1000),
                int((time.monotonic() - submitted_at) * 1000),
            )

    admission = await generation_queue.submit(
        chat_id=target_chat_id,
        user_id=user.id,
        run=run_generation,
        user_requests=rate_limits["rate_limit_user_requests"],
        chat_requests=rate_limits["rate_limit_chat_requests"],
    )
    logger.info(
        "generation_admission runtime_id=%s trace_id=%s update_id=%s "
        "source_chat_id=%s source_message_id=%s allowed=%s queued=%s "
        "position=%s reason=%s active=%s pending=%s",
        runtime_id,
        request_trace_id,
        metadata.update_id,
        metadata.chat_id,
        metadata.message_id,
        admission.allowed,
        admission.queued,
        admission.position,
        admission.reason,
        getattr(generation_queue, "active", None),
        getattr(generation_queue, "pending", None),
    )
    if not admission.allowed:
        await message.reply_text(f"generation unavailable: {admission.reason}")


async def _generate_and_reply_inner(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
    prompt_override_name: str | None = None,
    settings_chat_id: int,
    request_trace_id: str,
) -> None:
    config, db, api, encryptor, cache, media_cache = _services(context)
    message = update.effective_message
    if not message:
        return
    target_chat_id = settings_chat_id
    active_prompt = db.get_active_prompt(target_chat_id)
    if prompt_override_name:
        active_prompt = db.get_prompt_by_name(target_chat_id, prompt_override_name)
        if active_prompt is None:
            await message.reply_text(f"unknown prompt: {prompt_override_name}")
            return
    model = db.get_active_model_profile(target_chat_id)
    try:
        api_url = validate_allowed_api_url(model["api_url"], config.allowed_api_urls)
    except ValueError as exc:
        await message.reply_text(f"model profile rejected: {exc}")
        return
    deferred_default = active_prompt is None and is_runpod_url(api_url)
    if deferred_default:
        prompt_text = None
        prompt_name = "API default"
    elif active_prompt is None:
        try:
            default_prompt = await api.get_default_prompt(api_url=api_url)
            prompt_text = str(default_prompt["text"])
            prompt_name = str(default_prompt["name"])
            default_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
            if default_hash != str(default_prompt["prompt_hash"]):
                raise ValueError("default prompt hash mismatch")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            await message.reply_text(f"could not load API default prompt: {exc}")
            return
    else:
        prompt_text = str(active_prompt["text"])
        prompt_name = str(active_prompt["name"])
    generation = db.get_effective_generation_settings(target_chat_id)
    image_source = _image_source(message)
    try:
        image = await _download_image(image_source, context) if image_source else None
    except (ValueError, httpx.HTTPError, TelegramError) as exc:
        media_label = "image"
        if image_source:
            if getattr(image_source, "sticker", None) is not None:
                media_label = "sticker"
            elif getattr(image_source, "video", None) is not None:
                media_label = "video"
            elif getattr(image_source, "animation", None) is not None:
                media_label = "animation"
        await message.reply_text(f"{media_label} rejected: {exc}")
        return
    messages = await _build_messages(
        trigger_message=message,
        user_text=user_text,
        image=image,
        context_depth=generation["recent_context_messages"],
        context=context,
    )

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing(message, stop_typing))
    api_started_at = time.monotonic()
    logger.info(
        "generation_api_started runtime_id=%s trace_id=%s source_chat_id=%s "
        "source_message_id=%s model_profile=%s has_image=%s message_count=%s",
        context.application.bot_data.get("runtime_id", "unknown"),
        request_trace_id,
        message.chat_id,
        message.message_id,
        model["name"],
        image is not None,
        len(messages),
    )
    try:
        result = await api.generate(
            api_url=api_url,
            messages=messages,
            system_prompt=prompt_text,
            max_tokens=generation["max_tokens"],
            temperature=generation["temperature"],
            top_p=generation["top_p"],
            top_k=generation["top_k"],
            repetition_penalty=generation["repetition_penalty"],
        )
        if deferred_default:
            default_prompt = validate_prompt(result.pop("default_prompt", None))
            if result.get("usage", {}).get("prompt_hash") != default_prompt["prompt_hash"]:
                raise httpx.HTTPError("Runpod generation prompt hash mismatch")
            prompt_text = default_prompt["text"]
            prompt_name = default_prompt["name"]
    except httpx.HTTPError as exc:
        logger.warning(
            "generation_api_failed runtime_id=%s trace_id=%s source_chat_id=%s "
            "source_message_id=%s error_type=%s elapsed_ms=%s",
            context.application.bot_data.get("runtime_id", "unknown"),
            request_trace_id,
            message.chat_id,
            message.message_id,
            type(exc).__name__,
            int((time.monotonic() - api_started_at) * 1000),
        )
        await message.reply_text(f"generation failed: {exc}")
        return
    except Exception as exc:
        logger.warning(
            "generation_api_failed runtime_id=%s trace_id=%s source_chat_id=%s "
            "source_message_id=%s error_type=%s elapsed_ms=%s",
            context.application.bot_data.get("runtime_id", "unknown"),
            request_trace_id,
            message.chat_id,
            message.message_id,
            type(exc).__name__,
            int((time.monotonic() - api_started_at) * 1000),
        )
        raise
    finally:
        stop_typing.set()
        with suppress(asyncio.CancelledError):
            await typing_task

    logger.info(
        "generation_api_finished runtime_id=%s trace_id=%s source_chat_id=%s "
        "source_message_id=%s elapsed_ms=%s",
        context.application.bot_data.get("runtime_id", "unknown"),
        request_trace_id,
        message.chat_id,
        message.message_id,
        int((time.monotonic() - api_started_at) * 1000),
    )

    assert prompt_text is not None
    response_text = _disable_telegram_mentions(str(result.get("response", ""))).strip() or "[empty response]"
    chunks = [response_text[i : i + MAX_TELEGRAM_TEXT] for i in range(0, len(response_text), MAX_TELEGRAM_TEXT)]
    sent_messages = [await message.reply_text(chunk) for chunk in chunks]
    usage = result.get("usage") or {}
    expected_prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    prompt_hash_verified = usage.get("prompt_hash") == expected_prompt_hash
    update_metadata = describe_update(update)
    feedback_payload = {
        "feedback_schema_version": 2,
        "trigger": {
            "update_id": update_metadata.update_id,
            "update_type": update_metadata.update_type,
            "chat_id": message.chat_id,
            "message_id": message.message_id,
            "from_user_id": message.from_user.id if message.from_user else None,
            "from_user_label": _user_label(message.from_user),
            "message_date": update_metadata.message_date,
            "edit_date": update_metadata.edit_date,
            "reply_to_message_id": update_metadata.reply_to_message_id,
            "reply_to_user_id": update_metadata.reply_to_user_id,
            "text": _message_content(message),
            "has_image": image is not None,
            "media": image.media_metadata if image else None,
        },
        "api_request": {
            "messages": _feedback_messages(messages),
            "system_prompt_name": prompt_name,
            "system_prompt_text": prompt_text,
            "system_prompt_hash": expected_prompt_hash,
            "model_profile_name": model["name"],
            "model_api_url": api_url,
            "generation": generation,
        },
        "generation_provenance": {
            "prompt_hash_verified": prompt_hash_verified,
            "model": result.get("model"),
            "usage": usage,
        },
        "api_response": result,
        "bot_response_text": response_text,
    }
    encrypted_feedback = encryptor.encrypt_json(feedback_payload)
    for sent in sent_messages:
        db.insert_outbound_message(
            chat_id=sent.chat_id,
            telegram_message_id=sent.message_id,
            prompt_preset_id=active_prompt["id"] if active_prompt else None,
            model_profile_id=model["id"],
            prompt_hash=usage.get("prompt_hash"),
        )
        db.insert_encrypted_feedback_sample(
            chat_id=sent.chat_id,
            telegram_message_id=sent.message_id,
            payload=feedback_payload,
            encrypted_payload_b64=encrypted_feedback,
            schema_version=2,
        )
    first = sent_messages[0]
    cache.add(first.chat_id, CachedMessage(first.message_id, "assistant", response_text))
    if image:
        for sent in sent_messages:
            media_cache.store(
                chat_id=sent.chat_id,
                telegram_message_id=sent.message_id,
                source=image.data,
                mime_type=image.mime_type,
                original_filename=image.filename,
            )


async def gpt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    text = " ".join(context.args or []).strip()
    if not text and not message.reply_to_message and not _has_image(message):
        await message.reply_text("usage: /gpt <message>, attach an image, or reply with /gpt")
        return
    await _generate_and_reply(
        update=update,
        context=context,
        user_text=text,
        trigger_reasons=("gpt_command",),
    )


async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config, db, *_ = _services(context)
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    prompt = re.sub(
        r"^/image(?:@\w+)?\b", "", _message_content(message), flags=re.I
    ).strip()
    if not prompt and message.reply_to_message:
        prompt = _message_content(message.reply_to_message)
    if not prompt:
        await message.reply_text("usage: /image <description> or reply to text with /image")
        return
    if len(prompt) > MAX_IMAGE_GENERATION_PROMPT_CHARS:
        await message.reply_text(
            f"image prompt is too long (maximum {MAX_IMAGE_GENERATION_PROMPT_CHARS} characters)"
        )
        return

    target_chat_id = _target_chat_id(message, user, db, config)
    if target_chat_id not in config.allowed_chat_ids:
        await message.reply_text("not allowed")
        return
    if not config.image_generation_enabled:
        await message.reply_text("image generation is disabled by server policy")
        return

    image_settings = db.get_image_generation_settings(target_chat_id)
    if not image_settings["enabled"]:
        await message.reply_text("image generation is disabled for this chat")
        return

    queue: GenerationQueue = context.application.bot_data["image_generation_queue"]
    client: ImageGenerationClient = context.application.bot_data["image_generation"]
    rate_limits = db.get_effective_admission_settings(target_chat_id)
    metadata = describe_update(update)
    runtime_id = context.application.bot_data.get("runtime_id", "unknown")
    request_trace_id = trace_id(runtime_id, metadata)
    submitted_at = time.monotonic()
    logger.info(
        "image_generation_trigger runtime_id=%s trace_id=%s update_id=%s "
        "source_chat_id=%s source_message_id=%s user_id=%s prompt_chars=%s",
        runtime_id,
        request_trace_id,
        metadata.update_id,
        metadata.chat_id,
        metadata.message_id,
        metadata.user_id,
        len(prompt),
    )

    async def run_generation() -> None:
        started_at = time.monotonic()
        outcome = "completed"
        stop_action = asyncio.Event()
        action_task = asyncio.create_task(_uploading_photo(message, stop_action))
        logger.info(
            "image_generation_started runtime_id=%s trace_id=%s update_id=%s "
            "source_chat_id=%s source_message_id=%s queue_wait_ms=%s",
            runtime_id,
            request_trace_id,
            metadata.update_id,
            metadata.chat_id,
            metadata.message_id,
            int((started_at - submitted_at) * 1000),
        )
        try:
            image = await client.generate(
                prompt,
                width=image_settings["width"],
                height=image_settings["height"],
                steps=image_settings["steps"],
                guidance_scale=image_settings["guidance_scale"],
                auto_translate=image_settings["auto_translate"],
            )
            await message.reply_photo(
                photo=InputFile(image, filename="generated.png"),
                has_spoiler=True,
            )
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except ImageGenerationError as exc:
            outcome = "service_error"
            logger.warning(
                "image generation failed runtime_id=%s trace_id=%s "
                "source_chat_id=%s source_message_id=%s error_type=%s",
                runtime_id,
                request_trace_id,
                metadata.chat_id,
                metadata.message_id,
                type(exc).__name__,
            )
            with suppress(TelegramError):
                await message.reply_text(f"image generation failed: {exc}")
        except Exception:
            outcome = "internal_error"
            logger.exception(
                "image generation failed runtime_id=%s trace_id=%s "
                "source_chat_id=%s source_message_id=%s",
                runtime_id,
                request_trace_id,
                metadata.chat_id,
                metadata.message_id,
            )
            with suppress(TelegramError):
                await message.reply_text("image generation failed: internal error")
        finally:
            stop_action.set()
            await action_task
            logger.info(
                "image_generation_finished runtime_id=%s trace_id=%s update_id=%s "
                "source_chat_id=%s source_message_id=%s outcome=%s "
                "queue_wait_ms=%s run_ms=%s total_ms=%s",
                runtime_id,
                request_trace_id,
                metadata.update_id,
                metadata.chat_id,
                metadata.message_id,
                outcome,
                int((started_at - submitted_at) * 1000),
                int((time.monotonic() - started_at) * 1000),
                int((time.monotonic() - submitted_at) * 1000),
            )

    admission = await queue.submit(
        chat_id=target_chat_id,
        user_id=user.id,
        run=run_generation,
        user_requests=rate_limits["rate_limit_user_requests"],
        chat_requests=rate_limits["rate_limit_chat_requests"],
    )
    logger.info(
        "image_generation_admission runtime_id=%s trace_id=%s update_id=%s "
        "source_chat_id=%s source_message_id=%s allowed=%s queued=%s "
        "reason=%s active=%s pending=%s",
        runtime_id,
        request_trace_id,
        metadata.update_id,
        metadata.chat_id,
        metadata.message_id,
        admission.allowed,
        admission.queued,
        admission.reason,
        queue.active,
        queue.pending,
    )
    if not admission.allowed:
        await message.reply_text(f"image generation unavailable: {admission.reason}")


async def media_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    content = _message_content(message)
    trigger_reasons = await _media_message_trigger_reasons(message, context)
    if not trigger_reasons:
        return
    text = re.sub(r"^/gpt(?:@\w+)?\b", "", content, flags=re.I).strip()
    text = await _strip_bot_mention(text, context)
    await _generate_and_reply(
        update=update,
        context=context,
        user_text=text,
        trigger_reasons=trigger_reasons,
    )


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config, db, *_ = _services(context)
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return
    pending = db.get_pending_action(
        chat_id=message.chat_id,
        user_id=user.id,
        ttl_seconds=config.pending_action_ttl_seconds,
    )
    pending_target = pending["target_chat_id"] if pending else None
    if pending and pending_target is None:
        db.delete_pending_action(chat_id=message.chat_id, user_id=user.id)
        pending = None
    if pending and await _is_authorized_admin(
        update, context, target_chat_id=int(pending_target)
    ):
        target_chat_id = int(pending_target)
        if pending:
            if pending["action"] == "prompt_text":
                try:
                    db.upsert_prompt(
                        chat_id=target_chat_id,
                        name=pending["target_name"],
                        text=message.text,
                        created_by=user.id,
                    )
                except ValueError as exc:
                    await message.reply_text(str(exc))
                    return
                db.delete_pending_action(chat_id=message.chat_id, user_id=user.id)
                await message.reply_text(f"saved prompt: {pending['target_name']}")
                return
            if pending["action"] == "prompt_new_spec":
                name, separator, prompt_text = message.text.partition("\n")
                if not separator:
                    await message.reply_text("send a name on the first line and prompt text below it")
                    return
                try:
                    prompt = db.upsert_prompt(
                        chat_id=target_chat_id,
                        name=name,
                        text=prompt_text,
                        created_by=user.id,
                    )
                except ValueError as exc:
                    await message.reply_text(str(exc))
                    return
                db.delete_pending_action(chat_id=message.chat_id, user_id=user.id)
                await message.reply_text(f"saved prompt: {prompt['name']}")
                return
            if pending["action"] == "prompt_test":
                db.delete_pending_action(chat_id=message.chat_id, user_id=user.id)
                await _generate_and_reply(
                    update=update,
                    context=context,
                    user_text=message.text,
                    prompt_override_name=pending["target_name"],
                    settings_chat_id=target_chat_id,
                    trigger_reasons=("prompt_test_pending",),
                )
                return
            if pending["action"] == "image_custom_settings":
                try:
                    values = _parse_image_custom_settings(message.text)
                    db.set_image_generation_settings(target_chat_id, **values)
                except ValueError as exc:
                    await message.reply_text(str(exc))
                    return
                db.delete_pending_action(chat_id=message.chat_id, user_id=user.id)
                current = db.get_image_generation_settings(target_chat_id)
                await message.reply_text(
                    "saved image settings: "
                    f"{current['width']}x{current['height']}, "
                    f"{current['steps']} steps, guidance "
                    f"{current['guidance_scale']}, translation "
                    f"{'on' if current['auto_translate'] else 'off'}"
                )
                return
            if pending["action"] in {"model_new_spec", "model_edit_spec"}:
                if not _is_owner(config, user):
                    await message.reply_text("owner access required")
                    return
                try:
                    args = shlex.split(message.text)
                    if pending["action"] == "model_new_spec":
                        if len(args) < 2:
                            raise ValueError("send: name api_url [max] [temp] [top_p] [top_k] [repeat]")
                        name, api_url, values = validate_name(args[0]), args[1], args[2:]
                    else:
                        if not args:
                            raise ValueError("send: api_url [max] [temp] [top_p] [top_k] [repeat]")
                        name, api_url, values = pending["target_name"], args[0], args[1:]
                    api_url = validate_allowed_api_url(api_url, config.allowed_api_urls)
                    values_dict = validate_generation_settings(
                        max_tokens=int(values[0]) if len(values) > 0 else config.default_max_tokens,
                        temperature=float(values[1]) if len(values) > 1 else config.default_temperature,
                        top_p=float(values[2]) if len(values) > 2 else config.default_top_p,
                        top_k=int(values[3]) if len(values) > 3 else config.default_top_k,
                        repetition_penalty=float(values[4]) if len(values) > 4 else config.default_repetition_penalty,
                    )
                    db.upsert_model_profile(
                        name=name,
                        api_url=api_url,
                        model_label=name,
                        **values_dict,
                    )
                except ValueError as exc:
                    await message.reply_text(str(exc))
                    return
                db.delete_pending_action(chat_id=message.chat_id, user_id=user.id)
                await message.reply_text(f"saved model: {name}")
                return
    trigger_reasons = await _text_message_trigger_reasons(message, context)
    if trigger_reasons:
        await _generate_and_reply(
            update=update,
            context=context,
            user_text=await _strip_bot_mention(message.text, context),
            trigger_reasons=trigger_reasons,
        )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, db, *_ = _services(context)
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    deleted = db.delete_pending_action(chat_id=message.chat_id, user_id=user.id)
    await message.reply_text("pending action cancelled" if deleted else "no pending action")


def _encode_chat_id(chat_id: int) -> str:
    return ("n" if chat_id < 0 else "p") + str(abs(chat_id))


def _decode_chat_id(value: str) -> int:
    if not re.fullmatch(r"[np]\d+", value):
        raise ValueError("invalid settings link")
    return -int(value[1:]) if value[0] == "n" else int(value[1:])


def _settings_callback_data(chat_id: int, action: str) -> str:
    value = f"s:{_encode_chat_id(chat_id)}:{action}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("settings callback exceeds Telegram's 64-byte limit")
    return value


def _parse_settings_callback_data(value: str) -> tuple[int, str]:
    prefix, encoded_chat_id, action = value.split(":", 2)
    if prefix != "s" or not action:
        raise ValueError("invalid settings callback")
    return _decode_chat_id(encoded_chat_id), action


def _settings_button_label(kind: str, name: str, max_length: int = 48) -> str:
    label = f"{kind}: {name}"
    if len(label) <= max_length:
        return label
    return f"{label[: max_length - 3].rstrip()}..."


def _prompt_preview_text(name: str, prompt_text: str, max_length: int = 3800) -> str:
    header = f"Prompt: {name}\n\n"
    if len(header) + len(prompt_text) <= max_length:
        return f"{header}{prompt_text}"
    marker = "\n\n[preview truncated]"
    retained = max_length - len(header) - len(marker)
    return f"{header}{prompt_text[:retained].rstrip()}{marker}"


def _image_setting_values(settings: dict[str, Any]) -> dict[str, int | float]:
    return {
        "image_generation_enabled": int(settings["enabled"]),
        "image_width": settings["width"],
        "image_height": settings["height"],
        "image_steps": settings["steps"],
        "image_guidance_scale": settings["guidance_scale"],
        "image_auto_translate": int(settings["auto_translate"]),
    }


def _parse_image_custom_settings(text: str) -> dict[str, int | float]:
    raw = text.strip()
    if not raw:
        raise ValueError("send image settings as JSON or: width height steps guidance [translate]")

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid image settings JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("image settings JSON must be an object")
        aliases = {
            "width": "image_width",
            "height": "image_height",
            "steps": "image_steps",
            "guidance_scale": "image_guidance_scale",
            "auto_translate": "image_auto_translate",
        }
        unknown = set(payload) - set(aliases)
        if unknown:
            raise ValueError(f"unknown image setting: {sorted(unknown)[0]}")
        required = {"width", "height", "steps", "guidance_scale"}
        missing = required - set(payload)
        if missing:
            raise ValueError(f"missing image setting: {sorted(missing)[0]}")
        values = {aliases[name]: value for name, value in payload.items()}
    else:
        args = shlex.split(raw)
        if len(args) not in {4, 5}:
            raise ValueError(
                "send: width height steps guidance [translate]; "
                "translate is on/off"
            )
        values = {
            "image_width": args[0],
            "image_height": args[1],
            "image_steps": args[2],
            "image_guidance_scale": args[3],
        }
        if len(args) == 5:
            translate = args[4].lower()
            if translate not in {"1", "0", "true", "false", "on", "off"}:
                raise ValueError("translate must be on or off")
            values["image_auto_translate"] = int(
                translate in {"1", "true", "on"}
            )

    return validate_image_generation_settings(**values)


def _image_settings_view(
    chat_id: int, settings: dict[str, Any]
) -> tuple[str, InlineKeyboardMarkup]:
    cb = lambda action: _settings_callback_data(chat_id, action)
    rows = [
        [
            InlineKeyboardButton(
                f"Chat: {'on' if settings['enabled'] else 'off'}",
                callback_data=cb("field:image_generation_enabled"),
            )
        ],
        [
            InlineKeyboardButton(
                f"Width {settings['width']}",
                callback_data=cb("field:image_width"),
            ),
            InlineKeyboardButton(
                f"Height {settings['height']}",
                callback_data=cb("field:image_height"),
            ),
        ],
        [
            InlineKeyboardButton(
                f"Steps {settings['steps']}",
                callback_data=cb("field:image_steps"),
            ),
            InlineKeyboardButton(
                f"Guidance {settings['guidance_scale']}",
                callback_data=cb("field:image_guidance_scale"),
            ),
        ],
        [
            InlineKeyboardButton(
                f"Translate: {'on' if settings['auto_translate'] else 'off'}",
                callback_data=cb("field:image_auto_translate"),
            )
        ],
        [InlineKeyboardButton("Custom parameters", callback_data=cb("image_custom"))],
        [InlineKeyboardButton("Back", callback_data=cb("home"))],
    ]
    text = (
        "Image generation settings\n\n"
        f"Current: {settings['width']}x{settings['height']}, "
        f"{settings['steps']} steps, guidance {settings['guidance_scale']}, "
        f"translation {'on' if settings['auto_translate'] else 'off'}"
    )
    return text, InlineKeyboardMarkup(rows)


def _setting_option_label(field: str, value: int | float) -> str:
    if field in {"image_generation_enabled", "image_auto_translate"}:
        return "On" if value == 1 else "Off"
    return str(value)


def _settings_view(
    db: BotDB,
    chat_id: int,
    *,
    bot_username: str | None,
    private: bool,
    rate_window_seconds: int = 60,
    image_generation_globally_enabled: bool = True,
) -> tuple[str, InlineKeyboardMarkup]:
    prompt = db.get_active_prompt(chat_id)
    model = db.get_active_model_profile(chat_id)
    values = db.get_effective_generation_settings(chat_id)
    rate_limits = db.get_effective_admission_settings(chat_id)
    image_settings = db.get_image_generation_settings(chat_id)
    prompt_name = prompt["name"] if prompt else "API default"
    model_name = model["name"]
    text = "\n".join(
        [
            f"Settings for chat {chat_id}",
            f"Prompt: {prompt_name}",
            f"Model: {model_name}",
            f"Context: {values['recent_context_messages']} recent messages (RAM only)",
            f"Max tokens: {values['max_tokens']}",
            f"Temperature: {values['temperature']}",
            f"Top P: {values['top_p']}",
            f"Top K: {values['top_k']}",
            f"Repeat penalty: {values['repetition_penalty']}",
            f"User rate: {rate_limits['rate_limit_user_requests']}/{rate_window_seconds}s",
            f"Chat rate: {rate_limits['rate_limit_chat_requests']}/{rate_window_seconds}s",
            (
                "Images: disabled by server policy"
                if not image_generation_globally_enabled
                else "Images: "
                f"{'on' if image_settings['enabled'] else 'off'}, "
                f"{image_settings['width']}x{image_settings['height']}, "
                f"{image_settings['steps']} steps"
            ),
            "",
            "Prompt editing works here; model endpoint editing requires an owner."
            if private else "Open the private editor for prompt management.",
        ]
    )
    rows = [
        [
            InlineKeyboardButton(
                _settings_button_label("Prompt", prompt_name),
                callback_data=_settings_callback_data(chat_id, "prompt_preview"),
            ),
            InlineKeyboardButton(
                _settings_button_label("Model", model_name),
                callback_data=_settings_callback_data(chat_id, "models"),
            ),
        ],
        [
            InlineKeyboardButton(
                f"Context {values['recent_context_messages']}",
                callback_data=_settings_callback_data(
                    chat_id, "field:recent_context_messages"
                ),
            ),
            InlineKeyboardButton(
                f"Max {values['max_tokens']}",
                callback_data=_settings_callback_data(chat_id, "field:max_tokens"),
            ),
        ],
        [
            InlineKeyboardButton(
                f"Temp {values['temperature']}",
                callback_data=_settings_callback_data(chat_id, "field:temperature"),
            ),
            InlineKeyboardButton(
                f"Top P {values['top_p']}",
                callback_data=_settings_callback_data(chat_id, "field:top_p"),
            ),
        ],
        [
            InlineKeyboardButton(
                f"Top K {values['top_k']}",
                callback_data=_settings_callback_data(chat_id, "field:top_k"),
            ),
            InlineKeyboardButton(
                f"Repeat {values['repetition_penalty']}",
                callback_data=_settings_callback_data(
                    chat_id, "field:repetition_penalty"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                f"User/{rate_window_seconds}s "
                f"{rate_limits['rate_limit_user_requests']}",
                callback_data=_settings_callback_data(
                    chat_id, "field:rate_limit_user_requests"
                ),
            ),
            InlineKeyboardButton(
                f"Chat/{rate_window_seconds}s "
                f"{rate_limits['rate_limit_chat_requests']}",
                callback_data=_settings_callback_data(
                    chat_id, "field:rate_limit_chat_requests"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                (
                    "Images: server-disabled"
                    if not image_generation_globally_enabled
                    else "Images: "
                    f"{'on' if image_settings['enabled'] else 'off'} "
                    f"{image_settings['width']}x{image_settings['height']}"
                ),
                callback_data=_settings_callback_data(chat_id, "images"),
            )
        ],
    ]
    if not private and bot_username:
        rows.append([InlineKeyboardButton("Edit in private chat", url=f"https://t.me/{bot_username}?start=settings_{_encode_chat_id(chat_id)}")])
    return text, InlineKeyboardMarkup(rows)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    config, db, *_ = _services(context)
    message = update.effective_message
    target = _target_chat_id(message, update.effective_user, db, config)
    _, username = await _bot_identity(context)
    text, keyboard = _settings_view(
        db,
        target,
        bot_username=username,
        private=message.chat.type == ChatType.PRIVATE,
        rate_window_seconds=config.rate_limit_window_seconds,
        image_generation_globally_enabled=config.image_generation_enabled,
    )
    await message.reply_text(text, reply_markup=keyboard)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, db, *_ = _services(context)
    message = update.effective_message
    user = update.effective_user
    argument = (context.args or [""])[0]
    if message.chat.type != ChatType.PRIVATE or not argument.startswith("settings_"):
        if not await _require_admin(update, context):
            return
        await message.reply_text("Use /settings in the group you want to configure.")
        return
    try:
        target = _decode_chat_id(argument.removeprefix("settings_"))
    except ValueError as exc:
        await message.reply_text(str(exc))
        return
    if not await _require_admin(update, context, target_chat_id=target):
        return
    db.set_admin_session(user_id=user.id, target_chat_id=target)
    await settings_command(update, context)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config, db, api, *_ = _services(context)
    if not query or not query.message:
        return
    try:
        target, data = _parse_settings_callback_data(query.data or "")
    except ValueError:
        await query.answer("stale or invalid settings button", show_alert=True)
        return
    if target not in config.allowed_chat_ids:
        await query.answer("not allowed", show_alert=True)
        return
    if not await _is_authorized_admin(
        update,
        context,
        target_chat_id=target,
    ):
        await query.answer("not allowed", show_alert=True)
        return
    private = query.message.chat.type == ChatType.PRIVATE
    cb = lambda action: _settings_callback_data(target, action)
    if data == "prompt_preview":
        await query.answer()
        active = db.get_active_prompt(target)
        if active is None:
            model = db.get_active_model_profile(target)
            try:
                prompt = await api.get_default_prompt(api_url=model["api_url"])
                prompt_name = str(prompt["name"])
                prompt_text = str(prompt["text"])
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                await query.edit_message_text(
                    "The active model endpoint does not provide prompt preview.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Choose prompt", callback_data=cb("prompts"))],
                        [InlineKeyboardButton("Back", callback_data=cb("home"))],
                    ]),
                )
                return
        else:
            prompt_name = active["name"]
            prompt_text = active["text"]
        rows = [
            [InlineKeyboardButton("Choose prompt", callback_data=cb("prompts"))],
        ]
        if private and active is not None:
            rows.append([
                InlineKeyboardButton("Edit", callback_data=cb("prompt_edit")),
                InlineKeyboardButton("Test", callback_data=cb("prompt_test")),
            ])
        rows.append([InlineKeyboardButton("Back", callback_data=cb("home"))])
        await query.edit_message_text(
            _prompt_preview_text(prompt_name, prompt_text),
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return
    if data in {"prompt_new", "model_new"}:
        if not private:
            await query.answer("open the private editor", show_alert=True)
            return
        if data == "model_new" and not _is_owner(config, update.effective_user):
            await query.answer("owner access required", show_alert=True)
            return
        action = "prompt_new_spec" if data == "prompt_new" else "model_new_spec"
        db.set_pending_action(
            chat_id=query.message.chat_id,
            user_id=update.effective_user.id,
            target_chat_id=target,
            action=action,
            target_name="new",
        )
        instruction = (
            "Send the next message as:\nname\nprompt text"
            if data == "prompt_new"
            else "Send the next message as:\nname api_url [max] [temp] [top_p] [top_k] [repeat]"
        )
        await query.edit_message_text(instruction)
        await query.answer()
        return
    if data == "image_custom":
        if not private:
            await query.answer("open the private editor", show_alert=True)
            return
        if not config.image_generation_enabled:
            await query.answer("disabled by server policy", show_alert=True)
            return
        db.set_pending_action(
            chat_id=query.message.chat_id,
            user_id=update.effective_user.id,
            target_chat_id=target,
            action="image_custom_settings",
            target_name="image_custom",
        )
        await query.edit_message_text(
            "Send custom image settings as JSON:\n"
            '{"width":512,"height":512,"steps":16,'
            '"guidance_scale":5.0,"auto_translate":true}\n\n'
            "Or send: width height steps guidance [translate]\n"
            "Limits: width/height 256-512 in 64px steps; steps 2-16; "
            "guidance 0-5; translate on/off."
        )
        await query.answer()
        return
    if data in {"prompt_edit", "prompt_test", "model_edit"}:
        if not private:
            await query.answer("open the private editor", show_alert=True)
            return
        if data.startswith("prompt"):
            active = db.get_active_prompt(target)
        else:
            active = db.get_active_model_profile(target)
        if active is None:
            await query.answer("choose a saved item first", show_alert=True)
            return
        action = {
            "prompt_edit": "prompt_text",
            "prompt_test": "prompt_test",
            "model_edit": "model_edit_spec",
        }[data]
        if data == "model_edit" and not _is_owner(config, update.effective_user):
            await query.answer("owner access required", show_alert=True)
            return
        db.set_pending_action(
            chat_id=query.message.chat_id,
            user_id=update.effective_user.id,
            target_chat_id=target,
            action=action,
            target_name=active["name"],
        )
        instruction = {
            "prompt_edit": f"Send the replacement text for prompt {active['name']}.",
            "prompt_test": f"Send a test message for prompt {active['name']}.",
            "model_edit": "Send: api_url [max] [temp] [top_p] [top_k] [repeat]",
        }[data]
        await query.edit_message_text(instruction)
        await query.answer()
        return
    if data in {"prompt_delete", "model_delete"}:
        if data == "model_delete" and not _is_owner(config, update.effective_user):
            await query.answer("owner access required", show_alert=True)
            return
        active = db.get_active_prompt(target) if data == "prompt_delete" else db.get_active_model_profile(target)
        if active is None or active["name"] == "default":
            await query.answer("nothing deletable is active", show_alert=True)
            return
        kind = "prompt" if data == "prompt_delete" else "model"
        await query.edit_message_text(
            f"Delete {kind} {active['name']}?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Delete", callback_data=cb(f"{kind}_delete_confirm:{active['id']}"))],
                [InlineKeyboardButton("Cancel", callback_data=cb(f"{kind}s"))],
            ]),
        )
        await query.answer()
        return
    if data.startswith("prompt_delete_confirm:"):
        active = db.get_prompt_by_id(target, int(data.rsplit(":", 1)[1]))
        if active:
            db.soft_delete_prompt(target, active["name"])
            db.set_active_prompt(target, None)
    if data.startswith("model_delete_confirm:"):
        if not _is_owner(config, update.effective_user):
            await query.answer("owner access required", show_alert=True)
            return
        active = db.get_model_by_id(int(data.rsplit(":", 1)[1]))
        if active and active["name"] != "default":
            db.soft_delete_model_profile(active["name"])
            db.set_active_model_profile(target, None)
    if data.startswith("set:"):
        _, field, raw = data.split(":", 2)
        options = SETTING_OPTIONS.get(field)
        if options is None:
            await query.answer("unknown setting", show_alert=True)
            return
        value = next((item for item in options if str(item) == raw), None)
        if value is None:
            await query.answer("invalid value", show_alert=True)
            return
        if (
            field == "image_generation_enabled"
            and value == 1
            and not config.image_generation_enabled
        ):
            await query.answer("disabled by server policy", show_alert=True)
            return
        db.set_chat_setting(target, field, value)
    elif data.startswith("prompt:"):
        prompt_id = int(data.rsplit(":", 1)[1])
        if prompt_id != 0 and db.get_prompt_by_id(target, prompt_id) is None:
            await query.answer("unknown prompt", show_alert=True)
            return
        db.set_active_prompt(target, None if prompt_id == 0 else prompt_id)
    elif data.startswith("model:"):
        model = db.get_model_by_id(int(data.rsplit(":", 1)[1]))
        if model is None:
            await query.answer("unknown model", show_alert=True)
            return
        try:
            validate_allowed_api_url(model["api_url"], config.allowed_api_urls)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        db.set_active_model_profile(target, model["id"])

    if data.startswith("field:"):
        field = data.removeprefix("field:")
        if field not in SETTING_OPTIONS:
            await query.answer("unknown setting", show_alert=True)
            return
        values = (
            db.get_effective_admission_settings(target)
            if field in ADMISSION_SETTING_FIELDS
            else _image_setting_values(db.get_image_generation_settings(target))
            if field in IMAGE_SETTING_FIELDS
            else db.get_effective_generation_settings(target)
        )
        buttons = [
            InlineKeyboardButton(
                ("* " if option == values[field] else "")
                + _setting_option_label(field, option),
                callback_data=cb(f"set:{field}:{option}"),
            )
            for option in SETTING_OPTIONS[field]
        ]
        rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
        rows.append(
            [
                InlineKeyboardButton(
                    "Back",
                    callback_data=cb(
                        "images" if field in IMAGE_SETTING_FIELDS else "home"
                    ),
                )
            ]
        )
        await query.edit_message_text(SETTING_LABELS[field], reply_markup=InlineKeyboardMarkup(rows))
        await query.answer()
        return
    if data == "images":
        values = db.get_image_generation_settings(target)
        if not config.image_generation_enabled:
            await query.edit_message_text(
                "Image generation is disabled by server policy. Telegram settings cannot enable it.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back", callback_data=cb("home"))]]
                ),
            )
            await query.answer()
            return
        text, keyboard = _image_settings_view(target, values)
        await query.edit_message_text(text, reply_markup=keyboard)
        await query.answer()
        return
    if data == "prompts":
        prompt = db.get_active_prompt(target)
        rows = [[InlineKeyboardButton(("* " if prompt is None else "") + "API default", callback_data=cb("prompt:0"))]]
        rows.extend(
            [InlineKeyboardButton(
                ("* " if prompt and row["id"] == prompt["id"] else "") + row["name"],
                callback_data=cb(f"prompt:{row['id']}"),
            )]
            for row in db.list_prompts(target)
        )
        rows.append([InlineKeyboardButton("Back", callback_data=cb("home"))])
        if private:
            rows.insert(-1, [
                InlineKeyboardButton("New", callback_data=cb("prompt_new")),
                InlineKeyboardButton("Edit", callback_data=cb("prompt_edit")),
                InlineKeyboardButton("Test", callback_data=cb("prompt_test")),
            ])
            rows.insert(-1, [InlineKeyboardButton("Delete active", callback_data=cb("prompt_delete"))])
        await query.edit_message_text("Choose prompt", reply_markup=InlineKeyboardMarkup(rows))
        await query.answer()
        return
    if data == "models":
        active = db.get_active_model_profile(target)
        rows = [[InlineKeyboardButton(("* " if row["id"] == active["id"] else "") + row["name"], callback_data=cb(f"model:{row['id']}"))] for row in db.list_model_profiles() if row["api_url"] in config.allowed_api_urls]
        rows.append([InlineKeyboardButton("Back", callback_data=cb("home"))])
        if private and _is_owner(config, update.effective_user):
            rows.insert(-1, [
                InlineKeyboardButton("New", callback_data=cb("model_new")),
                InlineKeyboardButton("Edit", callback_data=cb("model_edit")),
                InlineKeyboardButton("Delete active", callback_data=cb("model_delete")),
            ])
        await query.edit_message_text("Choose model endpoint", reply_markup=InlineKeyboardMarkup(rows))
        await query.answer()
        return
    _, username = await _bot_identity(context)
    text, keyboard = _settings_view(
        db,
        target,
        bot_username=username,
        private=query.message.chat.type == ChatType.PRIVATE,
        rate_window_seconds=config.rate_limit_window_seconds,
        image_generation_globally_enabled=config.image_generation_enabled,
    )
    await query.edit_message_text(text, reply_markup=keyboard)
    await query.answer("saved" if data.startswith(("set:", "prompt:", "model:")) else None)


async def prompts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context)
    target = _target_chat_id(update.effective_message, update.effective_user, db, config)
    prompts = db.list_prompts(target)
    await _reply(update.effective_message, "\n".join(["prompts:", *[f"- {row['name']}" for row in prompts]]) if prompts else "no prompts")


async def prompt_show_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context)
    message = update.effective_message
    target = _target_chat_id(message, update.effective_user, db, config)
    prompt = db.get_prompt_by_name(target, context.args[0]) if context.args else db.get_active_prompt(target)
    await _reply(message, f"{prompt['name']}:\n\n{prompt['text']}" if prompt else "no prompt")


async def prompt_use_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context); message = update.effective_message
    if not context.args: await message.reply_text("usage: /prompt_use <name>"); return
    target = _target_chat_id(message, update.effective_user, db, config)
    prompt = db.get_prompt_by_name(target, context.args[0])
    if not prompt: await message.reply_text("unknown prompt"); return
    db.set_active_prompt(target, prompt["id"])
    await message.reply_text(f"active prompt: {prompt['name']}")


async def prompt_new_or_edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context); message = update.effective_message; user = update.effective_user
    if not context.args: await message.reply_text("usage: /prompt_new <name> [text] or /prompt_edit <name> [text]"); return
    try: name = validate_name(context.args[0])
    except ValueError as exc: await message.reply_text(str(exc)); return
    inline_text = " ".join(context.args[1:]).strip()
    target = _target_chat_id(message, user, db, config)
    if inline_text:
        db.upsert_prompt(chat_id=target, name=name, text=inline_text, created_by=user.id); await message.reply_text(f"saved prompt: {name}"); return
    db.set_pending_action(chat_id=message.chat_id, user_id=user.id, target_chat_id=target, action="prompt_text", target_name=name)
    await message.reply_text(f"send next message as prompt text for: {name}")


async def prompt_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context); message = update.effective_message
    if not context.args: await message.reply_text("usage: /prompt_delete <name>"); return
    target = _target_chat_id(message, update.effective_user, db, config)
    await message.reply_text("deleted" if db.soft_delete_prompt(target, context.args[0]) else "unknown prompt")


async def prompt_reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context); message = update.effective_message
    db.set_active_prompt(_target_chat_id(message, update.effective_user, db, config), None)
    await message.reply_text("active prompt reset to API default")


async def prompt_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    if len(context.args or []) < 2: await update.effective_message.reply_text("usage: /prompt_test <name> <message>"); return
    await _generate_and_reply(
        update=update,
        context=context,
        user_text=" ".join(context.args[1:]),
        prompt_override_name=context.args[0],
        trigger_reasons=("prompt_test_command",),
    )


async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context)
    lines = ["models:", *[f"- {row['name']} {row['api_url']}" for row in db.list_model_profiles() if row["api_url"] in config.allowed_api_urls]]
    await _reply(update.effective_message, "\n".join(lines))


async def model_show_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context); message = update.effective_message
    target = _target_chat_id(message, update.effective_user, db, config)
    model = db.get_model_by_name(context.args[0]) if context.args else db.get_active_model_profile(target)
    await _reply(message, "\n".join(f"{field}: {model[field]}" for field in ("name", "api_url", "model_label", "max_tokens", "temperature", "top_p", "top_k", "repetition_penalty")))


async def model_use_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    config, db, *_ = _services(context); message = update.effective_message
    if not context.args: await message.reply_text("usage: /model_use <name>"); return
    model = db.get_model_by_name(context.args[0])
    if not model: await message.reply_text("unknown model"); return
    try: validate_allowed_api_url(model["api_url"], config.allowed_api_urls)
    except ValueError as exc: await message.reply_text(str(exc)); return
    db.set_active_model_profile(_target_chat_id(message, update.effective_user, db, config), model["id"])
    await message.reply_text(f"active model: {model['name']}")


async def model_new_or_edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_owner(update, context): return
    config, db, *_ = _services(context); message = update.effective_message; args = context.args or []
    if len(args) < 2:
        await message.reply_text("usage: /model_new <name> <api_url> [max_tokens] [temperature] [top_p] [top_k] [repetition_penalty]"); return
    try:
        name = validate_name(args[0]); api_url = validate_allowed_api_url(args[1], config.allowed_api_urls)
        max_tokens = int(args[2]) if len(args) > 2 else config.default_max_tokens
        temperature = float(args[3]) if len(args) > 3 else config.default_temperature
        top_p = float(args[4]) if len(args) > 4 else config.default_top_p
        top_k = int(args[5]) if len(args) > 5 else config.default_top_k
        repeat = float(args[6]) if len(args) > 6 else config.default_repetition_penalty
    except ValueError as exc: await message.reply_text(str(exc)); return
    try:
        values = validate_generation_settings(max_tokens=max_tokens, temperature=temperature, top_p=top_p, top_k=top_k, repetition_penalty=repeat)
        db.upsert_model_profile(name=name, api_url=api_url, model_label=name, **values)
    except ValueError as exc: await message.reply_text(str(exc)); return
    await message.reply_text(f"saved model: {name}")


async def model_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_owner(update, context): return
    _, db, *_ = _services(context); message = update.effective_message
    if not context.args: await message.reply_text("usage: /model_delete <name>"); return
    if context.args[0] == "default": await message.reply_text("default model cannot be deleted"); return
    await message.reply_text("deleted" if db.soft_delete_model_profile(context.args[0]) else "unknown model")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    config, db, *_ = _services(context)
    message = update.effective_message
    target = _target_chat_id(message, update.effective_user, db, config)
    prompt = db.get_active_prompt(target)
    model = db.get_active_model_profile(target)
    values = db.get_effective_generation_settings(target)
    rate_limits = db.get_effective_admission_settings(target)
    image_settings = db.get_image_generation_settings(target)
    bot_id, username = await _bot_identity(context)
    stats = db.feedback_stats()
    generation_queue: GenerationQueue = context.application.bot_data["generation_queue"]
    image_generation_queue = context.application.bot_data.get(
        "image_generation_queue"
    )
    await _reply(
        message,
        "\n".join(
            [
                f"bot: @{username} ({bot_id})" if username else f"bot: {bot_id}",
                f"db: {config.db_path}",
                f"settings_chat: {target}",
                f"active_prompt: {prompt['name'] if prompt else 'api-default'}",
                f"active_model: {model['name']}",
                f"context_messages: {values['recent_context_messages']}",
                f"user_rate_limit: {rate_limits['rate_limit_user_requests']}"
                f"/{config.rate_limit_window_seconds}s",
                f"chat_rate_limit: {rate_limits['rate_limit_chat_requests']}"
                f"/{config.rate_limit_window_seconds}s",
                f"generation_active: {generation_queue.active}",
                f"generation_queued: {generation_queue.pending}",
                f"image_generation_policy: {'enabled' if config.image_generation_enabled else 'disabled'}",
                f"image_generation_chat: {'enabled' if image_settings['enabled'] else 'disabled'}",
                "image_generation_active: "
                f"{image_generation_queue.active if image_generation_queue else 0}",
                "image_generation_queued: "
                f"{image_generation_queue.pending if image_generation_queue else 0}",
                f"feedback_pending: {stats['unreacted_rows']}",
                f"feedback_reacted: {stats['reacted_rows']}",
                f"feedback_media: {stats['media_rows']}",
            ]
        ),
    )


async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context): return
    await _reply(update.effective_message, "\n".join(["tools scaffold:", *[f"- {tool.name}: {tool.description}" for tool in TOOL_REGISTRY.values()]]))


async def reaction_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, db, *_, media_cache = _services(context)
    reaction = update.message_reaction
    if not reaction or db.get_outbound_message(chat_id=reaction.chat.id, telegram_message_id=reaction.message_id) is None:
        return
    old_reaction = _to_dict_list(reaction.old_reaction)
    new_reaction = _to_dict_list(reaction.new_reaction)
    qualifies = bool(new_reaction)
    event_id = db.insert_reaction_event(
        chat_id=reaction.chat.id,
        telegram_message_id=reaction.message_id,
        user_id=reaction.user.id if getattr(reaction, "user", None) else None,
        old_reaction=old_reaction,
        new_reaction=new_reaction,
        qualifies_as_feedback=qualifies,
    )
    if qualifies:
        media_cache.promote(chat_id=reaction.chat.id, telegram_message_id=reaction.message_id, reaction_event_id=event_id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_id = getattr(update, "update_id", None)
    effective_chat = getattr(update, "effective_chat", None)
    effective_user = getattr(update, "effective_user", None)
    updater = getattr(context.application, "updater", None)
    logger.error(
        "bot handler error runtime_id=%s update_id=%s update_type=%s "
        "chat_id=%s user_id=%s polling_offset=%s error_type=%s",
        context.application.bot_data.get("runtime_id", "unknown"),
        update_id,
        type(update).__name__,
        getattr(effective_chat, "id", None),
        getattr(effective_user, "id", None),
        getattr(updater, "_last_update_id", None),
        type(context.error).__name__ if context.error else None,
        exc_info=context.error,
    )
