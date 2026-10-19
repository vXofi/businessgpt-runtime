import asyncio
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bot.businessgpt_client import BusinessGPTClient
from bot.runpod_client import MAX_RESPONSE_BYTES, RunpodError, RunpodQueueClient
from bot.validation import normalize_api_url


URL = "https://api.runpod.ai/v2/endpoint123"
JOB = "job-123-u1"
PROMPT = {"text": "trusted default", "name": "API default"}
PROMPT["prompt_hash"] = hashlib.sha256(PROMPT["text"].encode()).hexdigest()
PAYLOAD = {"operation": "generate", "request": {"messages": [{"role": "user", "content": "hello"}]}}


def completed():
    return {
        "id": JOB, "status": "COMPLETED", "delayTime": 12000, "executionTime": 500,
        "output": {
            "response": "answer", "usage": {"prompt_hash": PROMPT["prompt_hash"]},
            "default_prompt": dict(PROMPT),
        },
    }


class RunpodURLTests(unittest.TestCase):
    def test_canonical_endpoint_only(self):
        self.assertEqual(normalize_api_url(URL + "/"), URL)
        self.assertEqual(normalize_api_url(URL.replace(".ai", ".ai:443")), URL)
        for value in (
            URL.replace("https:", "http:"), URL + "/run", URL + "?key=secret",
            URL.replace(".ai", ".ai:8443"), URL.replace("api.runpod.ai", "user@api.runpod.ai"),
            "https://api.runpod.ai/v2/../generate",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_api_url(value)


class RunpodClientTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, handler, **kwargs):
        client = RunpodQueueClient("runpod-secret", poll_seconds=0.001, **kwargs)
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        self.addAsyncCleanup(client.close)
        return client

    async def test_queue_polling_preserves_usage_and_default_prompt(self):
        requests = []
        states = iter([
            {"id": JOB, "status": "IN_QUEUE"},
            {"id": JOB, "status": "IN_PROGRESS"},
            completed(),
        ])

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=next(states))

        client = await self.client(handler)
        with self.assertLogs("bot.runpod_client", level="INFO") as logs:
            result = await client.run(URL, PAYLOAD)
        self.assertEqual([r.method for r in requests], ["POST", "GET", "GET"])
        self.assertEqual(str(requests[0].url), URL + "/run")
        self.assertEqual(str(requests[-1].url), URL + "/status/" + JOB)
        body = json.loads(requests[0].content)
        self.assertEqual(body["input"], PAYLOAD)
        self.assertEqual(body["policy"], {"executionTimeout": 180000, "ttl": 630000})
        self.assertTrue(all(r.headers["Authorization"] == "Bearer runpod-secret" for r in requests))
        self.assertEqual(result["usage"]["runpod_job_id"], JOB)
        self.assertEqual(result["usage"]["runpod_delay_ms"], 12000)
        self.assertEqual(result["default_prompt"], PROMPT)
        self.assertNotIn("trusted default", "\n".join(logs.output))
        self.assertNotIn("runpod-secret", "\n".join(logs.output))

    async def test_failed_status_does_not_expose_provider_error_or_retry(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"id": JOB, "status": "FAILED", "error": "secret prompt"})

        client = await self.client(handler)
        with self.assertRaisesRegex(RunpodError, "job failed") as error:
            await client.run(URL, PAYLOAD)
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(len(requests), 1)

    async def test_timeout_cancels_known_job(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            return httpx.Response(200, json={"id": JOB, "status": "IN_QUEUE"})

        client = await self.client(handler, timeout_seconds=0.01)
        with self.assertRaisesRegex(RunpodError, "timed out"):
            await client.run(URL, PAYLOAD)
        self.assertEqual(paths[-1], "/v2/endpoint123/cancel/" + JOB)
        self.assertEqual(paths.count("/v2/endpoint123/run"), 1)

    async def test_task_cancellation_cancels_remote_job(self):
        polling = asyncio.Event()
        cancelled = []

        async def handler(request):
            if "/status/" in request.url.path:
                polling.set()
                await asyncio.sleep(10)
            if "/cancel/" in request.url.path:
                cancelled.append(request.url.path)
            return httpx.Response(200, json={"id": JOB, "status": "IN_QUEUE"})

        client = await self.client(handler)
        task = asyncio.create_task(client.run(URL, PAYLOAD))
        await asyncio.wait_for(polling.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(cancelled, ["/v2/endpoint123/cancel/" + JOB])

    async def test_ambiguous_submission_is_not_repeated(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("private error", request=request)

        client = await self.client(handler)
        with self.assertRaisesRegex(RunpodError, "not automatically resubmitted"):
            await client.run(URL, PAYLOAD)
        self.assertEqual(len(calls), 1)

    async def test_poll_failure_retries_status_not_submission(self):
        paths = []
        statuses = iter([200, 503, 200])

        def handler(request):
            paths.append(request.url.path)
            status = next(statuses)
            state = {"id": JOB, "status": "IN_QUEUE"} if len(paths) == 1 else completed()
            return httpx.Response(status, json=state)

        client = await self.client(handler)
        with patch("bot.runpod_client.asyncio.sleep", new_callable=AsyncMock):
            await client.run(URL, PAYLOAD)
        self.assertEqual(paths.count("/v2/endpoint123/run"), 1)
        self.assertEqual(paths.count("/v2/endpoint123/status/" + JOB), 2)

    async def test_disallowed_origin_rejected_before_credentials(self):
        handler = AsyncMock()
        client = await self.client(handler)
        with self.assertRaises(ValueError):
            await client.run("https://attacker.example/generate", PAYLOAD)
        handler.assert_not_awaited()

    async def test_redirect_is_not_followed(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(307, headers={"Location": "https://attacker.example"})

        client = await self.client(handler)
        with self.assertRaisesRegex(RunpodError, "HTTP 307"):
            await client.run(URL, PAYLOAD)
        self.assertEqual(len(calls), 1)

    async def test_request_limit_counts_utf8_and_envelope(self):
        handler = AsyncMock()
        client = await self.client(handler)
        with patch("bot.runpod_client.MAX_REQUEST_BYTES", 120):
            with self.assertRaisesRegex(RunpodError, "too large"):
                await client.run(URL, {"operation": "generate", "request": {"prompt": "\u044f" * 30}})
        handler.assert_not_awaited()

    async def test_nonfinite_input_rejected_before_post(self):
        handler = AsyncMock()
        client = await self.client(handler)
        with self.assertRaisesRegex(RunpodError, "invalid Runpod request"):
            await client.run(URL, {"temperature": float("nan")})
        handler.assert_not_awaited()

    async def test_response_limit_is_enforced(self):
        client = await self.client(lambda request: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)))
        with self.assertRaisesRegex(RunpodError, "response exceeds"):
            await client.run(URL, PAYLOAD)

    async def test_job_id_cannot_inject_paths(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"id": "../another-job", "status": "IN_QUEUE"})

        client = await self.client(handler)
        with self.assertRaisesRegex(RunpodError, "invalid job ID"):
            await client.run(URL, PAYLOAD)
        self.assertEqual(len(calls), 1)

    async def test_unknown_status_cancels_job(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            return httpx.Response(200, json={"id": JOB, "status": "unknown secret"})

        client = await self.client(handler)
        with self.assertRaisesRegex(RunpodError, "unknown job status"):
            await client.run(URL, PAYLOAD)
        self.assertTrue(paths[-1].endswith("/cancel/" + JOB))

    async def test_missing_or_mismatched_default_fails_closed(self):
        for default in (None, {**PROMPT, "prompt_hash": "wrong"}):
            result = completed()
            result["output"]["default_prompt"] = default
            client = await self.client(lambda request: httpx.Response(200, json=result))
            with self.subTest(default=default), self.assertRaises(RunpodError):
                await client.run(URL, PAYLOAD)

    async def test_direct_and_runpod_keys_never_cross(self):
        direct = "http://businessgpt-api:8000/generate"
        client = BusinessGPTClient(
            "direct-secret", frozenset({direct, URL}), runpod_api_key="runpod-secret"
        )
        self.addAsyncCleanup(client.close)
        seen = []

        def handler(request):
            seen.append((request.url.host, request.headers["Authorization"]))
            return httpx.Response(
                200, json=completed() if request.url.host == "api.runpod.ai" else {"response": "ok"}
            )

        await client._client.aclose()
        await client._runpod._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client._runpod._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        arguments = dict(
            messages=[{"role": "user", "content": "hi"}], system_prompt="preset",
            max_tokens=128, temperature=0.9, top_p=0.9, top_k=50, repetition_penalty=1.2,
        )
        await client.generate(api_url=direct, **arguments)
        await client.generate(api_url=URL, **arguments)
        self.assertEqual(seen, [
            ("businessgpt-api", "Bearer direct-secret"),
            ("api.runpod.ai", "Bearer runpod-secret"),
        ])
        with self.assertRaises(ValueError):
            await client.generate(api_url=URL.replace("endpoint123", "not-allowed"), **arguments)
        self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
