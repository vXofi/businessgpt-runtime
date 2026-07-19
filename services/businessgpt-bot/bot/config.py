from __future__ import annotations

import os
from dataclasses import dataclass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _parse_admin_ids(value: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as e:
            raise RuntimeError(f"invalid Telegram admin user id: {part}") from e
    if not ids:
        raise RuntimeError("BUSINESSGPT_BOT_ADMIN_USER_IDS must contain at least one id")
    return frozenset(ids)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_user_ids: frozenset[int]
    telegram_proxy_url: str | None
    api_url: str
    api_key: str
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

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            bot_token=_required_env("BUSINESSGPT_BOT_TOKEN"),
            admin_user_ids=_parse_admin_ids(_required_env("BUSINESSGPT_BOT_ADMIN_USER_IDS")),
            telegram_proxy_url=(
                os.environ.get("BUSINESSGPT_BOT_TELEGRAM_PROXY_URL", "").strip() or None
            ),
            api_url=os.environ.get(
                "BUSINESSGPT_API_URL",
                "http://businessgpt-api:8000/generate",
            ).strip(),
            api_key=_required_env("BUSINESSGPT_API_KEY"),
            db_path=os.environ.get("BUSINESSGPT_BOT_DB_PATH", "/data/bot.sqlite3").strip(),
            feedback_public_key_b64=_required_env("BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64"),
            media_cache_dir=os.environ.get(
                "BUSINESSGPT_BOT_MEDIA_CACHE_DIR", "/data/pending-media"
            ).strip(),
            media_cache_ttl_seconds=int(
                os.environ.get("BUSINESSGPT_BOT_MEDIA_CACHE_TTL_SECONDS", "86400")
            ),
            media_cache_max_bytes=int(
                os.environ.get("BUSINESSGPT_BOT_MEDIA_CACHE_MAX_BYTES", str(256 * 1024 * 1024))
            ),
            image_max_bytes=int(
                os.environ.get("BUSINESSGPT_BOT_IMAGE_MAX_BYTES", str(10 * 1024 * 1024))
            ),
            default_max_tokens=int(os.environ.get("BUSINESSGPT_BOT_MAX_TOKENS", "128")),
            default_temperature=float(os.environ.get("BUSINESSGPT_BOT_TEMPERATURE", "0.9")),
            default_top_p=float(os.environ.get("BUSINESSGPT_BOT_TOP_P", "0.9")),
            default_top_k=int(os.environ.get("BUSINESSGPT_BOT_TOP_K", "50")),
            default_repetition_penalty=float(
                os.environ.get("BUSINESSGPT_BOT_REPETITION_PENALTY", "1.2")
            ),
        )
