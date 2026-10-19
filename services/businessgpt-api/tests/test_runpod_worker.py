import asyncio
import hashlib
import io
import signal
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from runpod_worker import APIRuntime, Worker, validate_input


PROMPT = {"name": "API default", "text": "private default"}
PROMPT["prompt_hash"] = hashlib.sha256(PROMPT["text"].encode()).hexdigest()


class WorkerInputTests(unittest.TestCase):
    def test_existing_request_validation_and_image_parts(self):
        operation, payload = validate_input({"input": {
            "operation": "generate",
            "request": {
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                ]}],
                "system_prompt": "preset",
                "seed": 42,
            },
        }})
        self.assertEqual(operation, "generate")
        self.assertEqual(payload["system_prompt"], "preset")
        self.assertEqual(payload["seed"], 42)
        self.assertEqual(payload["messages"][0]["content"][1]["type"], "image_url")

    def test_only_fixed_operations_and_fields_are_allowed(self):
        for value in (
            None, {}, {"input": []}, {"input": {"operation": "http", "url": "http://internal"}},
            {"input": {"operation": []}},
            {"input": {"operation": "prompt", "url": "http://internal"}},
            {"input": {"operation": "generate", "request": {"url": "http://internal"}}},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_input(value)

    def test_validation_errors_do_not_include_input(self):
        with self.assertRaisesRegex(ValueError, "failed validation") as error:
            validate_input({"input": {"operation": "generate", "request": {
                "messages": [{"role": "private text", "content": "private content"}],
            }}})
        self.assertNotIn("private", str(error.exception))

    def test_input_size_is_bounded(self):
        with patch("runpod_worker.MAX_INPUT_BYTES", 30), self.assertRaisesRegex(ValueError, "size limit"):
            validate_input({"input": {"operation": "generate", "request": {"prompt": "long" * 30}}})

    def test_generation_range_and_nonfinite_values_are_rejected(self):
        for fields in ({"temperature": float("nan")}, {"max_tokens": -1}, {"top_k": 5000}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_input({"input": {"operation": "generate", "request": fields}})


class RuntimeTests(unittest.TestCase):
    def test_runtime_overrides_unsafe_inherited_environment(self):
        runtime = APIRuntime()
        with patch.dict("os.environ", {
            "HOST": "0.0.0.0", "LLAMA_HOST": "0.0.0.0",
            "BUSINESSGPT_API_KEY": "old-secret",
            "BUSINESSGPT_ALLOW_UNVERIFIED_MODEL": "true",
            "BUSINESSGPT_N_PARALLEL": "8",
        }):
            env = runtime.environment()
        self.assertEqual(env["HOST"], "127.0.0.1")
        self.assertEqual(env["LLAMA_HOST"], "127.0.0.1")
        self.assertEqual(env["BUSINESSGPT_ALLOW_UNVERIFIED_MODEL"], "false")
        self.assertEqual(env["BUSINESSGPT_N_PARALLEL"], "1")
        self.assertEqual(env["LLAMA_ARG_LOG_VERBOSITY"], "4")
        self.assertNotEqual(env["BUSINESSGPT_API_KEY"], "old-secret")

    def test_log_reader_retains_only_cuda_offload_flags(self):
        runtime = APIRuntime()
        runtime._consume_logs(io.BytesIO(
            b"CUDA0 found\nload_tensors: offloaded 33/33 layers to GPU\n"
            b"secret chat and system prompt\n"
        ))
        self.assertTrue(runtime.cuda_seen)
        self.assertEqual(runtime.offload, (33, 33))
        self.assertNotIn("secret chat", str(vars(runtime)))

    def test_stop_kills_child_group_and_clears_readiness(self):
        runtime = APIRuntime()
        process = Mock(pid=42)
        runtime.process = process
        runtime.ready = {"ok": True}
        runtime.prompt = PROMPT
        with patch("runpod_worker.os.killpg") as kill:
            runtime.stop()
        self.assertEqual(kill.call_args_list[0].args, (42, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (42, signal.SIGKILL))
        self.assertIsNone(runtime.ready)
        self.assertIsNone(runtime.prompt)
        self.assertIsNone(runtime.process)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, generate=None):
        return SimpleNamespace(
            ready={"ok": True}, prompt=dict(PROMPT), stop=Mock(), start=Mock(),
            health=AsyncMock(return_value={"ok": True}),
            generate=generate or AsyncMock(return_value={
                "response": "answer", "usage": {"prompt_hash": PROMPT["prompt_hash"]},
            }),
        )

    async def test_default_prompt_is_returned_with_same_generation(self):
        runtime = self.runtime()
        result = await Worker(runtime).handle({"input": {
            "operation": "generate", "request": {"prompt": "hello"},
        }})
        self.assertEqual(result["default_prompt"], PROMPT)
        self.assertEqual(result["usage"]["prompt_hash"], PROMPT["prompt_hash"])
        runtime.start.assert_not_called()
        runtime.stop.assert_not_called()

    async def test_override_does_not_return_unrelated_default(self):
        runtime = self.runtime()
        result = await Worker(runtime).handle({"input": {
            "operation": "generate",
            "request": {"prompt": "hello", "system_prompt": "preset"},
        }})
        self.assertNotIn("default_prompt", result)
        self.assertEqual(runtime.generate.call_args.args[0]["system_prompt"], "preset")

    async def test_prompt_and_health_do_not_generate(self):
        runtime = self.runtime()
        worker = Worker(runtime)
        self.assertEqual(await worker.handle({"input": {"operation": "prompt"}}), PROMPT)
        self.assertEqual(await worker.handle({"input": {"operation": "health"}}), {"ok": True})
        runtime.generate.assert_not_awaited()
        runtime.health.assert_awaited_once()

    async def test_cancel_stops_underlying_generation(self):
        started = asyncio.Event()

        async def generate(payload):
            started.set()
            await asyncio.sleep(10)

        runtime = self.runtime(generate)
        task = asyncio.create_task(Worker(runtime).handle({"input": {
            "operation": "generate", "request": {"prompt": "hello"},
        }}))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        runtime.stop.assert_called_once()

    async def test_backend_failure_is_sanitized_and_cleaned_up(self):
        runtime = self.runtime(AsyncMock(side_effect=RuntimeError("private request text")))
        with self.assertRaisesRegex(RuntimeError, "worker generation failed") as error:
            await Worker(runtime).handle({"input": {
                "operation": "generate", "request": {"prompt": "hello"},
            }})
        self.assertNotIn("private", str(error.exception))
        runtime.stop.assert_called_once()

    async def test_cancel_during_recovery_stops_startup_without_leaking_a_process(self):
        runtime = self.runtime()
        runtime.ready = None
        started = threading.Event()

        def start(cancelled):
            started.set()
            if not cancelled.wait(timeout=1):
                raise AssertionError("startup was not cancelled")
            raise RuntimeError("startup cancelled")

        runtime.start = Mock(side_effect=start)
        task = asyncio.create_task(Worker(runtime).handle({"input": {"operation": "health"}}))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        runtime.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
