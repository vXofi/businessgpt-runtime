from __future__ import annotations

import asyncio
import unittest

from bot.benchmark import run_queue_benchmark


class _FakeClient:
    async def generate(self, **kwargs):
        await asyncio.sleep(0.01)
        return {
            "response": kwargs["messages"][0]["content"],
            "model": "fake",
            "elapsed_ms": 10,
            "usage": {"completion_tokens": 1},
        }


class BenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_queue_records_wait_and_usage(self) -> None:
        records = await run_queue_benchmark(
            client=_FakeClient(),
            api_url="http://businessgpt-api:8000/generate",
            requests=[
                {"id": "a", "messages": [{"role": "user", "content": "a"}]},
                {"id": "b", "messages": [{"role": "user", "content": "b"}]},
            ],
            burst_width=2,
        )
        self.assertEqual([row["id"] for row in records], ["a", "b"])
        self.assertFalse(records[0]["queued"])
        self.assertTrue(records[1]["queued"])
        self.assertGreater(records[1]["queue_wait_ms"], records[0]["queue_wait_ms"])
        self.assertEqual(records[1]["usage"]["completion_tokens"], 1)


if __name__ == "__main__":
    unittest.main()
