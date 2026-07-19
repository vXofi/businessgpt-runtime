from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Optional


NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def validate_name(name: str) -> str:
    name = name.strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("name must be 1-64 chars: letters, digits, dot, underscore, dash")
    return name


class BotDB:
    def __init__(self, path: str) -> None:
        self.path = path
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

    def list_prompts(self) -> list[sqlite3.Row]:
        return self._fetchall(
            "select * from prompt_presets where deleted_at is null order by name"
        )

    def get_prompt_by_name(self, name: str) -> Optional[sqlite3.Row]:
        return self._fetchone(
            "select * from prompt_presets where name = ? and deleted_at is null",
            (validate_name(name),),
        )

    def get_prompt_by_id(self, preset_id: int | None) -> Optional[sqlite3.Row]:
        if preset_id is None:
            return None
        return self._fetchone(
            "select * from prompt_presets where id = ? and deleted_at is null",
            (preset_id,),
        )

    def upsert_prompt(self, *, name: str, text: str, created_by: int | None) -> sqlite3.Row:
        name = validate_name(name)
        text = text.strip()
        if not text:
            raise ValueError("prompt text must not be blank")
        now = utc_now()
        with self._lock:
            existing = self._conn.execute(
                "select id from prompt_presets where name = ?",
                (name,),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """
                    update prompt_presets
                    set text = ?, updated_at = ?, deleted_at = null
                    where name = ?
                    """,
                    (text, now, name),
                )
            else:
                self._conn.execute(
                    """
                    insert into prompt_presets(name, text, created_by, created_at, updated_at)
                    values (?, ?, ?, ?, ?)
                    """,
                    (name, text, created_by, now, now),
                )
            self._conn.commit()
        row = self.get_prompt_by_name(name)
        assert row is not None
        return row

    def soft_delete_prompt(self, name: str) -> bool:
        now = utc_now()
        with self._lock:
            cur = self._conn.execute(
                "update prompt_presets set deleted_at = ?, updated_at = ? where name = ? and deleted_at is null",
                (now, now, validate_name(name)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_chat_settings(self, chat_id: int) -> sqlite3.Row:
        with self._lock:
            self._conn.execute(
                "insert or ignore into chat_settings(chat_id) values (?)",
                (chat_id,),
            )
            self._conn.commit()
        row = self._fetchone("select * from chat_settings where chat_id = ?", (chat_id,))
        assert row is not None
        return row

    def set_active_prompt(self, chat_id: int, prompt_id: int | None) -> None:
        with self._lock:
            self._conn.execute(
                """
                insert into chat_settings(chat_id, active_prompt_id)
                values (?, ?)
                on conflict(chat_id) do update set active_prompt_id = excluded.active_prompt_id
                """,
                (chat_id, prompt_id),
            )
            self._conn.commit()

    def set_chat_setting(self, chat_id: int, field: str, value: Any) -> None:
        allowed = {
            "recent_context_messages",
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "repetition_penalty",
        }
        if field not in allowed:
            raise ValueError(f"unsupported chat setting: {field}")
        with self._lock:
            self._conn.execute(
                f"insert into chat_settings(chat_id, {field}) values (?, ?) "
                f"on conflict(chat_id) do update set {field} = excluded.{field}",
                (chat_id, value),
            )
            self._conn.commit()

    def get_effective_generation_settings(self, chat_id: int) -> dict[str, Any]:
        chat = self.get_chat_settings(chat_id)
        model = self.get_active_model_profile(chat_id)
        return {
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
        }

    def get_active_prompt(self, chat_id: int) -> Optional[sqlite3.Row]:
        settings = self.get_chat_settings(chat_id)
        return self.get_prompt_by_id(settings["active_prompt_id"])

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
                        max_tokens,
                        temperature,
                        top_p,
                        top_k,
                        repetition_penalty,
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
                        max_tokens,
                        temperature,
                        top_p,
                        top_k,
                        repetition_penalty,
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

    def set_pending_action(self, *, chat_id: int, user_id: int, action: str, target_name: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                insert into pending_admin_actions(chat_id, user_id, action, target_name, created_at)
                values (?, ?, ?, ?, ?)
                on conflict(chat_id, user_id) do update set
                    action = excluded.action,
                    target_name = excluded.target_name,
                    created_at = excluded.created_at
                """,
                (chat_id, user_id, action, validate_name(target_name), utc_now()),
            )
            self._conn.commit()

    def pop_pending_action(self, *, chat_id: int, user_id: int) -> Optional[sqlite3.Row]:
        with self._lock:
            row = self._conn.execute(
                "select * from pending_admin_actions where chat_id = ? and user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            if row:
                self._conn.execute(
                    "delete from pending_admin_actions where chat_id = ? and user_id = ?",
                    (chat_id, user_id),
                )
                self._conn.commit()
            return row

    def get_pending_action(self, *, chat_id: int, user_id: int) -> Optional[sqlite3.Row]:
        return self._fetchone(
            "select * from pending_admin_actions where chat_id = ? and user_id = ?",
            (chat_id, user_id),
        )

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
                    chat_id, telegram_message_id, prompt_preset_id, model_profile_id,
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
    ) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._lock:
            self._conn.execute(
                """
                insert into encrypted_feedback_samples(
                    chat_id, telegram_message_id, payload_json_sha256,
                    encrypted_payload_b64, created_at
                )
                values (?, ?, ?, ?, ?)
                """,
                (chat_id, telegram_message_id, payload_hash, encrypted_payload_b64, utc_now()),
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
