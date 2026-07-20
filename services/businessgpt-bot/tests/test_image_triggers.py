import unittest
from types import SimpleNamespace

from bot.handlers import (
    _image_source,
    _media_message_is_trigger,
    _text_message_is_trigger,
)


def make_context(bot_id=42, username="businessgpt_chatbot"):
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"bot_id": bot_id, "bot_username": username}
        )
    )


def make_message(
    *,
    text=None,
    caption=None,
    photo=False,
    sticker=False,
    reply_to=None,
    from_user_id=100,
):
    return SimpleNamespace(
        text=text,
        caption=caption,
        photo=[object()] if photo else [],
        document=None,
        sticker=object() if sticker else None,
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

    async def test_sticker_reply_to_bot_triggers_generation(self):
        replied_bot_message = make_message(from_user_id=42)
        sticker = make_message(sticker=True, reply_to=replied_bot_message)

        self.assertTrue(await _media_message_is_trigger(sticker, make_context()))

    async def test_text_reply_to_sticker_triggers_generation(self):
        sticker = make_message(sticker=True)
        reply = make_message(text="what does this mean?", reply_to=sticker)

        self.assertTrue(await _text_message_is_trigger(reply, make_context()))

    async def test_gpt_reply_uses_sticker_as_image_source(self):
        sticker = make_message(sticker=True)
        command = make_message(text="/gpt", reply_to=sticker)

        self.assertIs(_image_source(command), sticker)

    async def test_standalone_sticker_does_not_trigger_generation(self):
        sticker = make_message(sticker=True)

        self.assertFalse(await _media_message_is_trigger(sticker, make_context()))


if __name__ == "__main__":
    unittest.main()
