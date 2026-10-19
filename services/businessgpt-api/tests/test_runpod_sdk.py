"""Run inside the Serverless SDK environment; no GPU or network is used."""
import asyncio
import contextlib
import importlib.util
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from runpod_worker import Worker


@unittest.skipUnless(importlib.util.find_spec("runpod"), "Serverless SDK environment required")
class RunpodSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from runpod.serverless.modules.rp_job import run_job
        from runpod.serverless.modules.rp_logger import RunPodLogger

        RunPodLogger().set_level("WARN")
        self.run_job = run_job
        self.runtime = SimpleNamespace(
            ready={"ok": True}, prompt={"name": "API default", "text": "private prompt"},
            health=AsyncMock(return_value={"ok": True}), stop=Mock(),
            generate=AsyncMock(return_value={"response": "answer", "usage": {}}),
        )
        self.worker = Worker(self.runtime)

    async def test_sdk_wraps_output_without_altering_prompt_metadata(self):
        result = await self.run_job(self.worker.handle, {
            "id": "test-job", "input": {"operation": "generate", "request": {"prompt": "hello"}},
        })
        self.assertEqual(result["output"]["response"], "answer")
        self.assertEqual(result["output"]["default_prompt"]["text"], "private prompt")
        self.runtime.generate.assert_awaited_once()

    async def test_sdk_error_result_and_logs_do_not_contain_request_text(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = await self.run_job(self.worker.handle, {
                "id": "test-job", "input": {
                    "operation": "generate",
                    "request": {"messages": [{"role": "private-role", "content": "private-content"}]},
                },
            })
        self.assertIn("error", result)
        for value in (str(result), output.getvalue()):
            self.assertNotIn("private-role", value)
            self.assertNotIn("private-content", value)

    async def test_sdk_propagates_cancellation_into_worker_cleanup(self):
        started = asyncio.Event()

        async def generate(payload):
            started.set()
            await asyncio.sleep(10)

        self.runtime.generate = generate
        task = asyncio.create_task(self.run_job(self.worker.handle, {
            "id": "test-job", "input": {"operation": "generate", "request": {"prompt": "hello"}},
        }))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.runtime.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
