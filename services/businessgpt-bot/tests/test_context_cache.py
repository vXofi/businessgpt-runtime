import unittest

from bot.context_cache import CachedMessage, ContextCache


class ContextCacheTests(unittest.TestCase):
    def test_is_bounded_deduplicated_and_filterable(self):
        cache = ContextCache(max_messages_per_chat=3)
        for message_id in range(1, 5):
            cache.add(10, CachedMessage(message_id, "user", str(message_id)))
        cache.add(10, CachedMessage(4, "user", "duplicate"))

        recent = cache.recent(10, 20, exclude_message_ids={3})

        self.assertEqual([message.message_id for message in recent], [2, 4])


if __name__ == "__main__":
    unittest.main()
