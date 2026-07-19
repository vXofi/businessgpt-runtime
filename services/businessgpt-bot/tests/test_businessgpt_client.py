import unittest

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


if __name__ == "__main__":
    unittest.main()
