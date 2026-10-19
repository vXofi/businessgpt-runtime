from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import PurePosixPath

from .image_generation_client import normalize_image_api_url
from .validation import is_runpod_url, normalize_api_url, validate_generation_settings


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _parse_ids(value: str, *, name: str, require_negative: bool = False) -> frozenset[int]:
    ids: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as e:
            raise RuntimeError(f"invalid integer in {name}: {part}") from e
        if require_negative and int(part) >= 0:
            raise RuntimeError(f"{name} must contain group or supergroup chat IDs")
    if not ids:
        raise RuntimeError(f"{name} must contain at least one id")
    return frozenset(ids)


def _positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "true" if default else "false").strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name} must be true or false")
    return value == "true"


def _persistent_data_path(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    path = PurePosixPath(value)
    if not path.is_absolute() or path == PurePosixPath("/data") or not path.is_relative_to("/data"):
        raise RuntimeError(f"{name} must be an absolute path below /data")
    return str(path)


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    allowed_api_urls: frozenset[str]
    telegram_proxy_url: str | None
    api_url: str
    api_key: str
    image_generation_enabled: bool
    image_api_url: str
    image_api_key: str | None
    image_generation_timeout_seconds: int
    image_generation_queue_size: int
    db_path: str
    feedback_public_key_b64: str
    media_cache_dir: str
    media_cache_ttl_seconds: int
    media_cache_max_bytes: int
    image_max_bytes: int
    default_max_tokens: int
    default_temperature: float
    default_top_p: float
    default_top_k: int
    default_repetition_penalty: float
    default_recent_context_messages: int
    context_cache_max_chats: int
    context_cache_ttl_seconds: int
    max_concurrent_generations: int
    generation_queue_size: int
    rate_limit_user_requests: int
    rate_limit_chat_requests: int
    rate_limit_window_seconds: int
    pending_action_ttl_seconds: int
    feedback_unreacted_ttl_seconds: int
    feedback_unreacted_max_rows: int
    feedback_unreacted_max_bytes: int
    feedback_reacted_max_rows: int
    feedback_reacted_max_bytes: int
    feedback_media_max_bytes: int
    maintenance_interval_seconds: int
    disk_free_warning_bytes: int
    runpod_api_key: str | None = None
    runpod_timeout_seconds: int = 600
    runpod_proxy_url: str | None = None

    @property
    def admin_user_ids(self) -> frozenset[int]:
        """Compatibility for older helpers and one-release environment migration."""
        return self.owner_user_ids

    @classmethod
    def from_env(cls) -> "Config":
        image_generation_enabled = _bool_env(
            "BUSINESSGPT_IMAGE_GENERATION_ENABLED", False
        )
        api_url = normalize_api_url(
            os.environ.get(
                "BUSINESSGPT_API_URL",
                "http://businessgpt-api:8000/generate",
            )
        )
        allowed_api_values = os.environ.get(
            "BUSINESSGPT_BOT_ALLOWED_API_URLS", api_url
        )
        allowed_api_urls = frozenset(
            normalize_api_url(part)
            for part in allowed_api_values.split(",")
            if part.strip()
        )
        if api_url not in allowed_api_urls:
            raise RuntimeError("BUSINESSGPT_API_URL must be in BUSINESSGPT_BOT_ALLOWED_API_URLS")
        uses_runpod = any(is_runpod_url(url) for url in allowed_api_urls)
        uses_direct = any(not is_runpod_url(url) for url in allowed_api_urls)
        runpod_timeout = _positive_int("BUSINESSGPT_RUNPOD_TIMEOUT_SECONDS", 600)
        if not 180 <= runpod_timeout <= 1800:
            raise RuntimeError("BUSINESSGPT_RUNPOD_TIMEOUT_SECONDS must be between 180 and 1800")
        image_max_bytes = _positive_int("BUSINESSGPT_BOT_IMAGE_MAX_BYTES", 10 * 1024 * 1024)
        if uses_runpod:
            image_max_bytes = min(image_max_bytes, 6 * 1024 * 1024)
        owner_values = os.environ.get("BUSINESSGPT_BOT_OWNER_USER_IDS", "").strip()
        if not owner_values:
            owner_values = _required_env("BUSINESSGPT_BOT_ADMIN_USER_IDS")
        defaults = validate_generation_settings(
            recent_context_messages=os.environ.get(
                "BUSINESSGPT_BOT_RECENT_CONTEXT_MESSAGES", "10"
            ),
            max_tokens=os.environ.get("BUSINESSGPT_BOT_MAX_TOKENS", "128"),
            temperature=os.environ.get("BUSINESSGPT_BOT_TEMPERATURE", "0.9"),
            top_p=os.environ.get("BUSINESSGPT_BOT_TOP_P", "0.9"),
            top_k=os.environ.get("BUSINESSGPT_BOT_TOP_K", "50"),
            repetition_penalty=os.environ.get(
                "BUSINESSGPT_BOT_REPETITION_PENALTY", "1.2"
            ),
        )
        return cls(
            bot_token=_required_env("BUSINESSGPT_BOT_TOKEN"),
            owner_user_ids=_parse_ids(
                owner_values, name="BUSINESSGPT_BOT_OWNER_USER_IDS"
            ),
            allowed_chat_ids=_parse_ids(
                _required_env("BUSINESSGPT_BOT_ALLOWED_CHAT_IDS"),
                name="BUSINESSGPT_BOT_ALLOWED_CHAT_IDS",
                require_negative=True,
            ),
            allowed_api_urls=allowed_api_urls,
            telegram_proxy_url=(
                os.environ.get("BUSINESSGPT_BOT_TELEGRAM_PROXY_URL", "").strip() or None
            ),
            api_url=api_url,
            api_key=_required_env("BUSINESSGPT_API_KEY") if uses_direct else "",
            runpod_api_key=_required_env("BUSINESSGPT_RUNPOD_API_KEY") if uses_runpod else None,
            runpod_timeout_seconds=runpod_timeout,
            runpod_proxy_url=(
                os.environ.get(
                    "BUSINESSGPT_RUNPOD_PROXY_URL",
                    os.environ.get("BUSINESSGPT_BOT_TELEGRAM_PROXY_URL", ""),
                ).strip() or None
            ),
            image_generation_enabled=image_generation_enabled,
            image_api_url=normalize_image_api_url(
                os.environ.get(
                    "BUSINESSGPT_IMAGE_API_URL",
                    "https://anogram.ru/v1/images/generations/stream",
                )
            ),
            image_api_key=(
                _required_env("BUSINESSGPT_IMAGE_API_KEY")
                if image_generation_enabled
                else None
            ),
            image_generation_timeout_seconds=_positive_int(
                "BUSINESSGPT_BOT_IMAGE_GENERATION_TIMEOUT_SECONDS", 120
            ),
            image_generation_queue_size=_positive_int(
                "BUSINESSGPT_BOT_IMAGE_GENERATION_QUEUE_SIZE", 4
            ),
            db_path=_persistent_data_path(
                "BUSINESSGPT_BOT_DB_PATH", "/data/bot.sqlite3"
            ),
            feedback_public_key_b64=_required_env("BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64"),
            media_cache_dir=_persistent_data_path(
                "BUSINESSGPT_BOT_MEDIA_CACHE_DIR", "/data/pending-media"
            ),
            media_cache_ttl_seconds=_positive_int(
                "BUSINESSGPT_BOT_MEDIA_CACHE_TTL_SECONDS", 86400
            ),
            media_cache_max_bytes=_positive_int(
                "BUSINESSGPT_BOT_MEDIA_CACHE_MAX_BYTES", 256 * 1024 * 1024
            ),
            image_max_bytes=image_max_bytes,
            default_max_tokens=int(defaults["max_tokens"]),
            default_temperature=float(defaults["temperature"]),
            default_top_p=float(defaults["top_p"]),
            default_top_k=int(defaults["top_k"]),
            default_repetition_penalty=float(defaults["repetition_penalty"]),
            default_recent_context_messages=int(defaults["recent_context_messages"]),
            context_cache_max_chats=_positive_int("BUSINESSGPT_BOT_CONTEXT_MAX_CHATS", 64),
            context_cache_ttl_seconds=_positive_int(
                "BUSINESSGPT_BOT_CONTEXT_TTL_SECONDS", 21600
            ),
            max_concurrent_generations=_positive_int(
                "BUSINESSGPT_BOT_MAX_CONCURRENT_GENERATIONS", 1
            ),
            generation_queue_size=_positive_int(
                "BUSINESSGPT_BOT_GENERATION_QUEUE_SIZE", 8
            ),
            rate_limit_user_requests=_positive_int(
                "BUSINESSGPT_BOT_RATE_USER_REQUESTS", 3
            ),
            rate_limit_chat_requests=_positive_int(
                "BUSINESSGPT_BOT_RATE_CHAT_REQUESTS", 10
            ),
            rate_limit_window_seconds=_positive_int(
                "BUSINESSGPT_BOT_RATE_WINDOW_SECONDS", 60
            ),
            pending_action_ttl_seconds=_positive_int(
                "BUSINESSGPT_BOT_PENDING_ACTION_TTL_SECONDS", 900
            ),
            feedback_unreacted_ttl_seconds=_positive_int(
                "BUSINESSGPT_BOT_FEEDBACK_UNREACTED_TTL_SECONDS", 604800
            ),
            feedback_unreacted_max_rows=_positive_int(
                "BUSINESSGPT_BOT_FEEDBACK_UNREACTED_MAX_ROWS", 5000
            ),
            feedback_unreacted_max_bytes=_positive_int(
                "BUSINESSGPT_BOT_FEEDBACK_UNREACTED_MAX_BYTES", 536870912
            ),
            feedback_reacted_max_rows=_positive_int(
                "BUSINESSGPT_BOT_FEEDBACK_REACTED_MAX_ROWS", 50000
            ),
            feedback_reacted_max_bytes=_positive_int(
                "BUSINESSGPT_BOT_FEEDBACK_REACTED_MAX_BYTES", 4294967296
            ),
            feedback_media_max_bytes=_positive_int(
                "BUSINESSGPT_BOT_FEEDBACK_MEDIA_MAX_BYTES", 2147483648
            ),
            maintenance_interval_seconds=_positive_int(
                "BUSINESSGPT_BOT_MAINTENANCE_INTERVAL_SECONDS", 3600
            ),
            disk_free_warning_bytes=_positive_int(
                "BUSINESSGPT_BOT_DISK_FREE_WARNING_BYTES", 5368709120
            ),
        )
