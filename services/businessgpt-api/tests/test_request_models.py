from __future__ import annotations

import math
import unittest

from pydantic import ValidationError

from request_models import (
    MAX_CONTENT_PARTS_PER_MESSAGE,
    MAX_REQUEST_TEXT_CHARACTERS,
    GenerateRequest,
)


class RequestModelTests(unittest.TestCase):
    def test_limits_plain_message_strings(self) -> None:
        with self.assertRaises(ValidationError):
            GenerateRequest(messages=[{"role": "user", "content": "x" * 65_537}])

    def test_limits_parts_per_message(self) -> None:
        parts = [
            {"type": "text", "text": str(index)}
            for index in range(MAX_CONTENT_PARTS_PER_MESSAGE + 1)
        ]
        with self.assertRaises(ValidationError):
            GenerateRequest(messages=[{"role": "user", "content": parts}])

    def test_counts_discarded_system_messages_in_total(self) -> None:
        messages = [
            {"role": "system", "content": "x" * 65_536}
            for _ in range((MAX_REQUEST_TEXT_CHARACTERS // 65_536) + 1)
        ]
        with self.assertRaises(ValidationError):
            GenerateRequest(messages=messages)

    def test_rejects_non_finite_sampling_values(self) -> None:
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                GenerateRequest(prompt="test", temperature=value)

    def test_validates_optional_seed(self) -> None:
        self.assertEqual(GenerateRequest(prompt="test", seed=42).seed, 42)
        with self.assertRaises(ValidationError):
            GenerateRequest(prompt="test", seed=-1)


if __name__ == "__main__":
    unittest.main()
