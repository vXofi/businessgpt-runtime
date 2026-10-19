from __future__ import annotations

import base64
import json
import unittest

import httpx

from bot.image_generation_client import (
    PNG_SIGNATURE,
    ImageGenerationClient,
    ImageGenerationError,
    normalize_image_api_url,
)


class ImageGenerationURLTests(unittest.TestCase):
    def test_normalizes_stream_endpoint(self):
        self.assertEqual(
            normalize_image_api_url(
                "https://images.example.test/v1/images/generations/stream/"
            ),
            "https://images.example.test/v1/images/generations/stream",
        )

    def test_rejects_credentials_query_and_other_paths(self):
        for url in (
            "http://images.example.test/v1/images/generations/stream",
            "https://key@images.example.test/v1/images/generations/stream",
            "https://images.example.test/v1/images/generations/stream?key=secret",
            "https://images.example.test/v1/images/generations",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_image_api_url(url)


class ImageGenerationClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = ImageGenerationClient(
            api_url="https://images.example.test/v1/images/generations/stream",
            api_key="image-secret",
            proxy_url=None,
            timeout_seconds=120,
        )

    async def asyncTearDown(self):
        await self.client.close()

    async def test_sends_fixed_defaults_and_returns_png(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            encoded = base64.b64encode(PNG_SIGNATURE + b"generated").decode()
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream; charset=utf-8"},
                content=(
                    'data: {"status":"queued","progress":0}\n\n'
                    f'data: {{"status":"complete","b64_json":"{encoded}"}}\n\n'
                ).encode(),
            )

        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )

        image = await self.client.generate(
            "private prompt",
            width=384,
            height=512,
            steps=6,
            guidance_scale=1.5,
            auto_translate=False,
        )

        self.assertEqual(image, PNG_SIGNATURE + b"generated")
        request = requests[0]
        self.assertEqual(request.headers["X-API-Key"], "image-secret")
        self.assertEqual(
            json.loads(request.content),
            {
                "prompt": "private prompt",
                "width": 384,
                "height": 512,
                "steps": 6,
                "guidance_scale": 1.5,
                "auto_translate": False,
                "response_format": "b64_json",
            },
        )

    async def test_rejects_non_png_response_without_exposing_body(self):
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    500,
                    headers={"Content-Type": "text/plain"},
                    content=b"upstream secret diagnostics",
                )
            )
        )

        with self.assertRaisesRegex(ImageGenerationError, "HTTP 500") as raised:
            await self.client.generate(
                "prompt",
                width=512,
                height=512,
                steps=4,
                guidance_scale=1.0,
                auto_translate=True,
            )
        self.assertNotIn("secret diagnostics", str(raised.exception))

    async def test_rejects_stream_without_complete_event(self):
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    content=b'data: {"status":"queued"}\n\n',
                )
            )
        )

        with self.assertRaisesRegex(ImageGenerationError, "before completion"):
            await self.client.generate(
                "prompt",
                width=512,
                height=512,
                steps=4,
                guidance_scale=1.0,
                auto_translate=True,
            )

    async def test_rejects_invalid_complete_image(self):
        encoded = base64.b64encode(b"not a png").decode()
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    content=(
                        f'data: {{"status":"complete","b64_json":"{encoded}"}}\n\n'
                    ).encode(),
                )
            )
        )

        with self.assertRaisesRegex(ImageGenerationError, "invalid PNG"):
            await self.client.generate(
                "prompt",
                width=512,
                height=512,
                steps=4,
                guidance_scale=1.0,
                auto_translate=True,
            )


if __name__ == "__main__":
    unittest.main()
