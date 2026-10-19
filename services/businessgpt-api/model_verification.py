from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ModelVerificationError(RuntimeError):
    pass


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_files(
    *,
    manifest_path: str,
    model_path: str,
    projector_path: str | None,
    allow_unverified: bool,
) -> tuple[str | None, str | None]:
    if allow_unverified:
        return None, None

    manifest_file = Path(manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelVerificationError(f"could not read model manifest: {exc}") from exc

    try:
        language_models = manifest.get("language_models")
        if language_models is None:
            language_models = [manifest["language_model"]]
        if not isinstance(language_models, list) or not language_models:
            raise TypeError
        expected = {
            item["production_filename"]: item["production_sha256"]
            for item in language_models
        }
        expected[manifest["vision_projector"]["production_filename"]] = manifest[
            "vision_projector"
        ]["sha256"]
    except (KeyError, TypeError) as exc:
        raise ModelVerificationError("model manifest has an invalid structure") from exc

    def verify(path_value: str | None, label: str) -> str | None:
        if not path_value:
            return None
        path = Path(path_value)
        wanted = expected.get(path.name)
        if wanted is None:
            raise ModelVerificationError(f"{label} is not listed in the model manifest: {path.name}")
        actual = sha256_file(path)
        if actual != wanted:
            raise ModelVerificationError(
                f"{label} hash mismatch: expected {wanted}, got {actual}"
            )
        return actual

    return verify(model_path, "model"), verify(projector_path, "projector")
