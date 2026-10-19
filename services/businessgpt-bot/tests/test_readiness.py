import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from telegram.ext import ExtBot

from bot.readiness import Readiness, healthy, polling_lock
from bot.update_trace import TracingExtBot


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ready.json"

    async def test_empty_successful_poll_is_ready_only_after_initialization(self):
        readiness = Readiness(str(self.path))
        bot = TracingExtBot(token="123:ABC", runtime_id="test", readiness=readiness)
        with patch.object(ExtBot, "get_updates", AsyncMock(return_value=())):
            await bot.get_updates()
        readiness.write(True)
        self.assertFalse(healthy(self.path))
        readiness.initialized = True
        readiness.write(True)
        self.assertTrue(healthy(self.path))
        self.assertEqual(set(json.loads(self.path.read_text())),
                         {"initialized", "running", "last_poll", "updated_at"})

    async def test_poll_error_invalidates_success(self):
        readiness = Readiness(str(self.path))
        readiness.initialized = True
        readiness.poll(True)
        bot = TracingExtBot(token="123:ABC", runtime_id="test", readiness=readiness)
        with patch.object(ExtBot, "get_updates", AsyncMock(side_effect=RuntimeError("upstream"))):
            with self.assertRaises(RuntimeError):
                await bot.get_updates()
        readiness.write(True)
        self.assertFalse(healthy(self.path))

    def test_stale_or_stopped_or_missing_readiness_fails(self):
        self.assertFalse(healthy(self.path))
        readiness = Readiness(str(self.path))
        readiness.initialized = True
        readiness.poll(True)
        readiness.write(False)
        self.assertFalse(healthy(self.path))
        readiness.write(True)
        now = json.loads(self.path.read_text())["updated_at"]
        self.assertFalse(healthy(self.path, now=now + 21))

    def test_single_poller_lock(self):
        lock = self.path.with_name("polling.lock")
        with polling_lock(lock):
            with self.assertRaises(RuntimeError):
                polling_lock(lock)
        with polling_lock(lock):
            pass
