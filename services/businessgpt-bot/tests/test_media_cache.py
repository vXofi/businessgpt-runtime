import base64
import tempfile
import unittest
from pathlib import Path

from nacl.public import PrivateKey, SealedBox

from bot.crypto import FeedbackEncryptor
from bot.db import BotDB
from bot.media_cache import EncryptedMediaCache


class EncryptedMediaCacheTests(unittest.TestCase):
    def test_encrypts_pending_media_and_promotes_it_on_reaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            private_key = PrivateKey.generate()
            public_key_b64 = base64.b64encode(bytes(private_key.public_key)).decode("ascii")
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            cache = EncryptedMediaCache(
                directory=str(Path(tmp) / "pending"),
                ttl_seconds=3600,
                max_bytes=1024 * 1024,
                db=db,
                encryptor=FeedbackEncryptor(public_key_b64),
            )
            source = b"private image bytes"

            cache.store(
                chat_id=10,
                telegram_message_id=20,
                source=source,
                mime_type="image/png",
                original_filename="image.png",
            )

            pending = db.list_pending_feedback_media()[0]
            sealed_path = Path(pending["encrypted_path"])
            self.assertTrue(sealed_path.is_file())
            self.assertNotIn(source, sealed_path.read_bytes())

            reaction_event_id = db.insert_reaction_event(
                chat_id=10,
                telegram_message_id=20,
                user_id=40,
                old_reaction=[],
                new_reaction=["thumbs_up"],
                qualifies_as_feedback=False,
            )
            self.assertTrue(
                cache.promote(
                    chat_id=10,
                    telegram_message_id=20,
                    reaction_event_id=reaction_event_id,
                )
            )
            self.assertFalse(sealed_path.exists())
            retained = db.get_encrypted_feedback_media(
                chat_id=10,
                telegram_message_id=20,
            )
            decrypted = SealedBox(private_key).decrypt(
                base64.b64decode(retained["encrypted_payload_b64"])
            )
            self.assertEqual(decrypted, source)
            db.close()


if __name__ == "__main__":
    unittest.main()
