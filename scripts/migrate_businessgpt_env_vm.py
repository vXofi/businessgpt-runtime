#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import subprocess
import tempfile
from pathlib import Path


API_ENV_NAMES = (
    "BUSINESSGPT_MODEL_PATH",
    "BUSINESSGPT_MMPROJ_PATH",
    "BUSINESSGPT_API_KEY",
    "BUSINESSGPT_N_CTX",
    "BUSINESSGPT_CONTEXT_SAFETY_TOKENS",
    "BUSINESSGPT_N_THREADS",
    "BUSINESSGPT_N_GPU_LAYERS",
    "BUSINESSGPT_N_PARALLEL",
    "BUSINESSGPT_IMAGE_MAX_TOKENS",
    "BUSINESSGPT_STARTUP_TIMEOUT_SECONDS",
    "BUSINESSGPT_TOKENIZE_TIMEOUT_SECONDS",
    "BUSINESSGPT_GENERATION_TIMEOUT_SECONDS",
    "BUSINESSGPT_HEALTH_TIMEOUT_SECONDS",
    "BUSINESSGPT_MAX_REQUEST_BODY_BYTES",
    "BUSINESSGPT_IMAGE_DECODE_CONCURRENCY",
    "BUSINESSGPT_MODEL_MANIFEST_PATH",
    "BUSINESSGPT_ALLOW_UNVERIFIED_MODEL",
    "BUSINESSGPT_SYSTEM_PROMPT",
)
BOT_ENV_NAMES = (
    "BUSINESSGPT_BOT_TOKEN",
    "BUSINESSGPT_BOT_ADMIN_USER_IDS",
    "BUSINESSGPT_BOT_OWNER_USER_IDS",
    "BUSINESSGPT_BOT_ALLOWED_CHAT_IDS",
    "BUSINESSGPT_BOT_TELEGRAM_PROXY_URL",
    "BUSINESSGPT_API_URL",
    "BUSINESSGPT_BOT_ALLOWED_API_URLS",
    "BUSINESSGPT_API_KEY",
    "BUSINESSGPT_BOT_DB_PATH",
    "BUSINESSGPT_BOT_MEDIA_CACHE_DIR",
    "BUSINESSGPT_BOT_MEDIA_CACHE_TTL_SECONDS",
    "BUSINESSGPT_BOT_MEDIA_CACHE_MAX_BYTES",
    "BUSINESSGPT_BOT_IMAGE_MAX_BYTES",
    "BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64",
    "BUSINESSGPT_BOT_MAX_TOKENS",
    "BUSINESSGPT_BOT_TEMPERATURE",
    "BUSINESSGPT_BOT_TOP_P",
    "BUSINESSGPT_BOT_TOP_K",
    "BUSINESSGPT_BOT_REPETITION_PENALTY",
    "BUSINESSGPT_BOT_RECENT_CONTEXT_MESSAGES",
    "BUSINESSGPT_BOT_CONTEXT_MAX_CHATS",
    "BUSINESSGPT_BOT_CONTEXT_TTL_SECONDS",
    "BUSINESSGPT_BOT_MAX_CONCURRENT_GENERATIONS",
    "BUSINESSGPT_BOT_GENERATION_QUEUE_SIZE",
    "BUSINESSGPT_BOT_RATE_USER_REQUESTS",
    "BUSINESSGPT_BOT_RATE_CHAT_REQUESTS",
    "BUSINESSGPT_BOT_RATE_WINDOW_SECONDS",
    "BUSINESSGPT_BOT_PENDING_ACTION_TTL_SECONDS",
    "BUSINESSGPT_BOT_FEEDBACK_UNREACTED_TTL_SECONDS",
    "BUSINESSGPT_BOT_FEEDBACK_UNREACTED_MAX_ROWS",
    "BUSINESSGPT_BOT_FEEDBACK_UNREACTED_MAX_BYTES",
    "BUSINESSGPT_BOT_FEEDBACK_REACTED_MAX_ROWS",
    "BUSINESSGPT_BOT_FEEDBACK_REACTED_MAX_BYTES",
    "BUSINESSGPT_BOT_FEEDBACK_MEDIA_MAX_BYTES",
    "BUSINESSGPT_BOT_MAINTENANCE_INTERVAL_SECONDS",
    "BUSINESSGPT_BOT_DISK_FREE_WARNING_BYTES",
)


def container_env(name: str) -> dict[str, str]:
    raw = subprocess.check_output(
        ["docker", "inspect", name, "--format", "{{json .Config.Env}}"],
        text=True,
    )
    result: dict[str, str] = {}
    for item in json.loads(raw):
        key, separator, value = item.partition("=")
        if separator:
            result[key] = value
    return result


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            result[key] = value
    return result


