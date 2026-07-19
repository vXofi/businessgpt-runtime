import math
import unittest

from bot.validation import (
    normalize_api_url,
    validate_allowed_api_url,
    validate_generation_value,
)


class ValidationTests(unittest.TestCase):
    def test_api_url_requires_exact_generate_endpoint(self):
        allowed = frozenset({"http://businessgpt-api:8000/generate"})
        self.assertEqual(
            validate_allowed_api_url(
                "http://businessgpt-api:8000/generate", allowed
            ),
            "http://businessgpt-api:8000/generate",
        )
        for value in (
            "http://businessgpt-api:8000/health",
            "http://businessgpt-api:8000/generate?next=http://metadata",
            "http://user:pass@businessgpt-api:8000/generate",
            "file:///etc/passwd",
            "http://127.0.0.1:8000/generate",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_allowed_api_url(value, allowed)

    def test_normalizes_trailing_slash_only(self):
        self.assertEqual(
            normalize_api_url("http://service:8000/generate/"),
            "http://service:8000/generate",
        )

    def test_rejects_non_finite_and_out_of_range_values(self):
        for value in (math.nan, math.inf, -1, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_generation_value("temperature", value)


if __name__ == "__main__":
    unittest.main()
