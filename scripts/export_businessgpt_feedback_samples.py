#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "businessgpt-bot"))

from bot.crypto import decrypt_feedback_bytes, decrypt_feedback_payload  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decrypt BusinessGPT bot feedback samples from a copied SQLite DB."
    )
    parser.add_argument("db_path", help="Path to copied bot.sqlite3")
    parser.add_argument(
        "--private-key-b64",
        default=os.environ.get("BUSINESSGPT_BOT_FEEDBACK_PRIVATE_KEY_B64"),
        help="Private key b64. Defaults to BUSINESSGPT_BOT_FEEDBACK_PRIVATE_KEY_B64.",
    )
    parser.add_argument(
        "--include-pending",
        action="store_true",
        help="Also export encrypted samples that do not have a reaction_event_id yet.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output JSONL path. Defaults to stdout.",
    )
    parser.add_argument(
        "--media-output-dir",
        help="Decrypt retained reacted image inputs into this directory.",
    )
    return parser.parse_args()


def open_output(path: str | None) -> tuple[TextIO, bool]:
    if not path:
        return sys.stdout, False
    return open(path, "w", encoding="utf-8"), True


def main() -> int:
    args = parse_args()
    if not args.private_key_b64:
        raise SystemExit(
            "Set BUSINESSGPT_BOT_FEEDBACK_PRIVATE_KEY_B64 or pass --private-key-b64."
        )

    where = "" if args.include_pending else "where reaction_event_id is not null"
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        select id, chat_id, telegram_message_id, reaction_event_id,
               payload_json_sha256, encrypted_payload_b64, created_at
        from encrypted_feedback_samples
        {where}
        order by id
        """
    ).fetchall()

    output, should_close = open_output(args.output)
    try:
        for row in rows:
            payload = decrypt_feedback_payload(
                row["encrypted_payload_b64"],
                args.private_key_b64,
            )
            record = {
                "sample_id": row["id"],
                "chat_id": row["chat_id"],
                "telegram_message_id": row["telegram_message_id"],
                "reaction_event_id": row["reaction_event_id"],
                "payload_json_sha256": row["payload_json_sha256"],
                "created_at": row["created_at"],
                "payload": payload,
            }
            output.write(json.dumps(record, ensure_ascii=False) + "\n")

        if args.media_output_dir:
            media_dir = Path(args.media_output_dir)
            media_dir.mkdir(parents=True, exist_ok=True)
            media_rows = conn.execute(
                """
                select id, chat_id, telegram_message_id, reaction_event_id,
                       source_sha256, mime_type, original_filename,
                       encrypted_payload_b64, created_at
                from encrypted_feedback_media
                order by id
                """
            ).fetchall()
            extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
            for row in media_rows:
                data = decrypt_feedback_bytes(row["encrypted_payload_b64"], args.private_key_b64)
                actual_hash = hashlib.sha256(data).hexdigest()
                if actual_hash != row["source_sha256"]:
                    raise RuntimeError(f"media hash mismatch for sample {row['id']}")
                media_path = media_dir / f"media-{row['id']}{extensions.get(row['mime_type'], '.bin')}"
                media_path.write_bytes(data)
                record = {
                    "media_sample_id": row["id"],
                    "chat_id": row["chat_id"],
                    "telegram_message_id": row["telegram_message_id"],
                    "reaction_event_id": row["reaction_event_id"],
                    "source_sha256": row["source_sha256"],
                    "mime_type": row["mime_type"],
                    "original_filename": row["original_filename"],
                    "created_at": row["created_at"],
                    "decrypted_path": str(media_path),
                }
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
    finally:
        if should_close:
            output.close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
