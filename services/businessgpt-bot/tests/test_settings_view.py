import tempfile
import unittest
from pathlib import Path

from bot.db import BotDB
from bot.handlers import (
    SETTING_OPTIONS,
    _image_settings_view,
    _parse_image_custom_settings,
    _prompt_preview_text,
    _settings_button_label,
    _settings_view,
)


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
            prompt = db.upsert_prompt(chat_id=-100, name="drunk_short", text="test", created_by=1)
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
                "s:n100:prompt_preview",
            )
            self.assertEqual(keyboard.inline_keyboard[0][1].text, "Model: default")
            self.assertEqual(keyboard.inline_keyboard[4][0].text, "User/60s 3")
            self.assertEqual(
                keyboard.inline_keyboard[4][0].callback_data,
                "s:n100:field:rate_limit_user_requests",
            )
            self.assertEqual(keyboard.inline_keyboard[4][1].text, "Chat/60s 10")
            self.assertEqual(
                keyboard.inline_keyboard[5][0].text,
                "Images: on 512x512",
            )
            db.close()

    def test_global_image_policy_is_visible_and_not_presented_as_enabled(self):
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

            text, keyboard = _settings_view(
                db,
                -100,
                bot_username="businessgpt_chatbot",
                private=False,
                image_generation_globally_enabled=False,
            )

            self.assertIn("Images: disabled by server policy", text)
            self.assertEqual(
                keyboard.inline_keyboard[5][0].text,
                "Images: server-disabled",
            )
            db.close()

    def test_long_button_labels_are_bounded(self):
        label = _settings_button_label("Prompt", "x" * 64)

        self.assertLessEqual(len(label), 48)
        self.assertTrue(label.endswith("..."))

    def test_long_prompt_preview_is_bounded(self):
        preview = _prompt_preview_text("long", "x" * 5000)

        self.assertLessEqual(len(preview), 3800)
        self.assertTrue(preview.endswith("[preview truncated]"))

    def test_image_presets_include_new_provider_limits(self):
        self.assertEqual(SETTING_OPTIONS["image_steps"], tuple(range(2, 17)))
        self.assertIn(16, SETTING_OPTIONS["image_steps"])
        self.assertIn(0.5, SETTING_OPTIONS["image_guidance_scale"])
        self.assertIn(5.0, SETTING_OPTIONS["image_guidance_scale"])

    def test_custom_image_settings_accept_provider_json(self):
        self.assertEqual(
            _parse_image_custom_settings(
                '{"width":512,"height":512,"steps":16,'
                '"guidance_scale":5.0,"auto_translate":true}'
            ),
            {
                "image_width": 512,
                "image_height": 512,
                "image_steps": 16,
                "image_guidance_scale": 5.0,
                "image_auto_translate": 1,
            },
        )

    def test_custom_image_settings_button_is_available(self):
        _, keyboard = _image_settings_view(
            -100,
            {
                "enabled": True,
                "width": 512,
                "height": 512,
                "steps": 16,
                "guidance_scale": 5.0,
                "auto_translate": True,
            },
        )

        custom = keyboard.inline_keyboard[-2][0]
        self.assertEqual(custom.text, "Custom parameters")
        self.assertEqual(custom.callback_data, "s:n100:image_custom")


if __name__ == "__main__":
    unittest.main()
