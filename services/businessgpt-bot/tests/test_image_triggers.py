import unittest
from types import SimpleNamespace

from bot.handlers import _media_message_is_trigger, _text_message_is_trigger


def make_context(bot_id=42, username="businessgpt_chatbot"):
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"bot_id": bot_id, "bot_username": username}
        )
    )


def make_message(*, text=None, caption=None, photo=False, reply_to=None, from_user_id=100):
    return SimpleNamespace(
        text=text,
        caption=caption,
        photo=[object()] if photo else [],
        document=None,
        reply_to_message=reply_to,
        from_user=SimpleNamespace(id=from_user_id),
    )


class ImageTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_reply_to_bot_triggers_generation(self):
        replied_bot_message = make_message(from_user_id=42)
        image = make_message(photo=True, reply_to=replied_bot_message)

        self.assertTrue(await _media_message_is_trigger(image, make_context()))

    async def test_text_reply_to_image_triggers_generation(self):
        image = make_message(photo=True)
        reply = make_message(text="what is this?", reply_to=image)

        self.assertTrue(await _text_message_is_trigger(reply, make_context()))

    async def test_standalone_image_does_not_trigger_generation(self):
        image = make_message(photo=True)

        self.assertFalse(await _media_message_is_trigger(image, make_context()))


if __name__ == "__main__":
    unittest.main()
