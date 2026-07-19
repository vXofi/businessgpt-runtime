import unittest
from unittest.mock import patch

from bot.context_cache import CachedMessage, ContextCache


class ContextCacheTests(unittest.TestCase):
    def test_is_bounded_deduplicated_and_filterable(self):
        cache = ContextCache(max_messages_per_chat=3)
        for message_id in range(1, 5):
            cache.add(10, CachedMessage(message_id, "user", str(message_id)))
        cache.add(10, CachedMessage(4, "user", "duplicate"))

        recent = cache.recent(10, 20, exclude_message_ids={3})

        self.assertEqual([message.message_id for message in recent], [2, 4])

    def test_evicts_idle_and_lru_chats(self):
        cache = ContextCache(max_messages_per_chat=2, max_chats=2, ttl_seconds=10)
        with patch("bot.context_cache.time.monotonic", return_value=0):
            cache.add(1, CachedMessage(1, "user", "one"))
            cache.add(2, CachedMessage(2, "user", "two"))
        with patch("bot.context_cache.time.monotonic", return_value=1):
            cache.add(3, CachedMessage(3, "user", "three"))
            self.assertEqual(cache.recent(1, 1), [])
        with patch("bot.context_cache.time.monotonic", return_value=20):
            self.assertEqual(cache.recent(2, 1), [])


if __name__ == "__main__":
    unittest.main()
