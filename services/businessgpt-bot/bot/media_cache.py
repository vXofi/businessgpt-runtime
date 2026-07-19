from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .crypto import FeedbackEncryptor
from .db import BotDB


class EncryptedMediaCache:
    def __init__(
        self,
        *,
        directory: str,
        ttl_seconds: int,
        max_bytes: int,
        db: BotDB,
        encryptor: FeedbackEncryptor,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes
        self.db = db
        self.encryptor = encryptor

    def cleanup(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        retained = []
        for row in self.db.list_pending_feedback_media():
            path = Path(row["encrypted_path"])
            if row["expires_at"] <= now or not path.is_file():
                path.unlink(missing_ok=True)
                self.db.delete_pending_feedback_media(
                    int(row["chat_id"]), int(row["telegram_message_id"])
                )
            else:
                retained.append(row)

        total = sum(int(row["encrypted_bytes"]) for row in retained)
        for row in retained:
            if total <= self.max_bytes:
                break
            Path(row["encrypted_path"]).unlink(missing_ok=True)
            self.db.delete_pending_feedback_media(
                int(row["chat_id"]), int(row["telegram_message_id"])
            )
            total -= int(row["encrypted_bytes"])

    def store(
        self,
        *,
        chat_id: int,
        telegram_message_id: int,
        source: bytes,
        mime_type: str,
        original_filename: str | None,
    ) -> None:
        self.cleanup()
        encrypted = self.encryptor.encrypt_bytes(source).encode("ascii")
        target = self.directory / f"{chat_id}_{telegram_message_id}.sealed"
        fd, temporary = tempfile.mkstemp(prefix=".media-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
        ).replace(microsecond=0).isoformat()
        self.db.insert_pending_feedback_media(
            chat_id=chat_id,
            telegram_message_id=telegram_message_id,
            encrypted_path=str(target),
            source_sha256=hashlib.sha256(source).hexdigest(),
            mime_type=mime_type,
            original_filename=original_filename,
            encrypted_bytes=len(encrypted),
            expires_at=expires_at,
        )
        self.cleanup()

    def promote(self, *, chat_id: int, telegram_message_id: int, reaction_event_id: int) -> bool:
        self.cleanup()
        row = next(
            (
                item for item in self.db.list_pending_feedback_media()
                if int(item["chat_id"]) == chat_id
                and int(item["telegram_message_id"]) == telegram_message_id
            ),
            None,
        )
        if row is None:
            return False
        path = Path(row["encrypted_path"])
        if not path.is_file():
            self.db.delete_pending_feedback_media(chat_id, telegram_message_id)
            return False
        promoted = self.db.promote_pending_feedback_media(
            chat_id=chat_id,
            telegram_message_id=telegram_message_id,
            reaction_event_id=reaction_event_id,
            encrypted_payload_b64=path.read_text(encoding="ascii"),
        )
        if promoted:
            path.unlink(missing_ok=True)
        return promoted
