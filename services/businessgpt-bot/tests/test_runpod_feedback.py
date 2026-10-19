import copy
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.handlers import _generate_and_reply_inner
from test_generation_handler_queue import make_update


URL = "https://api.runpod.ai/v2/endpoint123"
PROMPT = {"name": "API default", "text": "exact model default"}
PROMPT["prompt_hash"] = hashlib.sha256(PROMPT["text"].encode()).hexdigest()


class RunpodFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def generate(self, prompt_metadata, *, digest=None):
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-100, message_id=99))
        update = make_update(reply)
        update.effective_message.from_user.username = "tester"
        db = Mock()
        db.get_active_prompt.return_value = None
        db.get_active_model_profile.return_value = {"id": 1, "name": "default", "api_url": URL}
        db.get_effective_generation_settings.return_value = {
            "recent_context_messages": 10, "max_tokens": 128, "temperature": 0.9,
            "top_p": 0.9, "top_k": 50, "repetition_penalty": 1.2,
        }
        api = SimpleNamespace(
            get_default_prompt=AsyncMock(),
            generate=AsyncMock(return_value={
                "response": "hi @tester", "model": "model.gguf",
                "usage": {"prompt_hash": digest or PROMPT["prompt_hash"], "runpod_job_id": "job-1"},
                "default_prompt": copy.deepcopy(prompt_metadata),
            }),
        )
        encryptor = Mock()
        encryptor.encrypt_json.return_value = "ciphertext"
        context = SimpleNamespace(application=SimpleNamespace(bot_data={
            "config": SimpleNamespace(allowed_api_urls=frozenset({URL})),
            "db": db, "businessgpt": api, "feedback_encryptor": encryptor,
            "context_cache": Mock(), "media_cache": Mock(), "runtime_id": "runtime",
        }))
        messages = [
            {"role": "user", "content": "previous message"},
            {"role": "user", "content": "current message"},
        ]
        with patch("bot.handlers._image_source", return_value=None), \
             patch("bot.handlers._build_messages", new=AsyncMock(return_value=messages)), \
             patch("bot.handlers._typing", new=AsyncMock()):
            await _generate_and_reply_inner(
                update=update, context=context, user_text="hello",
                settings_chat_id=-100, request_trace_id="trace",
            )
        return reply, api, db, encryptor, messages

    async def test_one_job_preserves_full_context_and_exact_default_in_encrypted_sample(self):
        reply, api, db, encryptor, messages = await self.generate(PROMPT)
        api.get_default_prompt.assert_not_awaited()
        self.assertIsNone(api.generate.call_args.kwargs["system_prompt"])
        reply.assert_awaited_once_with("hi tester")
        payload = encryptor.encrypt_json.call_args.args[0]
        self.assertEqual(payload["api_request"]["messages"], messages)
        self.assertEqual(payload["api_request"]["system_prompt_text"], PROMPT["text"])
        self.assertTrue(payload["generation_provenance"]["prompt_hash_verified"])
        self.assertEqual(payload["api_response"]["usage"]["runpod_job_id"], "job-1")
        self.assertNotIn("default_prompt", payload["api_response"])
        self.assertEqual(
            db.insert_encrypted_feedback_sample.call_args.kwargs["encrypted_payload_b64"],
            "ciphertext",
        )

    async def test_unverifiable_prompt_does_not_send_answer_or_store_wrong_provenance(self):
        for metadata, digest in ((None, None), (PROMPT, "wrong")):
            with self.subTest(metadata=metadata):
                reply, api, db, encryptor, _ = await self.generate(metadata, digest=digest)
                self.assertTrue(reply.call_args.args[0].startswith("generation failed:"))
                api.get_default_prompt.assert_not_awaited()
                encryptor.encrypt_json.assert_not_called()
                db.insert_encrypted_feedback_sample.assert_not_called()


if __name__ == "__main__":
    unittest.main()
