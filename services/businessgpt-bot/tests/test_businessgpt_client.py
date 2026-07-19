import unittest

from bot.businessgpt_client import _prompt_endpoint_url


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


if __name__ == "__main__":
    unittest.main()
