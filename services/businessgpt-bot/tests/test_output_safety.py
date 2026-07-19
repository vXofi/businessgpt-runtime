import unittest

from bot.handlers import _disable_telegram_mentions


class OutputSafetyTests(unittest.TestCase):
    def test_removes_username_mentions_from_generated_output(self):
        self.assertEqual(
            _disable_telegram_mentions("Ask @alice or email a@b.test"),
            "Ask alice or email ab.test",
        )

    def test_leaves_text_without_at_signs_unchanged(self):
        self.assertEqual(_disable_telegram_mentions("plain answer"), "plain answer")


if __name__ == "__main__":
    unittest.main()
