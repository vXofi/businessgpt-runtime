import os
import unittest
from unittest.mock import patch

from bot.config import Config


BASE_ENV = {
    "BUSINESSGPT_BOT_TOKEN": "telegram-token",
    "BUSINESSGPT_BOT_OWNER_USER_IDS": "123",
    "BUSINESSGPT_BOT_ALLOWED_CHAT_IDS": "-100",
    "BUSINESSGPT_API_KEY": "api-secret",
    "BUSINESSGPT_IMAGE_GENERATION_ENABLED": "true",
    "BUSINESSGPT_IMAGE_API_KEY": "image-secret",
    "BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64": "public-key",
}


class ConfigTests(unittest.TestCase):
    def test_runpod_only_requires_its_own_key_and_bounds_images(self):
        env = {
            **BASE_ENV,
            "BUSINESSGPT_API_URL": "https://api.runpod.ai/v2/endpoint123",
            "BUSINESSGPT_RUNPOD_API_KEY": "runpod-secret",
            "BUSINESSGPT_BOT_TELEGRAM_PROXY_URL": "socks5://proxy:1080",
        }
        env.pop("BUSINESSGPT_API_KEY")
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.runpod_api_key, "runpod-secret")
        self.assertEqual(config.runpod_timeout_seconds, 600)
        self.assertEqual(config.image_max_bytes, 6 * 1024 * 1024)
        self.assertEqual(config.runpod_proxy_url, "socks5://proxy:1080")
        env.pop("BUSINESSGPT_RUNPOD_API_KEY")
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "RUNPOD_API_KEY"):
                Config.from_env()

    def test_mixed_profiles_require_both_credentials(self):
        env = {
            **BASE_ENV,
            "BUSINESSGPT_BOT_ALLOWED_API_URLS": (
                "http://businessgpt-api:8000/generate,https://api.runpod.ai/v2/endpoint123"
            ),
            "BUSINESSGPT_RUNPOD_API_KEY": "runpod-secret",
        }
        env.pop("BUSINESSGPT_API_KEY")
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BUSINESSGPT_API_KEY"):
                Config.from_env()

    def test_runpod_deadline_is_bounded(self):
        for timeout in ("0", "10", "1801"):
            with patch.dict(os.environ, {**BASE_ENV, "BUSINESSGPT_RUNPOD_TIMEOUT_SECONDS": timeout}, clear=True):
                with self.assertRaises(RuntimeError):
                    Config.from_env()

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
        self.assertEqual(
            config.image_api_url,
            "https://anogram.ru/v1/images/generations/stream",
        )
        self.assertEqual(config.image_generation_queue_size, 4)

    def test_requires_image_api_key_and_generate_endpoint(self):
        env = dict(BASE_ENV)
        env.pop("BUSINESSGPT_IMAGE_API_KEY")
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BUSINESSGPT_IMAGE_API_KEY"):
                Config.from_env()

        env = {
            **BASE_ENV,
            "BUSINESSGPT_IMAGE_API_URL": "https://images.example.test/docs",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "path must be /v1/images"):
                Config.from_env()

    def test_image_generation_is_fail_closed_and_does_not_require_key(self):
        env = dict(BASE_ENV)
        env.pop("BUSINESSGPT_IMAGE_GENERATION_ENABLED")
        env.pop("BUSINESSGPT_IMAGE_API_KEY")

        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()

        self.assertFalse(config.image_generation_enabled)
        self.assertIsNone(config.image_api_key)


if __name__ == "__main__":
    unittest.main()
