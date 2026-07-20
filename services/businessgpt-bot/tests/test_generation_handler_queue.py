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

    async def submit(self, *, chat_id, user_id, run):
        self.run = run
        self.submission = (chat_id, user_id)
        return self.admission


def make_context(queue: FakeGenerationQueue):
    bot_data = {
        "config": SimpleNamespace(allowed_chat_ids=frozenset({-100})),
        "db": object(),
        "businessgpt": object(),
        "feedback_encryptor": object(),
        "context_cache": object(),
        "media_cache": object(),
        "generation_queue": queue,
    }
    return SimpleNamespace(application=SimpleNamespace(bot_data=bot_data))


def make_update(reply_text: AsyncMock):
    message = SimpleNamespace(
        chat_id=-100,
        chat=SimpleNamespace(type=ChatType.SUPERGROUP),
        reply_text=reply_text,
    )
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=42),
    )


class GenerationHandlerQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_handler_returns_then_worker_runs_generation(self):
        notice = SimpleNamespace(delete=AsyncMock())
        reply_text = AsyncMock(return_value=notice)
        queue = FakeGenerationQueue(
            GenerationAdmission(True, queued=True, position=1)
        )
        context = make_context(queue)
        update = make_update(reply_text)

        with patch(
            "bot.handlers._generate_and_reply_inner", new_callable=AsyncMock
        ) as generate:
            await _generate_and_reply(
                update=update,
                context=context,
                user_text="hello",
            )

            generate.assert_not_awaited()
            self.assertEqual(queue.submission, (-100, 42))
            reply_text.assert_awaited_once_with("generation queued: position 1")

            await queue.run()

            notice.delete.assert_awaited_once()
            generate.assert_awaited_once()

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
