import tempfile
import unittest
import sqlite3
from pathlib import Path

from bot.db import BotDB


class BotDBTest(unittest.TestCase):
    def test_migrations_and_prompt_soft_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            prompt = db.upsert_prompt(name="test", text="hello", created_by=1)
            self.assertEqual(prompt["name"], "test")
            db.set_active_prompt(100, prompt["id"])
            self.assertEqual(db.get_active_prompt(100)["text"], "hello")
            self.assertTrue(db.soft_delete_prompt("test"))
            self.assertIsNone(db.get_active_prompt(100))
            db.close()

    def test_default_model_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            db.seed_default_model_profile(
                api_url="http://businessgpt-api:8000/generate",
                max_tokens=128,
                temperature=0.9,
                top_p=0.9,
                top_k=50,
                repetition_penalty=1.2,
            )
            model = db.get_active_model_profile(100)
            self.assertEqual(model["name"], "default")
            self.assertEqual(model["max_tokens"], 128)

            db.upsert_model_profile(
                name="default",
                api_url="http://custom/generate",
                model_label="custom",
                max_tokens=256,
                temperature=0.7,
                top_p=0.8,
                top_k=40,
                repetition_penalty=1.1,
            )
            db.seed_default_model_profile(
                api_url="http://businessgpt-api:8000/generate",
                max_tokens=128,
                temperature=0.9,
                top_p=0.9,
                top_k=50,
                repetition_penalty=1.2,
            )
            model = db.get_active_model_profile(100)
            self.assertEqual(model["api_url"], "http://custom/generate")
            self.assertEqual(model["max_tokens"], 256)
            db.close()

    def test_outbound_message_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            db.seed_default_model_profile(
                api_url="http://businessgpt-api:8000/generate",
                max_tokens=128,
                temperature=0.9,
                top_p=0.9,
                top_k=50,
                repetition_penalty=1.2,
            )
            model = db.get_active_model_profile(100)
            db.insert_outbound_message(
                chat_id=100,
                telegram_message_id=200,
                prompt_preset_id=None,
                model_profile_id=model["id"],
                prompt_hash="abc123",
            )

            self.assertIsNotNone(
                db.get_outbound_message(chat_id=100, telegram_message_id=200)
            )
            self.assertIsNone(
                db.get_outbound_message(chat_id=100, telegram_message_id=201)
            )
            db.close()

    def test_chat_overrides_and_context_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            db.seed_default_model_profile(
                api_url="http://businessgpt-api:8000/generate",
                max_tokens=128,
                temperature=0.9,
                top_p=0.9,
                top_k=50,
                repetition_penalty=1.2,
            )
            db.set_chat_setting(100, "recent_context_messages", 10)
            db.set_chat_setting(100, "temperature", 0.5)

            settings = db.get_effective_generation_settings(100)

            self.assertEqual(settings["recent_context_messages"], 10)
            self.assertEqual(settings["temperature"], 0.5)
            self.assertEqual(settings["top_k"], 50)
            db.close()

    def test_migrates_existing_schema_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.sqlite3"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                create table model_profiles (
                    id integer primary key, name text not null unique,
                    api_url text not null, model_label text not null,
                    max_tokens integer not null, temperature real not null,
                    top_p real not null, repetition_penalty real not null,
                    created_at text not null, updated_at text not null, deleted_at text
                );
                create table chat_settings (
                    chat_id integer primary key,
                    active_prompt_id integer,
                    active_model_profile_id integer
                );
                insert into chat_settings(chat_id) values (99);
                """
            )
            conn.commit()
            conn.close()

            db = BotDB(str(path))
            db.migrate()

            settings = db.get_chat_settings(99)
            self.assertEqual(settings["recent_context_messages"], 0)
            self.assertIn("top_k", settings.keys())
            db.close()


if __name__ == "__main__":
    unittest.main()
