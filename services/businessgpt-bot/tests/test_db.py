import tempfile
import unittest
import sqlite3
from pathlib import Path

from bot.db import BotDB


class BotDBTest(unittest.TestCase):
    def test_prompt_presets_are_scoped_per_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            first = db.upsert_prompt(chat_id=-1, name="same", text="one", created_by=1)
            second = db.upsert_prompt(chat_id=-2, name="same", text="two", created_by=2)
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(db.get_prompt_by_name(-1, "same")["text"], "one")
            self.assertEqual(db.get_prompt_by_name(-2, "same")["text"], "two")
            self.assertIsNone(db.get_prompt_by_id(-2, first["id"]))
            db.close()

    def test_migrations_and_prompt_soft_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            prompt = db.upsert_prompt(chat_id=-100, name="test", text="hello", created_by=1)
            self.assertEqual(prompt["name"], "test")
            db.set_active_prompt(-100, prompt["id"])
            self.assertEqual(db.get_active_prompt(-100)["text"], "hello")
            self.assertTrue(db.soft_delete_prompt(-100, "test"))
            self.assertIsNone(db.get_active_prompt(-100))
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

    def test_chat_rate_limits_inherit_defaults_and_support_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(
                str(Path(tmp) / "bot.sqlite3"),
                default_rate_limit_user_requests=4,
                default_rate_limit_chat_requests=12,
            )
            db.migrate()

            self.assertEqual(
                db.get_effective_admission_settings(100),
                {
                    "rate_limit_user_requests": 4,
                    "rate_limit_chat_requests": 12,
                },
            )
            db.set_chat_setting(100, "rate_limit_user_requests", 10)
            db.set_chat_setting(100, "rate_limit_chat_requests", 30)

            self.assertEqual(
                db.get_effective_admission_settings(100),
                {
                    "rate_limit_user_requests": 10,
                    "rate_limit_chat_requests": 30,
                },
            )
            self.assertEqual(
                db.get_effective_admission_settings(200)[
                    "rate_limit_user_requests"
                ],
                4,
            )
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
            self.assertEqual(settings["recent_context_messages"], 10)
            self.assertIn("top_k", settings.keys())
            self.assertIn("rate_limit_user_requests", settings.keys())
            self.assertIn("rate_limit_chat_requests", settings.keys())
            db.close()

    def test_pending_actions_expire_without_being_popped_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            db.set_pending_action(
                chat_id=1,
                user_id=2,
                target_chat_id=-3,
                action="prompt_text",
                target_name="test",
            )
            self.assertIsNotNone(db.get_pending_action(chat_id=1, user_id=2))
            self.assertIsNotNone(db.get_pending_action(chat_id=1, user_id=2))
            db._conn.execute(
                "update pending_admin_actions set created_at = '2000-01-01T00:00:00+00:00'"
            )
            db._conn.commit()
            self.assertIsNone(db.get_pending_action(chat_id=1, user_id=2, ttl_seconds=1))
            db.close()

    def test_feedback_retention_preserves_reacted_context_under_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            for message_id in range(3):
                db.insert_encrypted_feedback_sample(
                    chat_id=-1,
                    telegram_message_id=message_id,
                    payload={"messages": ["context"]},
                    encrypted_payload_b64="x" * 10,
                )
            event_id = db.insert_reaction_event(
                chat_id=-1,
                telegram_message_id=2,
                user_id=4,
                old_reaction=[],
                new_reaction=["like"],
            )
            self.assertGreater(event_id, 0)
            db.prune_feedback(
                unreacted_ttl_seconds=3600,
                unreacted_max_rows=1,
                unreacted_max_bytes=100,
                reacted_max_rows=10,
                reacted_max_bytes=100,
                media_max_bytes=100,
            )
            stats = db.feedback_stats()
            self.assertEqual(stats["unreacted_rows"], 1)
            self.assertEqual(stats["reacted_rows"], 1)
            db.close()

    def test_feedback_retention_deletes_large_sets_in_bounded_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = BotDB(str(Path(tmp) / "bot.sqlite3"))
            db.migrate()
            db._conn.executemany(
                """
                insert into encrypted_feedback_samples(
                    chat_id, telegram_message_id, payload_json_sha256,
                    encrypted_payload_b64, encrypted_bytes, schema_version, created_at
                ) values (-1, ?, 'hash', 'sealed', 6, 2, '2026-01-01T00:00:00+00:00')
                """,
                ((message_id,) for message_id in range(1200)),
            )
            db._conn.commit()

            db.prune_feedback(
                unreacted_ttl_seconds=10**9,
                unreacted_max_rows=1,
                unreacted_max_bytes=100,
                reacted_max_rows=1,
                reacted_max_bytes=100,
                media_max_bytes=100,
            )

            self.assertEqual(db.feedback_stats()["unreacted_rows"], 1)
            db.close()


if __name__ == "__main__":
    unittest.main()
