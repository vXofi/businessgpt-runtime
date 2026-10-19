import json
import unittest

import httpx

from bot.businessgpt_client import BusinessGPTClient, _prompt_endpoint_url


class BusinessGPTClientTests(unittest.TestCase):
    def test_prompt_endpoint_replaces_generate_path(self):
        self.assertEqual(
            _prompt_endpoint_url("http://businessgpt-api:8000/generate"),
            "http://businessgpt-api:8000/prompt",
        )

    def test_prompt_endpoint_preserves_api_prefix(self):
        self.assertEqual(
            _prompt_endpoint_url("https://example.test/v1/generate?ignored=true"),
            "https://example.test/v1/prompt",
        )


class BusinessGPTClientSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_disallowed_endpoint_before_sending_api_key(self):
        client = BusinessGPTClient(
            "production-secret",
            frozenset({"http://businessgpt-api:8000/generate"}),
        )
        try:
            with self.assertRaisesRegex(ValueError, "not in"):
                await client.generate(
                    api_url="https://attacker.example/generate",
                    messages=[{"role": "user", "content": "hello"}],
                    system_prompt="prompt",
                    max_tokens=128,
                    temperature=0.9,
                    top_p=0.9,
                    top_k=50,
                    repetition_penalty=1.2,
                )
        finally:
            await client.close()

    async def test_sends_seed_only_when_requested(self):
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"response": "ok"})

        api_url = "http://businessgpt-api:8000/generate"
        client = BusinessGPTClient("secret", frozenset({api_url}))
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        arguments = {
            "api_url": api_url,
            "messages": [{"role": "user", "content": "hello"}],
            "system_prompt": "prompt",
            "max_tokens": 128,
            "temperature": 0.9,
            "top_p": 0.9,
            "top_k": 50,
            "repetition_penalty": 1.2,
        }
        try:
            await client.generate(**arguments)
            await client.generate(**arguments, seed=42)
        finally:
            await client.close()
        self.assertNotIn("seed", payloads[0])
        self.assertEqual(payloads[1]["seed"], 42)


if __name__ == "__main__":
    unittest.main()
