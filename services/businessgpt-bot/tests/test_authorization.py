import unittest
from types import SimpleNamespace

from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop

from bot.handlers import authorize_update, _is_authorized_admin, _member_status_is_admin


class FakeBot:
    def __init__(self, status):
        self.status = status
        self.calls = []

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        return SimpleNamespace(status=self.status)


class FakeDB:
    def __init__(self, session_chat_id=None):
        self.session_chat_id = session_chat_id

    def get_admin_session_chat(self, user_id):
        return self.session_chat_id


def make_context(*, status, global_admin_ids=(), session_chat_id=None):
    bot = FakeBot(status)
    bot_data = {
        "config": SimpleNamespace(
            admin_user_ids=frozenset(global_admin_ids),
            owner_user_ids=frozenset(global_admin_ids),
            allowed_chat_ids=frozenset({-200, -300}),
        ),
        "db": FakeDB(session_chat_id),
        "businessgpt": object(),
        "feedback_encryptor": object(),
        "context_cache": object(),
        "media_cache": object(),
    }
    return SimpleNamespace(
        application=SimpleNamespace(bot_data=bot_data),
        bot=bot,
    )


def make_update(*, user_id=100, chat_id=-200, chat_type=ChatType.SUPERGROUP):
    user = SimpleNamespace(id=user_id)
    message = SimpleNamespace(
        chat=SimpleNamespace(type=chat_type),
        chat_id=chat_id,
    )
    return SimpleNamespace(effective_user=user, effective_message=message)


class AuthorizationTests(unittest.TestCase):
    def test_telegram_group_admin_statuses(self):
        self.assertTrue(_member_status_is_admin("creator"))
        self.assertTrue(_member_status_is_admin("administrator"))
        self.assertFalse(_member_status_is_admin("member"))
        self.assertFalse(_member_status_is_admin("restricted"))


class AuthorizationDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_allowed_chat_gate_stops_untrusted_group_before_handlers(self):
        context = make_context(status="member")
        update = make_update(chat_id=-999)
        update.effective_chat = SimpleNamespace(
            id=-999,
            type=ChatType.SUPERGROUP,
        )

        with self.assertRaises(ApplicationHandlerStop):
            await authorize_update(update, context)

    async def test_allowed_chat_gate_accepts_configured_group(self):
        context = make_context(status="member")
        update = make_update(chat_id=-200)
        update.effective_chat = SimpleNamespace(
            id=-200,
            type=ChatType.SUPERGROUP,
        )

        await authorize_update(update, context)

    async def test_group_admin_is_authorized_for_current_group(self):
        context = make_context(status="administrator")

        allowed = await _is_authorized_admin(make_update(), context)

        self.assertTrue(allowed)
        self.assertEqual(context.bot.calls, [(-200, 100)])

    async def test_ordinary_group_member_is_denied(self):
        context = make_context(status="member")

        self.assertFalse(await _is_authorized_admin(make_update(), context))

    async def test_private_editor_checks_session_group(self):
        context = make_context(status="creator", session_chat_id=-300)
        update = make_update(chat_id=100, chat_type=ChatType.PRIVATE)

        self.assertTrue(await _is_authorized_admin(update, context))
        self.assertEqual(context.bot.calls, [(-300, 100)])

    async def test_global_admin_does_not_need_group_lookup(self):
        context = make_context(status="member", global_admin_ids=(100,))

        self.assertTrue(await _is_authorized_admin(make_update(), context))
        self.assertEqual(context.bot.calls, [])


if __name__ == "__main__":
    unittest.main()
