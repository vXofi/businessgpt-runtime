import os
import unittest
from unittest.mock import patch

from bot.config import Config


BASE_ENV = {
    "BUSINESSGPT_BOT_TOKEN": "telegram-token",
    "BUSINESSGPT_BOT_OWNER_USER_IDS": "123",
    "BUSINESSGPT_BOT_ALLOWED_CHAT_IDS": "-100",
    "BUSINESSGPT_API_KEY": "api-secret",
    "BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64": "public-key",
}


class ConfigTests(unittest.TestCase):
    def test_requires_an_explicit_negative_chat_allowlist(self):
        env = dict(BASE_ENV)
        env.pop("BUSINESSGPT_BOT_ALLOWED_CHAT_IDS")
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ALLOWED_CHAT_IDS"):
                Config.from_env()

        env = {**BASE_ENV, "BUSINESSGPT_BOT_ALLOWED_CHAT_IDS": "123"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "group or supergroup"):
                Config.from_env()

    def test_requires_persistent_database_and_media_paths(self):
        env = {**BASE_ENV, "BUSINESSGPT_BOT_DB_PATH": "/tmp/bot.sqlite3"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "below /data"):
                Config.from_env()

    def test_default_endpoint_is_allowlisted_exactly(self):
        env = {
            **BASE_ENV,
            "BUSINESSGPT_BOT_ALLOWED_API_URLS": "https://attacker.example/generate",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must be in"):
                Config.from_env()

        with patch.dict(os.environ, BASE_ENV, clear=True):
            config = Config.from_env()
        self.assertEqual(
            config.allowed_api_urls,
            frozenset({"http://businessgpt-api:8000/generate"}),
        )
        self.assertEqual(config.default_recent_context_messages, 10)
        self.assertEqual(config.generation_queue_size, 8)


if __name__ == "__main__":
    unittest.main()
