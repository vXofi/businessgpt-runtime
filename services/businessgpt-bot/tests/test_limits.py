import asyncio
import unittest

from bot.limits import GenerationQueue


async def wait_for_event(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=1)


class GenerationQueueTests(unittest.IsolatedAsyncioTestCase):
    def make_queue(
        self,
        *,
        max_concurrent: int = 1,
        max_queue_size: int = 2,
        user_requests: int = 3,
        chat_requests: int = 10,
    ) -> GenerationQueue:
        queue = GenerationQueue(
            max_concurrent=max_concurrent,
            max_queue_size=max_queue_size,
            user_requests=user_requests,
            chat_requests=chat_requests,
            window_seconds=60,
        )
        queue.start()
        self.addAsyncCleanup(queue.shutdown)
        return queue

    async def test_jobs_run_in_fifo_order_instead_of_rejecting_busy(self):
        queue = self.make_queue()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        order: list[int] = []

        async def first() -> None:
            order.append(1)
            first_started.set()
            await release_first.wait()

        async def second() -> None:
            order.append(2)

        first_admission = await queue.submit(chat_id=-1, user_id=1, run=first)
        await wait_for_event(first_started)
        second_admission = await queue.submit(chat_id=-1, user_id=2, run=second)

        self.assertTrue(first_admission.allowed)
        self.assertFalse(first_admission.queued)
        self.assertTrue(second_admission.allowed)
        self.assertTrue(second_admission.queued)
        self.assertEqual(second_admission.position, 1)
        self.assertEqual(order, [1])

        release_first.set()
        await asyncio.wait_for(queue.join(), timeout=1)
        self.assertEqual(order, [1, 2])

    async def test_full_queue_rejection_does_not_consume_rate_quota(self):
        queue = self.make_queue(max_queue_size=1, user_requests=1)
        release_first = asyncio.Event()
        release_second = asyncio.Event()
        first_started = asyncio.Event()
        second_started = asyncio.Event()

        async def first() -> None:
            first_started.set()
            await release_first.wait()

        async def second() -> None:
            second_started.set()
            await release_second.wait()

        async def third() -> None:
            return None

        await queue.submit(chat_id=-1, user_id=1, run=first)
        await wait_for_event(first_started)
        queued = await queue.submit(chat_id=-1, user_id=2, run=second)
        full = await queue.submit(chat_id=-1, user_id=3, run=third)
        self.assertTrue(queued.queued)
        self.assertEqual(full.reason, "generation queue is full")

        release_first.set()
        await wait_for_event(second_started)
        retried = await queue.submit(chat_id=-1, user_id=3, run=third)
        self.assertTrue(retried.allowed)
        release_second.set()
        await asyncio.wait_for(queue.join(), timeout=1)

    async def test_accepted_queued_work_consumes_user_rate_quota(self):
        queue = self.make_queue(user_requests=1)
        release = asyncio.Event()
        started = asyncio.Event()

        async def blocking() -> None:
            started.set()
            await release.wait()

        async def no_op() -> None:
            return None

        await queue.submit(chat_id=-1, user_id=1, run=blocking)
        await wait_for_event(started)
        queued = await queue.submit(chat_id=-1, user_id=2, run=no_op)
        limited = await queue.submit(chat_id=-1, user_id=2, run=no_op)

        self.assertTrue(queued.allowed)
        self.assertEqual(limited.reason, "user rate limit reached")
        release.set()
        await asyncio.wait_for(queue.join(), timeout=1)

    async def test_multiple_workers_respect_configured_concurrency(self):
        queue = self.make_queue(max_concurrent=2, max_queue_size=1)
        release = asyncio.Event()
        both_started = asyncio.Event()
        active = 0
        maximum_active = 0

        async def blocking() -> None:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            if active == 2:
                both_started.set()
            await release.wait()
            active -= 1

        first = await queue.submit(chat_id=-1, user_id=1, run=blocking)
        second = await queue.submit(chat_id=-1, user_id=2, run=blocking)
        await wait_for_event(both_started)

        self.assertFalse(first.queued)
        self.assertFalse(second.queued)
        self.assertEqual(maximum_active, 2)
        release.set()
        await asyncio.wait_for(queue.join(), timeout=1)

    async def test_submission_can_override_default_user_limit(self):
        queue = self.make_queue(user_requests=1)

        async def no_op() -> None:
            return None

        first = await queue.submit(
            chat_id=-1,
            user_id=1,
            run=no_op,
            user_requests=2,
        )
        await queue.join()
        second = await queue.submit(
            chat_id=-1,
            user_id=1,
            run=no_op,
            user_requests=2,
        )
        await queue.join()
        limited = await queue.submit(
            chat_id=-1,
            user_id=1,
            run=no_op,
            user_requests=2,
        )

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertEqual(limited.reason, "user rate limit reached")

    async def test_user_rate_is_scoped_to_chat(self):
        queue = self.make_queue(user_requests=1)

        async def no_op() -> None:
            return None

        first = await queue.submit(chat_id=-1, user_id=1, run=no_op)
        await queue.join()
        limited = await queue.submit(chat_id=-1, user_id=1, run=no_op)
        other_chat = await queue.submit(chat_id=-2, user_id=1, run=no_op)

        self.assertTrue(first.allowed)
        self.assertEqual(limited.reason, "user rate limit reached")
        self.assertTrue(other_chat.allowed)
        await queue.join()


if __name__ == "__main__":
    unittest.main()
