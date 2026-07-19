import base64
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nacl.public import PrivateKey

from bot.crypto import FeedbackEncryptor
from bot.db import BotDB


SCRIPT_PATH = (
    Path(__file__).parents[3] / "scripts" / "export_businessgpt_feedback_samples.py"
)


class FeedbackExportTests(unittest.TestCase):
    def test_schema_v2_export_includes_complete_reaction_history(self):
        spec = importlib.util.spec_from_file_location("feedback_export", SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.sqlite3"
            private_key = PrivateKey.generate()
            public_key = base64.b64encode(bytes(private_key.public_key)).decode("ascii")
            private_key_b64 = base64.b64encode(bytes(private_key)).decode("ascii")
            payload = {
                "feedback_schema_version": 2,
                "api_request": {"messages": [{"role": "user", "content": "context"}]},
            }

            db = BotDB(str(path))
            db.migrate()
            db.insert_encrypted_feedback_sample(
                chat_id=-1,
                telegram_message_id=2,
                payload=payload,
                encrypted_payload_b64=FeedbackEncryptor(public_key).encrypt_json(payload),
            )
            db.insert_reaction_event(
                chat_id=-1,
                telegram_message_id=2,
                user_id=3,
                old_reaction=[],
                new_reaction=["like"],
            )
            db.insert_reaction_event(
                chat_id=-1,
                telegram_message_id=2,
                user_id=3,
                old_reaction=["like"],
                new_reaction=["dislike"],
            )
            db.close()

            output = io.StringIO()
            argv = [
                str(SCRIPT_PATH),
                str(path),
                "--private-key-b64",
                private_key_b64,
            ]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                self.assertEqual(module.main(), 0)

            record = json.loads(output.getvalue())
            self.assertEqual(record["feedback_schema_version"], 2)
            self.assertEqual(len(record["reaction_history"]), 2)
            self.assertEqual(record["final_reaction_state"], ["dislike"])
            self.assertEqual(record["payload"], payload)


if __name__ == "__main__":
    unittest.main()
