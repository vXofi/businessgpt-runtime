from __future__ import annotations

import asyncio
import unittest

from api_security import SecurityLimitsMiddleware


def scope(*, authorization: str | None = "Bearer secret", content_length: int | None = None):
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {"type": "http", "method": "POST", "path": "/generate", "headers": headers}


async def invoke(middleware, request_scope, messages):
    sent = []
    incoming = iter(messages)

    async def receive():
        return next(incoming)

    async def send(message):
        sent.append(message)

    await middleware(request_scope, receive, send)
    return sent


class SecurityMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_insecure_mode_does_not_require_an_empty_bearer_header(self) -> None:
        async def app(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = SecurityLimitsMiddleware(
            app, api_key="", max_body_bytes=10, max_generations=1
        )
        sent = await invoke(
            middleware,
            scope(authorization=None),
            [{"type": "http.request", "body": b""}],
        )
        self.assertEqual(sent[0]["status"], 200)

    async def test_rejects_auth_before_reading_body(self) -> None:
        reads = 0

        async def app(_scope, receive, _send):
            nonlocal reads
            reads += 1
            await receive()

        middleware = SecurityLimitsMiddleware(
            app, api_key="secret", max_body_bytes=10, max_generations=1
        )
        sent = await invoke(
            middleware,
            scope(authorization=None),
            [{"type": "http.request", "body": b"large"}],
        )
        self.assertEqual(sent[0]["status"], 401)
        self.assertEqual(reads, 0)

    async def test_rejects_declared_and_streamed_oversize_bodies(self) -> None:
        async def app(_scope, receive, send):
            while (await receive()).get("more_body"):
                pass
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = SecurityLimitsMiddleware(
            app, api_key="secret", max_body_bytes=5, max_generations=1
        )
        declared = await invoke(
            middleware,
            scope(content_length=6),
            [{"type": "http.request", "body": b""}],
        )
        streamed = await invoke(
            middleware,
            scope(),
            [
                {"type": "http.request", "body": b"123", "more_body": True},
                {"type": "http.request", "body": b"456", "more_body": False},
            ],
        )
        self.assertEqual(declared[0]["status"], 413)
        self.assertEqual(streamed[0]["status"], 413)

    async def test_rejects_parallel_generation_and_releases_slot(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def app(_scope, receive, send):
            await receive()
            entered.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = SecurityLimitsMiddleware(
            app, api_key="secret", max_body_bytes=10, max_generations=1
        )
        first = asyncio.create_task(
            invoke(
                middleware,
                scope(),
                [{"type": "http.request", "body": b"x", "more_body": False}],
            )
        )
        await entered.wait()
        second = await invoke(
            middleware,
            scope(),
            [{"type": "http.request", "body": b"x", "more_body": False}],
        )
        self.assertEqual(second[0]["status"], 429)
        release.set()
        self.assertEqual((await first)[0]["status"], 200)


if __name__ == "__main__":
    unittest.main()
