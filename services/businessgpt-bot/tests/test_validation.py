import math
import unittest

from bot.validation import (
    normalize_api_url,
    validate_allowed_api_url,
    validate_admission_value,
    validate_generation_value,
    validate_image_generation_value,
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

    def test_admission_limits_require_bounded_positive_integers(self):
        self.assertEqual(validate_admission_value("rate_limit_user_requests", 5), 5)
        for value in (0, 1.5, math.nan, 101):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_admission_value("rate_limit_user_requests", value)

    def test_image_settings_enforce_documented_limits(self):
        self.assertEqual(validate_image_generation_value("image_width", 384), 384)
        self.assertEqual(
            validate_image_generation_value("image_guidance_scale", 5.0), 5.0
        )
        self.assertEqual(validate_image_generation_value("image_steps", 16), 16)
        self.assertEqual(
            validate_image_generation_value("image_generation_enabled", 0), 0
        )
        for field, value in (
            ("image_width", 300),
            ("image_height", 576),
            ("image_steps", 17),
            ("image_guidance_scale", 5.1),
            ("image_guidance_scale", math.nan),
            ("image_auto_translate", 2),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_image_generation_value(field, value)


if __name__ == "__main__":
    unittest.main()
