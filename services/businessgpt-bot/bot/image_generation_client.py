from __future__ import annotations

import base64
import json
from binascii import Error as Base64Error
from collections.abc import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_GENERATED_IMAGE_BYTES = 16 * 1024 * 1024
MAX_SSE_EVENTS = 64
MAX_SSE_EVENT_BYTES = ((MAX_GENERATED_IMAGE_BYTES + 2) // 3) * 4 + 64 * 1024


class ImageGenerationError(RuntimeError):
    """A safe, user-facing image generation failure."""


def normalize_image_api_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https":
        raise ValueError("image API URL must use https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("image API URL must contain a host and no credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("image API URL must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path != "/v1/images/generations/stream":
        raise ValueError(
            "image API URL path must be /v1/images/generations/stream"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class ImageGenerationClient:
    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        proxy_url: str | None,
        timeout_seconds: int,
    ) -> None:
        self._api_url = normalize_image_api_url(api_url)
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            proxy=proxy_url,
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=10.0,
                write=10.0,
                pool=5.0,
            ),
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def generate(
        self,
        prompt: str,
        *,
        width: int,
        height: int,
        steps: int,
        guidance_scale: float,
        auto_translate: bool,
    ) -> bytes:
        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "guidance_scale": guidance_scale,
            "auto_translate": auto_translate,
            "response_format": "b64_json",
        }
        try:
            async with self._client.stream(
                "POST",
                self._api_url,
                headers={"X-API-Key": self._api_key},
                json=payload,
            ) as response:
                if response.status_code in {401, 403}:
                    raise ImageGenerationError("image service authentication failed")
                if response.status_code in {429, 502, 503, 504}:
                    raise ImageGenerationError("image service is busy or unavailable")
                if response.status_code != 200:
                    raise ImageGenerationError(
                        f"image service returned HTTP {response.status_code}"
                    )
                content_type = response.headers.get("content-type", "").partition(";")[0]
                if content_type != "text/event-stream":
                    raise ImageGenerationError(
                        "image service returned a non-stream response"
                    )
                image = await _read_generated_image(response)
        except ImageGenerationError:
            raise
        except httpx.TimeoutException as exc:
            raise ImageGenerationError("image generation timed out") from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationError("image service is unreachable") from exc

        return image


async def _read_generated_image(response: httpx.Response) -> bytes:
    event_count = 0
    async for raw_event in _iter_sse_data(response):
        event_count += 1
        if event_count > MAX_SSE_EVENTS:
            raise ImageGenerationError("image service returned too many events")
        try:
            event = json.loads(raw_event)
        except json.JSONDecodeError as exc:
            raise ImageGenerationError("image service returned invalid event data") from exc
        if not isinstance(event, dict):
            raise ImageGenerationError("image service returned invalid event data")
        status = event.get("status")
        if status in {"error", "failed"}:
            raise ImageGenerationError("image service failed to generate an image")
        if status != "complete":
            continue

        encoded = event.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ImageGenerationError("image service completion has no image")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (Base64Error, ValueError) as exc:
            raise ImageGenerationError("image service returned invalid Base64") from exc
        if len(image) > MAX_GENERATED_IMAGE_BYTES:
            raise ImageGenerationError("generated image is too large")
        if not image.startswith(PNG_SIGNATURE):
            raise ImageGenerationError("image service returned an invalid PNG")
        return image

    raise ImageGenerationError("image service stream ended before completion")


async def _iter_sse_data(response: httpx.Response) -> AsyncIterator[str]:
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(buffer[:newline]).rstrip(b"\r")
            del buffer[: newline + 1]
            value = _decode_sse_data_line(line)
            if value is not None:
                yield value
        if len(buffer) > MAX_SSE_EVENT_BYTES:
            raise ImageGenerationError("image service event is too large")
    if buffer:
        value = _decode_sse_data_line(bytes(buffer).rstrip(b"\r"))
        if value is not None:
            yield value


def _decode_sse_data_line(line: bytes) -> str | None:
    if len(line) > MAX_SSE_EVENT_BYTES:
        raise ImageGenerationError("image service event is too large")
    if not line.startswith(b"data:"):
        return None
    try:
        value = line.removeprefix(b"data:").strip().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImageGenerationError("image service returned invalid event data") from exc
    return value or None
