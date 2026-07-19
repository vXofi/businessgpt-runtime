from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable


ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


class RequestBodyTooLarge(Exception):
    pass


class SecurityLimitsMiddleware:
    """Authenticate and reserve generation capacity before reading request bodies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_key: str,
        max_body_bytes: int,
        max_generations: int,
        retry_after_seconds: int = 5,
    ) -> None:
        self.app = app
        self.api_key = api_key
        self.max_body_bytes = max_body_bytes
        self.max_generations = max_generations
        self.retry_after_seconds = retry_after_seconds
        self._active_generations = 0
        self._admission_lock = asyncio.Lock()

    @staticmethod
    async def _json_response(
        send: Callable[[dict], Awaitable[None]],
        status: int,
        detail: str,
        *,
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        response_headers.extend(headers or [])
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body})

    def _authorized(self, scope: dict) -> bool:
        if not self.api_key:
            return True
        headers = {name.lower(): value for name, value in scope.get("headers", [])}
        supplied = headers.get(b"authorization", b"").decode("latin-1")
        return secrets.compare_digest(supplied, f"Bearer {self.api_key}")

    @staticmethod
    def _content_length(scope: dict) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return -1
        return None

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        protected = path in {"/generate", "/prompt", "/health/ready"}
        if protected and not self._authorized(scope):
            await self._json_response(send, 401, "invalid api key")
            return

        declared_length = self._content_length(scope)
        if declared_length == -1:
            await self._json_response(send, 400, "invalid content-length")
            return
        if declared_length is not None and declared_length > self.max_body_bytes:
            await self._json_response(send, 413, "request body too large")
            return

        admitted = False
        if path == "/generate":
            async with self._admission_lock:
                if self._active_generations >= self.max_generations:
                    await self._json_response(
                        send,
                        429,
                        "generation capacity is busy",
                        headers=[
                            (b"retry-after", str(self.retry_after_seconds).encode("ascii"))
                        ],
                    )
                    return
                self._active_generations += 1
                admitted = True

        received = 0

        async def limited_receive() -> dict:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        response_started = False

        async def tracked_send(message: dict) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                await self._json_response(send, 413, "request body too large")
        finally:
            if admitted:
                async with self._admission_lock:
                    self._active_generations -= 1
