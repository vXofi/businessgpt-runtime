from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from model_verification import ModelVerificationError, verify_model_files


class ModelVerificationTests(unittest.TestCase):
    def test_rejects_malformed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            model = root / "model.gguf"
            manifest.write_text('{"schema_version": 1}', encoding="utf-8")
            model.write_bytes(b"model")

            with self.assertRaisesRegex(ModelVerificationError, "invalid structure"):
                verify_model_files(
                    manifest_path=str(manifest),
                    model_path=str(model),
                    projector_path=None,
                    allow_unverified=False,
                )

    def test_verifies_manifest_hashes_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.gguf"
            projector = root / "projector.gguf"
            model.write_bytes(b"model")
            projector.write_bytes(b"projector")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "language_model": {
                            "production_filename": model.name,
                            "production_sha256": hashlib.sha256(b"model").hexdigest(),
                        },
                        "vision_projector": {
                            "production_filename": projector.name,
                            "sha256": hashlib.sha256(b"projector").hexdigest(),
                        },
                    }
                )
            )
            hashes = verify_model_files(
                manifest_path=str(manifest),
                model_path=str(model),
                projector_path=str(projector),
                allow_unverified=False,
            )
            self.assertEqual(hashes[0], hashlib.sha256(b"model").hexdigest())
            model.write_bytes(b"tampered")
            with self.assertRaises(ModelVerificationError):
                verify_model_files(
                    manifest_path=str(manifest),
                    model_path=str(model),
                    projector_path=str(projector),
                    allow_unverified=False,
                )


if __name__ == "__main__":
    unittest.main()
