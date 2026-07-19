import tempfile
import unittest
from pathlib import Path

from bot.db import BotDB
from bot.handlers import _prompt_preview_text, _settings_button_label, _settings_view


class SettingsViewTests(unittest.TestCase):
    def test_active_prompt_and_model_names_are_visible_on_buttons(self):
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
            prompt = db.upsert_prompt(name="drunk_short", text="test", created_by=1)
            db.set_active_prompt(-100, prompt["id"])

            _, keyboard = _settings_view(
                db,
                -100,
                bot_username="businessgpt_chatbot",
                private=False,
            )

            self.assertEqual(keyboard.inline_keyboard[0][0].text, "Prompt: drunk_short")
            self.assertEqual(
                keyboard.inline_keyboard[0][0].callback_data,
                "s:prompt_preview",
            )
            self.assertEqual(keyboard.inline_keyboard[0][1].text, "Model: default")
            db.close()

    def test_long_button_labels_are_bounded(self):
        label = _settings_button_label("Prompt", "x" * 64)

        self.assertLessEqual(len(label), 48)
        self.assertTrue(label.endswith("..."))

    def test_long_prompt_preview_is_bounded(self):
        preview = _prompt_preview_text("long", "x" * 5000)

        self.assertLessEqual(len(preview), 3800)
        self.assertTrue(preview.endswith("[preview truncated]"))


if __name__ == "__main__":
    unittest.main()
