from __future__ import annotations

import unittest

from context_window import (
    ContextBudgetError,
    PromptMessage,
    fit_messages_to_context,
    format_messages,
)


def character_token_count(text: str) -> int:
    return len(text)


class ContextWindowTests(unittest.TestCase):
    def test_keeps_input_when_it_fits(self) -> None:
        messages = [PromptMessage(role="user", content="hello")]
        full_prompt = format_messages(messages, "system")

        result = fit_messages_to_context(
            messages=messages,
            system_prompt="system",
            prompt_budget_tokens=len(full_prompt),
            count_tokens=character_token_count,
        )

        self.assertEqual(result.prompt, full_prompt)
        self.assertEqual(result.messages, tuple(messages))
        self.assertFalse(result.input_truncated)
        self.assertEqual(result.dropped_messages, 0)

    def test_drops_oldest_message_before_touching_newest(self) -> None:
        messages = [
            PromptMessage(role="user", content="old context"),
            PromptMessage(role="user", content="new request"),
        ]
        newest_only = format_messages(messages[-1:], "system")

        result = fit_messages_to_context(
            messages=messages,
            system_prompt="system",
            prompt_budget_tokens=len(newest_only),
            count_tokens=character_token_count,
        )

        self.assertNotIn("old context", result.prompt)
        self.assertIn("new request", result.prompt)
        self.assertEqual(result.dropped_messages, 1)
        self.assertEqual(result.messages[0].content, "new request")
        self.assertTrue(result.input_truncated)

    def test_trims_long_newest_message_and_keeps_both_ends(self) -> None:
        content = "BEGIN-" + ("x" * 500) + "-END"
        minimal = format_messages(
            [PromptMessage(role="user", content="[message truncated to fit context]")],
            "system",
        )

        result = fit_messages_to_context(
            messages=[PromptMessage(role="user", content=content)],
            system_prompt="system",
            prompt_budget_tokens=len(minimal) + 80,
            count_tokens=character_token_count,
        )

        self.assertIn("BEGIN", result.prompt)
        self.assertIn("END", result.prompt)
        self.assertIn("message truncated to fit context", result.prompt)
        self.assertLessEqual(result.prompt_tokens, len(minimal) + 80)
        self.assertGreater(result.truncated_characters, 0)

    def test_rejects_when_system_and_envelope_cannot_fit(self) -> None:
        with self.assertRaises(ContextBudgetError):
            fit_messages_to_context(
                messages=[PromptMessage(role="user", content="hello")],
                system_prompt="system",
                prompt_budget_tokens=1,
                count_tokens=character_token_count,
            )


if __name__ == "__main__":
    unittest.main()
