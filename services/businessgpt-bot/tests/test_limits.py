import unittest

from bot.limits import GenerationLimiter


class GenerationLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_rejections_do_not_consume_rate_quota(self):
        limiter = GenerationLimiter(
            max_concurrent=1,
            user_requests=1,
            chat_requests=2,
            window_seconds=60,
        )
        first = await limiter.try_start(chat_id=1, user_id=1)
        busy = await limiter.try_start(chat_id=1, user_id=2)
        self.assertTrue(first.allowed)
        self.assertEqual(busy.reason, "model is busy")

        await limiter.finish()
        retried = await limiter.try_start(chat_id=1, user_id=2)
        self.assertTrue(retried.allowed)
        await limiter.finish()

    async def test_concurrency_and_user_rate_limits(self):
        limiter = GenerationLimiter(
            max_concurrent=1,
            user_requests=2,
            chat_requests=3,
            window_seconds=60,
        )
        first = await limiter.try_start(chat_id=-1, user_id=10)
        busy = await limiter.try_start(chat_id=-1, user_id=11)
        self.assertTrue(first.allowed)
        self.assertEqual(busy.reason, "model is busy")
        await limiter.finish()
        second = await limiter.try_start(chat_id=-1, user_id=10)
        self.assertTrue(second.allowed)
        await limiter.finish()
        limited = await limiter.try_start(chat_id=-1, user_id=10)
        self.assertEqual(limited.reason, "user rate limit reached")


if __name__ == "__main__":
    unittest.main()