def write_private_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} contains a newline and must move to a mounted file")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("# Managed by migrate_businessgpt_env_vm.py; mode 0600.\n")
            for key in sorted(values):
                output.write(f"{key}={values[key]}\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def detect_allowed_chat_ids(db_path: Path) -> list[int]:
    connection = sqlite3.connect(db_path)
    try:
        return [
            int(row[0])
            for row in connection.execute(
                "select distinct chat_id from chat_settings where chat_id < 0 order by chat_id"
            )
        ]
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate running BusinessGPT container settings to private env files."
    )
    parser.add_argument("--api-env", default="/opt/businessgpt/api.env", type=Path)
    parser.add_argument("--bot-env", default="/opt/businessgpt-bot/bot.env", type=Path)
    parser.add_argument(
        "--bot-db", default="/opt/businessgpt-bot/data/bot.sqlite3", type=Path
    )
    parser.add_argument("--allowed-chat-id", action="append", type=int)
    parser.add_argument("--rotate-api-key", action="store_true")
    args = parser.parse_args()

    api_source = {**container_env("businessgpt-api"), **read_env_file(args.api_env)}
    bot_source = {**container_env("businessgpt-bot"), **read_env_file(args.bot_env)}
    api = {name: api_source[name] for name in API_ENV_NAMES if api_source.get(name)}
    bot = {name: bot_source[name] for name in BOT_ENV_NAMES if bot_source.get(name)}

    owners = bot.get("BUSINESSGPT_BOT_OWNER_USER_IDS") or bot.get(
        "BUSINESSGPT_BOT_ADMIN_USER_IDS"
    )
    if not owners:
        raise SystemExit("could not recover BusinessGPT owner user IDs")
    bot["BUSINESSGPT_BOT_OWNER_USER_IDS"] = owners
    bot["BUSINESSGPT_BOT_ADMIN_USER_IDS"] = owners

    configured_chat_ids = [
        int(value.strip())
        for value in bot.get("BUSINESSGPT_BOT_ALLOWED_CHAT_IDS", "").split(",")
        if value.strip()
    ]
    allowed_chat_ids = (
        args.allowed_chat_id
        or configured_chat_ids
        or detect_allowed_chat_ids(args.bot_db)
    )
    if not allowed_chat_ids or any(chat_id >= 0 for chat_id in allowed_chat_ids):
        raise SystemExit("provide at least one negative --allowed-chat-id")
    bot["BUSINESSGPT_BOT_ALLOWED_CHAT_IDS"] = ",".join(
        str(chat_id) for chat_id in sorted(set(allowed_chat_ids))
    )

    api_url = bot.get(
        "BUSINESSGPT_API_URL", "http://businessgpt-api:8000/generate"
    )
    bot["BUSINESSGPT_API_URL"] = api_url
    bot.setdefault("BUSINESSGPT_BOT_ALLOWED_API_URLS", api_url)
    bot.setdefault("BUSINESSGPT_BOT_RECENT_CONTEXT_MESSAGES", "10")
    bot.setdefault("BUSINESSGPT_BOT_PENDING_ACTION_TTL_SECONDS", "900")
    bot.setdefault("BUSINESSGPT_BOT_GENERATION_QUEUE_SIZE", "8")
    bot.setdefault("BUSINESSGPT_BOT_FEEDBACK_UNREACTED_TTL_SECONDS", "604800")
    bot.setdefault("BUSINESSGPT_BOT_FEEDBACK_UNREACTED_MAX_ROWS", "5000")
    bot.setdefault("BUSINESSGPT_BOT_FEEDBACK_UNREACTED_MAX_BYTES", "536870912")
    bot.setdefault("BUSINESSGPT_BOT_FEEDBACK_REACTED_MAX_ROWS", "50000")
    bot.setdefault("BUSINESSGPT_BOT_FEEDBACK_REACTED_MAX_BYTES", "4294967296")
    bot.setdefault("BUSINESSGPT_BOT_FEEDBACK_MEDIA_MAX_BYTES", "2147483648")

    if args.rotate_api_key:
        api_key = secrets.token_urlsafe(48)
    else:
        api_key = api.get("BUSINESSGPT_API_KEY") or bot.get("BUSINESSGPT_API_KEY")
    if not api_key:
        raise SystemExit("could not recover BUSINESSGPT_API_KEY")
    api["BUSINESSGPT_API_KEY"] = api_key
    bot["BUSINESSGPT_API_KEY"] = api_key

    write_private_env(args.api_env, api)
    write_private_env(args.bot_env, bot)
    print(f"wrote private API env: {args.api_env}")
    print(f"wrote private bot env: {args.bot_env}")
    print("API key synchronized" + (" and rotated" if args.rotate_api_key else ""))
    print("secret values were not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
