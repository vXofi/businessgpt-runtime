import unittest
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ExtBot

from bot.update_trace import (
    TracingExtBot,
    UpdateTraceTracker,
    describe_update,
)


def make_update(
    *,
    update_id=100,
    message_id=200,
    edited=False,
    text="private message text",
):
    user = SimpleNamespace(id=300, is_bot=False)
    replied_user = SimpleNamespace(id=400, is_bot=True)
    message = SimpleNamespace(
        message_id=message_id,
        chat_id=-500,
        from_user=user,
        text=text,
        caption=None,
        photo=[],
        document=None,
        sticker=None,
        video=None,
        animation=None,
        date=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        edit_date=datetime(2026, 8, 1, 10, 5, tzinfo=UTC) if edited else None,
        reply_to_message=SimpleNamespace(
            message_id=199,
            from_user=replied_user,
        ),
    )
    return SimpleNamespace(
        update_id=update_id,
        message=None if edited else message,
        edited_message=message if edited else None,
        callback_query=None,
        message_reaction=None,
        effective_message=message,
        effective_chat=SimpleNamespace(id=-500),
        effective_user=user,
    )


class UpdateMetadataTests(unittest.TestCase):
    def test_describes_message_identity_without_content(self):
        metadata = describe_update(make_update())

        self.assertEqual(metadata.update_id, 100)
        self.assertEqual(metadata.update_type, "message")
        self.assertEqual(metadata.chat_id, -500)
        self.assertEqual(metadata.message_id, 200)
        self.assertEqual(metadata.reply_to_message_id, 199)
        self.assertEqual(metadata.reply_to_user_id, 400)
        self.assertEqual(metadata.media_kind, "text")

    def test_distinguishes_video_and_animation(self):
        for field in ("video", "animation"):
            with self.subTest(field=field):
                update = make_update(text=None)
                setattr(update.effective_message, field, object())

                self.assertEqual(describe_update(update).media_kind, field)

    def test_tracker_reports_repeated_message_identity(self):
        tracker = UpdateTraceTracker(max_messages=10, ttl_seconds=60)
        first = describe_update(make_update(update_id=100))
        second = replace(first, update_id=101)

        self.assertIsNone(tracker.observe(first, now=10.0))
        repeated = tracker.observe(second, now=12.5)

        self.assertEqual(repeated.first_update_id, 100)
        self.assertEqual(repeated.previous_update_id, 100)
        self.assertEqual(repeated.elapsed_ms, 2500)

    def test_tracker_expires_old_message_identity(self):
        tracker = UpdateTraceTracker(max_messages=10, ttl_seconds=5)
        metadata = describe_update(make_update())

        tracker.observe(metadata, now=10.0)

        self.assertIsNone(tracker.observe(metadata, now=16.0))


class PollBoundaryLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_logs_nonempty_poll_batch_without_message_content(self):
        bot = TracingExtBot(token="123:ABC", runtime_id="runtime-test")
        update = make_update(text="DO_NOT_LOG_THIS")

        with patch.object(
            ExtBot,
            "get_updates",
            new=AsyncMock(return_value=(update,)),
        ), self.assertLogs("bot.update_trace", level="INFO") as captured:
            result = await bot.get_updates(offset=99, timeout=10)

        output = "\n".join(captured.output)
        self.assertEqual(result, (update,))
        self.assertIn("telegram_poll_batch", output)
        self.assertIn("requested_offset=99", output)
        self.assertIn("updates=100:message:-500:200", output)
        self.assertNotIn("DO_NOT_LOG_THIS", output)

    async def test_trace_failure_does_not_drop_polled_update(self):
        class BrokenUpdate:
            update_id = 102
            message = object()
            edited_message = None
            callback_query = None
            message_reaction = None

            @property
            def effective_message(self):
                raise RuntimeError("broken metadata")

        bot = TracingExtBot(token="123:ABC", runtime_id="runtime-test")
        update = BrokenUpdate()

        with patch.object(
            ExtBot,
            "get_updates",
            new=AsyncMock(return_value=(update,)),
        ), self.assertLogs("bot.update_trace", level="INFO") as captured:
            result = await bot.get_updates(offset=102, timeout=10)

        self.assertEqual(result, (update,))
        self.assertIn("102:trace-error:None:None", "\n".join(captured.output))

    async def test_logs_failed_poll_offset(self):
        bot = TracingExtBot(token="123:ABC", runtime_id="runtime-test")

        with patch.object(
            ExtBot,
            "get_updates",
            new=AsyncMock(side_effect=RuntimeError("private transport detail")),
        ), self.assertLogs("bot.update_trace", level="WARNING") as captured:
            with self.assertRaises(RuntimeError):
                await bot.get_updates(offset=101, timeout=10)

        output = "\n".join(captured.output)
        self.assertIn("telegram_poll_failed", output)
        self.assertIn("requested_offset=101", output)
        self.assertIn("error_type=RuntimeError", output)
        self.assertNotIn("private transport detail", output)


if __name__ == "__main__":
    unittest.main()
