from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Optional

from .validation import (
    ADMISSION_RANGES,
    BOOLEAN_IMAGE_GENERATION_FIELDS,
    GENERATION_RANGES,
    IMAGE_GENERATION_RANGES,
    normalize_api_url,
    validate_admission_settings,
    validate_admission_value,
    validate_generation_settings,
    validate_generation_value,
    validate_image_generation_settings,
    validate_image_generation_value,
)


NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def validate_name(name: str) -> str:
    name = name.strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("name must be 1-64 chars: letters, digits, dot, underscore, dash")
    return name


class BotDB:
    def __init__(
        self,
        path: str,
        *,
        default_recent_context_messages: int = 10,
        default_rate_limit_user_requests: int = 3,
        default_rate_limit_chat_requests: int = 10,
    ) -> None:
        self.path = path
        self.default_recent_context_messages = int(
            validate_generation_value(
                "recent_context_messages", default_recent_context_messages
            )
        )
        admission_defaults = validate_admission_settings(
            rate_limit_user_requests=default_rate_limit_user_requests,
            rate_limit_chat_requests=default_rate_limit_chat_requests,
        )
        self.default_rate_limit_user_requests = admission_defaults[
            "rate_limit_user_requests"
        ]
        self.default_rate_limit_chat_requests = admission_defaults[
            "rate_limit_chat_requests"
        ]
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("pragma foreign_keys = on")
            self._conn.execute("pragma journal_mode = wal")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                create table if not exists prompt_presets (
                    id integer primary key,
                    name text not null unique,
                    text text not null,
                    created_by integer,
                    created_at text not null,
                    updated_at text not null,
                    deleted_at text
                );

                create table if not exists prompt_presets_v2 (
                    id integer primary key,
                    chat_id integer not null,
                    name text not null,
                    text text not null,
                    created_by integer,
                    created_at text not null,
                    updated_at text not null,
                    deleted_at text,
                    unique(chat_id, name)
                );

                create table if not exists schema_metadata (
                    key text primary key,
                    value text not null
                );

                create table if not exists model_profiles (
                    id integer primary key,
                    name text not null unique,
                    api_url text not null,
                    model_label text not null,
                    max_tokens integer not null,
                    temperature real not null,
                    top_p real not null,
                    repetition_penalty real not null,
                    created_at text not null,
                    updated_at text not null,
                    deleted_at text
                );

                create table if not exists chat_settings (
                    chat_id integer primary key,
                    active_prompt_id integer references prompt_presets(id),
                    active_model_profile_id integer references model_profiles(id)
                );

                create table if not exists outbound_messages (
                    id integer primary key,
                    chat_id integer not null,
                    telegram_message_id integer not null,
                    prompt_preset_id integer references prompt_presets(id),
                    model_profile_id integer references model_profiles(id),
                    prompt_hash text,
                    created_at text not null,
                    unique(chat_id, telegram_message_id)
                );

                create table if not exists reaction_events (
                    id integer primary key,
                    chat_id integer not null,
                    telegram_message_id integer not null,
                    user_id integer,
                    old_reaction_json text not null,
                    new_reaction_json text not null,
                    created_at text not null
                );

                create table if not exists encrypted_feedback_samples (
                    id integer primary key,
                    chat_id integer not null,
                    telegram_message_id integer not null,
                    reaction_event_id integer references reaction_events(id),
                    payload_json_sha256 text not null,
                    encrypted_payload_b64 text not null,
                    created_at text not null
                );

                create table if not exists pending_admin_actions (
                    id integer primary key,
                    chat_id integer not null,
                    user_id integer not null,
                    action text not null,
                    target_name text not null,
                    created_at text not null,
                    unique(chat_id, user_id)
                );

                create table if not exists admin_sessions (
                    user_id integer primary key,
                    target_chat_id integer not null,
                    expires_at text not null
                );

                create table if not exists pending_feedback_media (
                    chat_id integer not null,
                    telegram_message_id integer not null,
                    encrypted_path text not null,
                    source_sha256 text not null,
                    mime_type text not null,
                    original_filename text,
                    encrypted_bytes integer not null,
                    expires_at text not null,
                    created_at text not null,
                    primary key(chat_id, telegram_message_id)
                );

                create table if not exists encrypted_feedback_media (
                    id integer primary key,
                    chat_id integer not null,
                    telegram_message_id integer not null,
                    reaction_event_id integer not null references reaction_events(id),
                    source_sha256 text not null,
                    mime_type text not null,
                    original_filename text,
                    encrypted_payload_b64 text not null,
                    created_at text not null,
                    unique(chat_id, telegram_message_id)
                );
                """
            )
            self._ensure_column("model_profiles", "top_k", "integer not null default 50")
            self._ensure_column(
                "chat_settings", "recent_context_messages", "integer not null default 0"
            )
            self._ensure_column("chat_settings", "max_tokens", "integer")
            self._ensure_column("chat_settings", "temperature", "real")
            self._ensure_column("chat_settings", "top_p", "real")
            self._ensure_column("chat_settings", "top_k", "integer")
            self._ensure_column("chat_settings", "repetition_penalty", "real")
            self._ensure_column(
                "chat_settings", "rate_limit_user_requests", "integer"
            )
            self._ensure_column(
                "chat_settings", "rate_limit_chat_requests", "integer"
            )
            self._ensure_column(
                "chat_settings", "image_generation_enabled", "integer not null default 1"
            )
            self._ensure_column(
                "chat_settings", "image_width", "integer not null default 512"
            )
            self._ensure_column(
                "chat_settings", "image_height", "integer not null default 512"
            )
            self._ensure_column(
                "chat_settings", "image_steps", "integer not null default 4"
            )
            self._ensure_column(
                "chat_settings", "image_guidance_scale", "real not null default 1.0"
            )
            self._ensure_column(
                "chat_settings", "image_auto_translate", "integer not null default 1"
            )
            self._ensure_column("chat_settings", "active_prompt_v2_id", "integer")
            self._ensure_column("outbound_messages", "prompt_preset_v2_id", "integer")
            self._ensure_column("pending_admin_actions", "target_chat_id", "integer")
            self._ensure_column(
                "encrypted_feedback_samples",
                "schema_version",
                "integer not null default 1",
            )
            self._ensure_column(
                "encrypted_feedback_samples",
                "encrypted_bytes",
                "integer not null default 0",
            )
            self._conn.execute(
                "update encrypted_feedback_samples set encrypted_bytes = length(encrypted_payload_b64) "
                "where encrypted_bytes = 0"
            )
            context_migration = self._conn.execute(
                "select value from schema_metadata where key = 'context_default_v2'"
            ).fetchone()
            if context_migration is None:
                self._conn.execute(
                    "update chat_settings set recent_context_messages = ? "
                    "where recent_context_messages = 0",
                    (self.default_recent_context_messages,),
                )
                self._conn.execute(
                    "insert into schema_metadata(key, value) values ('context_default_v2', ?)",
                    (str(self.default_recent_context_messages),),
                )
            self._conn.commit()

    def migrate_legacy_prompts(self, default_chat_id: int) -> None:
        """Copy v1 global prompts into chat-scoped storage without breaking old exports."""
        with self._lock:
            rows = self._conn.execute(
                "select * from prompt_presets where deleted_at is null order by id"
            ).fetchall()
            for row in rows:
                chat_rows = self._conn.execute(
                    "select chat_id from chat_settings where active_prompt_id = ?",
                    (row["id"],),
                ).fetchall()
                chat_ids = {int(item["chat_id"]) for item in chat_rows if int(item["chat_id"]) < 0}
                if not chat_ids:
                    chat_ids = {default_chat_id}
                for chat_id in chat_ids:
                    self._conn.execute(
                        """
                        insert or ignore into prompt_presets_v2(
                            chat_id, name, text, created_by, created_at, updated_at, deleted_at
                        ) values (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chat_id,
                            row["name"],
                            row["text"],
                            row["created_by"],
                            row["created_at"],
                            row["updated_at"],
                            row["deleted_at"],
                        ),
                    )
                    copied = self._conn.execute(
                        "select id from prompt_presets_v2 where chat_id = ? and name = ?",
                        (chat_id, row["name"]),
                    ).fetchone()
                    self._conn.execute(
                        "update chat_settings set active_prompt_v2_id = ? "
                        "where chat_id = ? and active_prompt_id = ?",
                        (copied["id"], chat_id, row["id"]),
                    )
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"] for row in self._conn.execute(f"pragma table_info({table})").fetchall()
        }
        if column not in columns:
            self._conn.execute(f"alter table {table} add column {column} {definition}")

    def seed_default_model_profile(
        self,
        *,
        api_url: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        repetition_penalty: float,
    ) -> None:
        if self.get_model_by_name("default") is not None:
            return
        self.upsert_model_profile(
            name="default",
            api_url=api_url,
            model_label="default",
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
        )

    def _fetchone(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def _fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def list_prompts(self, chat_id: int) -> list[sqlite3.Row]:
        return self._fetchall(
            "select * from prompt_presets_v2 where chat_id = ? and deleted_at is null order by name",
            (chat_id,),
        )

    def get_prompt_by_name(self, chat_id: int, name: str) -> Optional[sqlite3.Row]:
        return self._fetchone(
            "select * from prompt_presets_v2 where chat_id = ? and name = ? and deleted_at is null",
            (chat_id, validate_name(name)),
        )

    def get_prompt_by_id(self, chat_id: int, preset_id: int | None) -> Optional[sqlite3.Row]:
        if preset_id is None:
            return None
        return self._fetchone(
            "select * from prompt_presets_v2 where chat_id = ? and id = ? and deleted_at is null",
            (chat_id, preset_id),
        )

    def upsert_prompt(
        self, *, chat_id: int, name: str, text: str, created_by: int | None
    ) -> sqlite3.Row:
        name = validate_name(name)
        text = text.strip()
        if not text:
            raise ValueError("prompt text must not be blank")
        now = utc_now()
        with self._lock:
            existing = self._conn.execute(
                "select id from prompt_presets_v2 where chat_id = ? and name = ?",
                (chat_id, name),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """
                    update prompt_presets_v2
                    set text = ?, updated_at = ?, deleted_at = null
                    where chat_id = ? and name = ?
                    """,
                    (text, now, chat_id, name),
                )
            else:
                self._conn.execute(
                    """
                    insert into prompt_presets_v2(
                        chat_id, name, text, created_by, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (chat_id, name, text, created_by, now, now),
                )
            self._conn.commit()
        row = self.get_prompt_by_name(chat_id, name)
        assert row is not None
        return row

    def soft_delete_prompt(self, chat_id: int, name: str) -> bool:
        now = utc_now()
        with self._lock:
            cur = self._conn.execute(
                "update prompt_presets_v2 set deleted_at = ?, updated_at = ? "
                "where chat_id = ? and name = ? and deleted_at is null",
                (now, now, chat_id, validate_name(name)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_chat_settings(self, chat_id: int) -> sqlite3.Row:
        with self._lock:
            self._conn.execute(
                "insert or ignore into chat_settings(chat_id, recent_context_messages) values (?, ?)",
                (chat_id, self.default_recent_context_messages),
            )
            self._conn.commit()
        row = self._fetchone("select * from chat_settings where chat_id = ?", (chat_id,))
        assert row is not None
        return row

    def set_active_prompt(self, chat_id: int, prompt_id: int | None) -> None:
        with self._lock:
            self._conn.execute(
                """
                insert into chat_settings(chat_id, active_prompt_v2_id)
                values (?, ?)
                on conflict(chat_id) do update set active_prompt_v2_id = excluded.active_prompt_v2_id
                """,
                (chat_id, prompt_id),
            )
            self._conn.commit()

    def set_chat_setting(self, chat_id: int, field: str, value: Any) -> None:
        if field in GENERATION_RANGES:
            value = validate_generation_value(field, value)
        elif field in ADMISSION_RANGES:
            value = validate_admission_value(field, value)
        elif field in IMAGE_GENERATION_RANGES or field in BOOLEAN_IMAGE_GENERATION_FIELDS:
            value = validate_image_generation_value(field, value)
        else:
            raise ValueError(f"unsupported chat setting: {field}")
        with self._lock:
            self._conn.execute(
                f"insert into chat_settings(chat_id, {field}) values (?, ?) "
                f"on conflict(chat_id) do update set {field} = excluded.{field}",
                (chat_id, value),
            )
            self._conn.commit()

    def set_image_generation_settings(
        self, chat_id: int, **settings: Any
    ) -> None:
        allowed_fields = set(IMAGE_GENERATION_RANGES) | set(
            BOOLEAN_IMAGE_GENERATION_FIELDS
        )
        if not settings:
            raise ValueError("at least one image generation setting is required")
        unsupported = set(settings) - allowed_fields
        if unsupported:
            raise ValueError(
                f"unsupported image generation setting: {sorted(unsupported)[0]}"
            )
        values = validate_image_generation_settings(**settings)
        fields = list(values)
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        assignments = ", ".join(
            f"{field} = excluded.{field}" for field in fields
        )
        with self._lock:
            self._conn.execute(
                f"insert into chat_settings(chat_id, {columns}) "
                f"values (?, {placeholders}) "
                f"on conflict(chat_id) do update set {assignments}",
                (chat_id, *(values[field] for field in fields)),
            )
            self._conn.commit()

    def get_effective_generation_settings(self, chat_id: int) -> dict[str, Any]:
        chat = self.get_chat_settings(chat_id)
        model = self.get_active_model_profile(chat_id)
        return validate_generation_settings(**{
            "recent_context_messages": int(chat["recent_context_messages"]),
            "max_tokens": int(chat["max_tokens"] if chat["max_tokens"] is not None else model["max_tokens"]),
            "temperature": float(chat["temperature"] if chat["temperature"] is not None else model["temperature"]),
            "top_p": float(chat["top_p"] if chat["top_p"] is not None else model["top_p"]),
            "top_k": int(chat["top_k"] if chat["top_k"] is not None else model["top_k"]),
            "repetition_penalty": float(
                chat["repetition_penalty"]
                if chat["repetition_penalty"] is not None
                else model["repetition_penalty"]
            ),
        })

    def get_effective_admission_settings(self, chat_id: int) -> dict[str, int]:
        chat = self.get_chat_settings(chat_id)
        return validate_admission_settings(
            rate_limit_user_requests=(
                chat["rate_limit_user_requests"]
                if chat["rate_limit_user_requests"] is not None
                else self.default_rate_limit_user_requests
            ),
            rate_limit_chat_requests=(
                chat["rate_limit_chat_requests"]
                if chat["rate_limit_chat_requests"] is not None
                else self.default_rate_limit_chat_requests
            ),
        )

    def get_image_generation_settings(self, chat_id: int) -> dict[str, Any]:
        chat = self.get_chat_settings(chat_id)
        values = validate_image_generation_settings(
            image_generation_enabled=chat["image_generation_enabled"],
            image_width=chat["image_width"],
            image_height=chat["image_height"],
            image_steps=chat["image_steps"],
            image_guidance_scale=chat["image_guidance_scale"],
            image_auto_translate=chat["image_auto_translate"],
        )
        return {
            "enabled": bool(values["image_generation_enabled"]),
            "width": int(values["image_width"]),
            "height": int(values["image_height"]),
            "steps": int(values["image_steps"]),
            "guidance_scale": float(values["image_guidance_scale"]),
            "auto_translate": bool(values["image_auto_translate"]),
        }

    def get_active_prompt(self, chat_id: int) -> Optional[sqlite3.Row]:
        settings = self.get_chat_settings(chat_id)
        return self.get_prompt_by_id(chat_id, settings["active_prompt_v2_id"])

    def list_model_profiles(self) -> list[sqlite3.Row]:
        return self._fetchall(
            "select * from model_profiles where deleted_at is null order by name"
        )

    def get_model_by_name(self, name: str) -> Optional[sqlite3.Row]:
        return self._fetchone(
            "select * from model_profiles where name = ? and deleted_at is null",
            (validate_name(name),),
        )

    def get_model_by_id(self, profile_id: int | None) -> Optional[sqlite3.Row]:
        if profile_id is None:
            return None
        return self._fetchone(
            "select * from model_profiles where id = ? and deleted_at is null",
            (profile_id,),
        )

    def upsert_model_profile(
        self,
        *,
        name: str,
        api_url: str,
        model_label: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        repetition_penalty: float,
    ) -> sqlite3.Row:
        name = validate_name(name)
        api_url = normalize_api_url(api_url)
        values = validate_generation_settings(
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
        )
        now = utc_now()
        with self._lock:
            existing = self._conn.execute(
                "select id from model_profiles where name = ?",
                (name,),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """
                    update model_profiles
                    set api_url = ?, model_label = ?, max_tokens = ?, temperature = ?,
                        top_p = ?, top_k = ?, repetition_penalty = ?, updated_at = ?, deleted_at = null
                    where name = ?
                    """,
                    (
                        api_url,
                        model_label,
                        values["max_tokens"],
                        values["temperature"],
                        values["top_p"],
                        values["top_k"],
                        values["repetition_penalty"],
                        now,
                        name,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    insert into model_profiles(
                        name, api_url, model_label, max_tokens, temperature, top_p, top_k,
                        repetition_penalty, created_at, updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        api_url,
                        model_label,
                        values["max_tokens"],
                        values["temperature"],
                        values["top_p"],
                        values["top_k"],
                        values["repetition_penalty"],
                        now,
                        now,
                    ),
                )
            self._conn.commit()
        row = self.get_model_by_name(name)
        assert row is not None
        return row

    def soft_delete_model_profile(self, name: str) -> bool:
        now = utc_now()
        with self._lock:
            cur = self._conn.execute(
                "update model_profiles set deleted_at = ?, updated_at = ? where name = ? and deleted_at is null",
                (now, now, validate_name(name)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def set_active_model_profile(self, chat_id: int, profile_id: int | None) -> None:
        with self._lock:
            self._conn.execute(
                """
                insert into chat_settings(chat_id, active_model_profile_id)
                values (?, ?)
                on conflict(chat_id) do update set active_model_profile_id = excluded.active_model_profile_id
                """,
                (chat_id, profile_id),
            )
            self._conn.commit()

    def get_active_model_profile(self, chat_id: int) -> sqlite3.Row:
        settings = self.get_chat_settings(chat_id)
        row = self.get_model_by_id(settings["active_model_profile_id"])
        if row is not None:
            return row
        default = self.get_model_by_name("default")
        if default is None:
            raise RuntimeError("default model profile is missing")
        return default

    def set_pending_action(
        self,
        *,
        chat_id: int,
        user_id: int,
        target_chat_id: int,
        action: str,
        target_name: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                insert into pending_admin_actions(
                    chat_id, user_id, target_chat_id, action, target_name, created_at
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(chat_id, user_id) do update set
                    target_chat_id = excluded.target_chat_id,
                    action = excluded.action,
                    target_name = excluded.target_name,
                    created_at = excluded.created_at
                """,
                (
                    chat_id,
                    user_id,
                    target_chat_id,
                    action,
                    validate_name(target_name),
                    utc_now(),
                ),
            )
            self._conn.commit()

    def delete_pending_action(self, *, chat_id: int, user_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "delete from pending_admin_actions where chat_id = ? and user_id = ?",
                (chat_id, user_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def get_pending_action(
        self, *, chat_id: int, user_id: int, ttl_seconds: int = 900
    ) -> Optional[sqlite3.Row]:
        row = self._fetchone(
            "select * from pending_admin_actions where chat_id = ? and user_id = ?",
            (chat_id, user_id),
        )
        if row is None:
            return None
        cutoff = (datetime.now(UTC) - timedelta(seconds=ttl_seconds)).replace(
            microsecond=0
        ).isoformat()
        if row["created_at"] <= cutoff:
            self.delete_pending_action(chat_id=chat_id, user_id=user_id)
            return None
        return row

    def set_admin_session(self, *, user_id: int, target_chat_id: int, ttl_hours: int = 24) -> None:
        expires_at = (datetime.now(UTC) + timedelta(hours=ttl_hours)).replace(microsecond=0).isoformat()
        with self._lock:
            self._conn.execute(
                """
                insert into admin_sessions(user_id, target_chat_id, expires_at) values (?, ?, ?)
                on conflict(user_id) do update set
                    target_chat_id = excluded.target_chat_id, expires_at = excluded.expires_at
                """,
                (user_id, target_chat_id, expires_at),
            )
            self._conn.commit()

    def get_admin_session_chat(self, user_id: int) -> int | None:
        row = self._fetchone(
            "select target_chat_id, expires_at from admin_sessions where user_id = ?",
            (user_id,),
        )
        if row is None:
            return None
        if row["expires_at"] <= utc_now():
            with self._lock:
                self._conn.execute("delete from admin_sessions where user_id = ?", (user_id,))
                self._conn.commit()
            return None
        return int(row["target_chat_id"])

    def insert_outbound_message(
        self,
        *,
        chat_id: int,
        telegram_message_id: int,
        prompt_preset_id: int | None,
        model_profile_id: int | None,
        prompt_hash: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                insert or replace into outbound_messages(
                    chat_id, telegram_message_id, prompt_preset_v2_id, model_profile_id,
                    prompt_hash, created_at
                )
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    telegram_message_id,
                    prompt_preset_id,
                    model_profile_id,
                    prompt_hash,
                    utc_now(),
                ),
            )
            self._conn.commit()

    def get_outbound_message(
        self,
        *,
        chat_id: int,
        telegram_message_id: int,
    ) -> Optional[sqlite3.Row]:
        return self._fetchone(
            """
            select * from outbound_messages
            where chat_id = ? and telegram_message_id = ?
            """,
            (chat_id, telegram_message_id),
        )

    def insert_encrypted_feedback_sample(
        self,
        *,
        chat_id: int,
        telegram_message_id: int,
        payload: dict[str, Any],
        encrypted_payload_b64: str,
        schema_version: int = 2,
    ) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._lock:
            self._conn.execute(
                """
                insert into encrypted_feedback_samples(
                    chat_id, telegram_message_id, payload_json_sha256,
                    encrypted_payload_b64, encrypted_bytes, schema_version, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    telegram_message_id,
                    payload_hash,
                    encrypted_payload_b64,
                    len(encrypted_payload_b64),
                    schema_version,
                    utc_now(),
                ),
            )
            self._conn.commit()

    def insert_reaction_event(
        self,
        *,
        chat_id: int,
        telegram_message_id: int,
        user_id: int | None,
        old_reaction: Any,
        new_reaction: Any,
        qualifies_as_feedback: bool = True,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                insert into reaction_events(
                    chat_id, telegram_message_id, user_id, old_reaction_json,
                    new_reaction_json, created_at
                )
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    telegram_message_id,
                    user_id,
                    json.dumps(old_reaction, ensure_ascii=False, sort_keys=True),
                    json.dumps(new_reaction, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            reaction_event_id = int(cur.lastrowid)
            if qualifies_as_feedback:
                self._conn.execute(
                    """
                    update encrypted_feedback_samples
                    set reaction_event_id = ?
                    where chat_id = ? and telegram_message_id = ? and reaction_event_id is null
                    """,
                    (reaction_event_id, chat_id, telegram_message_id),
                )
            self._conn.commit()
            return reaction_event_id

    def insert_pending_feedback_media(
        self,
        *,
        chat_id: int,
        telegram_message_id: int,
        encrypted_path: str,
        source_sha256: str,
        mime_type: str,
        original_filename: str | None,
        encrypted_bytes: int,
        expires_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                insert or replace into pending_feedback_media(
                    chat_id, telegram_message_id, encrypted_path, source_sha256,
                    mime_type, original_filename, encrypted_bytes, expires_at, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id, telegram_message_id, encrypted_path, source_sha256,
                    mime_type, original_filename, encrypted_bytes, expires_at, utc_now(),
                ),
            )
            self._conn.commit()

    def list_pending_feedback_media(self) -> list[sqlite3.Row]:
        return self._fetchall("select * from pending_feedback_media order by created_at")

    def delete_pending_feedback_media(self, chat_id: int, telegram_message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "delete from pending_feedback_media where chat_id = ? and telegram_message_id = ?",
                (chat_id, telegram_message_id),
            )
            self._conn.commit()

    def promote_pending_feedback_media(
        self,
        *,
        chat_id: int,
        telegram_message_id: int,
        reaction_event_id: int,
        encrypted_payload_b64: str,
    ) -> bool:
        pending = self._fetchone(
            "select * from pending_feedback_media where chat_id = ? and telegram_message_id = ?",
            (chat_id, telegram_message_id),
        )
        if pending is None:
            return False
        with self._lock:
            self._conn.execute(
                """
                insert or ignore into encrypted_feedback_media(
                    chat_id, telegram_message_id, reaction_event_id, source_sha256,
                    mime_type, original_filename, encrypted_payload_b64, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id, telegram_message_id, reaction_event_id,
                    pending["source_sha256"], pending["mime_type"],
                    pending["original_filename"], encrypted_payload_b64, utc_now(),
                ),
            )
            self._conn.execute(
                "delete from pending_feedback_media where chat_id = ? and telegram_message_id = ?",
                (chat_id, telegram_message_id),
            )
            self._conn.commit()
        return True

    def get_encrypted_feedback_media(
        self,
        *,
        chat_id: int,
        telegram_message_id: int,
    ) -> Optional[sqlite3.Row]:
        return self._fetchone(
            """
            select * from encrypted_feedback_media
            where chat_id = ? and telegram_message_id = ?
            """,
            (chat_id, telegram_message_id),
        )

    @staticmethod
    def _ids_outside_caps(
        rows: list[sqlite3.Row], *, max_rows: int, max_bytes: int
    ) -> list[int]:
        retained_rows = 0
        retained_bytes = 0
        delete_ids: list[int] = []
        for row in rows:
            row_bytes = int(row["stored_bytes"])
            if retained_rows >= max_rows or retained_bytes + row_bytes > max_bytes:
                delete_ids.append(int(row["id"]))
                continue
            retained_rows += 1
            retained_bytes += row_bytes
        return delete_ids

    def _delete_ids(self, table: str, ids: list[int], *, batch_size: int = 500) -> int:
        if table not in {"encrypted_feedback_samples", "encrypted_feedback_media"}:
            raise ValueError("unsupported retention table")
        deleted = 0
        for offset in range(0, len(ids), batch_size):
            batch = ids[offset : offset + batch_size]
            placeholders = ",".join("?" for _ in batch)
            cursor = self._conn.execute(
                f"delete from {table} where id in ({placeholders})",
                batch,
            )
            deleted += cursor.rowcount
        return deleted

    def prune_feedback(
        self,
        *,
        unreacted_ttl_seconds: int,
        unreacted_max_rows: int,
        unreacted_max_bytes: int,
        reacted_max_rows: int,
        reacted_max_bytes: int,
        media_max_bytes: int,
    ) -> dict[str, int]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=unreacted_ttl_seconds)).replace(
            microsecond=0
        ).isoformat()
        deleted = {"samples": 0, "media": 0, "events": 0, "outbound": 0}
        with self._lock:
            cursor = self._conn.execute(
                "delete from encrypted_feedback_samples "
                "where reaction_event_id is null and created_at <= ?",
                (cutoff,),
            )
            deleted["samples"] += cursor.rowcount

            for where, max_rows, max_bytes in (
                ("reaction_event_id is null", unreacted_max_rows, unreacted_max_bytes),
                ("reaction_event_id is not null", reacted_max_rows, reacted_max_bytes),
            ):
                rows = self._conn.execute(
                    f"select id, encrypted_bytes as stored_bytes "
                    f"from encrypted_feedback_samples where {where} "
                    "order by created_at desc, id desc"
                ).fetchall()
                delete_ids = self._ids_outside_caps(
                    rows, max_rows=max_rows, max_bytes=max_bytes
                )
                if delete_ids:
                    deleted["samples"] += self._delete_ids(
                        "encrypted_feedback_samples",
                        delete_ids,
                    )

            media_rows = self._conn.execute(
                "select id, length(encrypted_payload_b64) as stored_bytes "
                "from encrypted_feedback_media order by created_at desc, id desc"
            ).fetchall()
            media_delete_ids = self._ids_outside_caps(
                media_rows,
                max_rows=len(media_rows),
                max_bytes=media_max_bytes,
            )
            if media_delete_ids:
                deleted["media"] += self._delete_ids(
                    "encrypted_feedback_media",
                    media_delete_ids,
                )

            cursor = self._conn.execute(
                """
                delete from reaction_events
                where not exists (
                    select 1 from encrypted_feedback_samples sample
                    where sample.chat_id = reaction_events.chat_id
                      and sample.telegram_message_id = reaction_events.telegram_message_id
                ) and not exists (
                    select 1 from encrypted_feedback_media media
                    where media.chat_id = reaction_events.chat_id
                      and media.telegram_message_id = reaction_events.telegram_message_id
                )
                """
            )
            deleted["events"] += cursor.rowcount
            cursor = self._conn.execute(
                """
                delete from outbound_messages
                where created_at <= ? and not exists (
                    select 1 from encrypted_feedback_samples sample
                    where sample.chat_id = outbound_messages.chat_id
                      and sample.telegram_message_id = outbound_messages.telegram_message_id
                ) and not exists (
                    select 1 from encrypted_feedback_media media
                    where media.chat_id = outbound_messages.chat_id
                      and media.telegram_message_id = outbound_messages.telegram_message_id
                )
                """,
                (cutoff,),
            )
            deleted["outbound"] += cursor.rowcount
            self._conn.commit()
            self._conn.execute("pragma wal_checkpoint(truncate)")
            self._conn.execute("pragma incremental_vacuum")
        return deleted

    def feedback_stats(self) -> dict[str, int]:
        with self._lock:
            unreacted = self._conn.execute(
                "select count(*), coalesce(sum(encrypted_bytes), 0) "
                "from encrypted_feedback_samples where reaction_event_id is null"
            ).fetchone()
            reacted = self._conn.execute(
                "select count(*), coalesce(sum(encrypted_bytes), 0) "
                "from encrypted_feedback_samples where reaction_event_id is not null"
            ).fetchone()
            media = self._conn.execute(
                "select count(*), coalesce(sum(length(encrypted_payload_b64)), 0) "
                "from encrypted_feedback_media"
            ).fetchone()
        return {
            "unreacted_rows": int(unreacted[0]),
            "unreacted_bytes": int(unreacted[1]),
            "reacted_rows": int(reacted[0]),
            "reacted_bytes": int(reacted[1]),
            "media_rows": int(media[0]),
            "media_bytes": int(media[1]),
        }
