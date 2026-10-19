from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.constants import ChatType

from bot.handlers import image_command
from bot.image_generation_client import PNG_SIGNATURE
from bot.limits import GenerationAdmission


class FakeQueue:
    def __init__(self, admission: GenerationAdmission) -> None:
        self.admission = admission
        self.run = None
        self.submission = None
        self.active = 0
        self.pending = 0

    async def submit(
        self, *, chat_id, user_id, run, user_requests, chat_requests
    ) -> GenerationAdmission:
        self.run = run
        self.submission = (chat_id, user_id, user_requests, chat_requests)
        return self.admission


def make_update(text: str = "/image a red fox"):
    chat = SimpleNamespace(
        type=ChatType.SUPERGROUP,
        send_action=AsyncMock(),
    )
    user = SimpleNamespace(id=42, is_bot=False)
    message = SimpleNamespace(
        message_id=77,
        chat_id=-100,
        chat=chat,
        from_user=user,
        text=text,
        caption=None,
        photo=[],
        document=None,
        sticker=None,
        date=None,
        edit_date=None,
        reply_to_message=None,
        reply_text=AsyncMock(),
        reply_photo=AsyncMock(),
    )
    return SimpleNamespace(
        update_id=88,
        message=message,
        edited_message=None,
        callback_query=None,
        message_reaction=None,
        effective_message=message,
        effective_chat=SimpleNamespace(id=-100),
        effective_user=user,
    )


def make_context(
    queue: FakeQueue,
    *,
    globally_enabled: bool = True,
    chat_enabled: bool = True,
):
    db = SimpleNamespace(
        get_effective_admission_settings=lambda chat_id: {
            "rate_limit_user_requests": 3,
            "rate_limit_chat_requests": 10,
        },
        get_image_generation_settings=lambda chat_id: {
            "enabled": chat_enabled,
            "width": 384,
            "height": 512,
            "steps": 6,
            "guidance_scale": 1.5,
            "auto_translate": False,
        },
    )
    client = SimpleNamespace(generate=AsyncMock(return_value=PNG_SIGNATURE + b"png"))
    bot_data = {
        "config": SimpleNamespace(
            allowed_chat_ids=frozenset({-100}),
            owner_user_ids=frozenset({1}),
            image_generation_enabled=globally_enabled,
        ),
        "db": db,
        "businessgpt": object(),
        "feedback_encryptor": object(),
        "context_cache": object(),
        "media_cache": object(),
        "image_generation_queue": queue,
        "image_generation": client,
        "runtime_id": "runtime-test",
    }
    return SimpleNamespace(
        application=SimpleNamespace(bot_data=bot_data),
        image_client=client,
    )


class ImageGenerationHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueues_and_sends_png_without_feedback_services(self):
        queue = FakeQueue(GenerationAdmission(True, queued=True, position=1))
        context = make_context(queue)
        update = make_update()

        await image_command(update, context)

        self.assertEqual(queue.submission, (-100, 42, 3, 10))
        context.image_client.generate.assert_not_awaited()
        update.effective_message.reply_photo.assert_not_awaited()

        await queue.run()

        context.image_client.generate.assert_awaited_once_with(
            "a red fox",
            width=384,
            height=512,
            steps=6,
            guidance_scale=1.5,
            auto_translate=False,
        )
        _, kwargs = update.effective_message.reply_photo.await_args
        self.assertTrue(kwargs["has_spoiler"])
        self.assertEqual(kwargs["photo"].filename, "generated.png")
        update.effective_message.reply_text.assert_not_awaited()

    async def test_rejected_queue_reports_unavailable(self):
        queue = FakeQueue(
            GenerationAdmission(False, reason="generation queue is full")
        )
        context = make_context(queue)
        update = make_update()

        await image_command(update, context)

        update.effective_message.reply_text.assert_awaited_once_with(
            "image generation unavailable: generation queue is full"
        )
        context.image_client.generate.assert_not_awaited()

    async def test_can_use_replied_text_as_prompt(self):
        queue = FakeQueue(GenerationAdmission(True))
        context = make_context(queue)
        update = make_update("/image")
        update.effective_message.reply_to_message = SimpleNamespace(
            text="city at night",
            caption=None,
        )

        await image_command(update, context)
        await queue.run()

        context.image_client.generate.assert_awaited_once_with(
            "city at night",
            width=384,
            height=512,
            steps=6,
            guidance_scale=1.5,
            auto_translate=False,
        )

    async def test_global_policy_denies_before_client_or_queue_lookup(self):
        queue = FakeQueue(GenerationAdmission(True))
        context = make_context(queue, globally_enabled=False)
        context.application.bot_data.pop("image_generation")
        context.application.bot_data.pop("image_generation_queue")
        update = make_update()

        await image_command(update, context)

        update.effective_message.reply_text.assert_awaited_once_with(
            "image generation is disabled by server policy"
        )

    async def test_chat_setting_denies_without_queueing(self):
        queue = FakeQueue(GenerationAdmission(True))
        context = make_context(queue, chat_enabled=False)
        update = make_update()

        await image_command(update, context)

        update.effective_message.reply_text.assert_awaited_once_with(
            "image generation is disabled for this chat"
        )
        self.assertIsNone(queue.run)


if __name__ == "__main__":
    unittest.main()
