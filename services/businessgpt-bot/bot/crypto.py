from __future__ import annotations

import base64
import json
from typing import Any

from nacl.public import PrivateKey, PublicKey, SealedBox


class FeedbackEncryptor:
    def __init__(self, public_key_b64: str) -> None:
        public_key = PublicKey(base64.b64decode(public_key_b64))
        self._box = SealedBox(public_key)

    def encrypt_json(self, payload: dict[str, Any]) -> str:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        encrypted = self._box.encrypt(data.encode("utf-8"))
        return base64.b64encode(encrypted).decode("ascii")

    def encrypt_bytes(self, data: bytes) -> str:
        return base64.b64encode(self._box.encrypt(data)).decode("ascii")


def generate_keypair() -> tuple[str, str]:
    private_key = PrivateKey.generate()
    public_key = private_key.public_key
    public_b64 = base64.b64encode(bytes(public_key)).decode("ascii")
    private_b64 = base64.b64encode(bytes(private_key)).decode("ascii")
    return public_b64, private_b64


def decrypt_feedback_payload(encrypted_payload_b64: str, private_key_b64: str) -> dict[str, Any]:
    private_key = PrivateKey(base64.b64decode(private_key_b64))
    box = SealedBox(private_key)
    decrypted = box.decrypt(base64.b64decode(encrypted_payload_b64))
    return json.loads(decrypted.decode("utf-8"))


def decrypt_feedback_bytes(encrypted_payload_b64: str, private_key_b64: str) -> bytes:
    private_key = PrivateKey(base64.b64decode(private_key_b64))
    return SealedBox(private_key).decrypt(base64.b64decode(encrypted_payload_b64))


if __name__ == "__main__":
    public_b64, private_b64 = generate_keypair()
    print(f"BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64={public_b64}")
    print(f"PRIVATE_KEY_B64_KEEP_ON_PC={private_b64}")
