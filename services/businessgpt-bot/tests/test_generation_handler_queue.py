import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.constants import ChatType

from bot.handlers import _generate_and_reply
from bot.limits import GenerationAdmission


class FakeGenerationQueue:
    def __init__(self, admission: GenerationAdmission) -> None:
        self.admission = admission
        self.run = None
        self.submission = None

    async def submit(
        self, *, chat_id, user_id, run, user_requests, chat_requests
    ):
        self.run = run
        self.submission = (chat_id, user_id, user_requests, chat_requests)
        return self.admission


def make_context(queue: FakeGenerationQueue):
    db = SimpleNamespace(
        get_effective_admission_settings=lambda chat_id: {
            "rate_limit_user_requests": 5,
            "rate_limit_chat_requests": 20,
        }
    )
    bot_data = {
        "config": SimpleNamespace(allowed_chat_ids=frozenset({-100})),
        "db": db,
        "businessgpt": object(),
        "feedback_encryptor": object(),
        "context_cache": object(),
        "media_cache": object(),
        "generation_queue": queue,
        "runtime_id": "runtime-test",
    }
    return SimpleNamespace(application=SimpleNamespace(bot_data=bot_data))


def make_update(reply_text: AsyncMock):
    message = SimpleNamespace(
        message_id=77,
        chat_id=-100,
        chat=SimpleNamespace(type=ChatType.SUPERGROUP),
        from_user=SimpleNamespace(id=42, is_bot=False),
        text="hello",
        caption=None,
        photo=[],
        document=None,
        sticker=None,
        date=None,
        edit_date=None,
        reply_to_message=None,
        reply_text=reply_text,
    )
    return SimpleNamespace(
        update_id=88,
        message=message,
        edited_message=None,
        callback_query=None,
        message_reaction=None,
        effective_message=message,
        effective_chat=SimpleNamespace(id=-100),
        effective_user=SimpleNamespace(id=42),
    )


class GenerationHandlerQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_handler_is_silent_then_worker_runs_generation(self):
        reply_text = AsyncMock()
        queue = FakeGenerationQueue(
            GenerationAdmission(True, queued=True, position=1)
        )
        context = make_context(queue)
        update = make_update(reply_text)

        with patch(
            "bot.handlers._generate_and_reply_inner", new_callable=AsyncMock
        ) as generate, self.assertLogs("bot.handlers", level="INFO") as captured:
            await _generate_and_reply(update=update, context=context, user_text="secret text")

            generate.assert_not_awaited()
            self.assertEqual(queue.submission, (-100, 42, 5, 20))
            reply_text.assert_not_awaited()

            await queue.run()

            generate.assert_awaited_once()

        output = "\n".join(captured.output)
        self.assertIn("generation_trigger", output)
        self.assertIn("generation_admission", output)
        self.assertIn("generation_job_started", output)
        self.assertIn("generation_job_finished", output)
        self.assertIn("source_message_id=77", output)
        self.assertNotIn("secret text", output)

    async def test_full_queue_reports_unavailable_without_running(self):
        reply_text = AsyncMock()
        queue = FakeGenerationQueue(
            GenerationAdmission(False, reason="generation queue is full")
        )

        with patch(
            "bot.handlers._generate_and_reply_inner", new_callable=AsyncMock
        ) as generate:
            await _generate_and_reply(
                update=make_update(reply_text),
                context=make_context(queue),
                user_text="hello",
            )

            reply_text.assert_awaited_once_with(
                "generation unavailable: generation queue is full"
            )
            generate.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
