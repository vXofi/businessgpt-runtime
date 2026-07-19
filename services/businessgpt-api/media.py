from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError


ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
DATA_URL_RE = re.compile(
    r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\r\n]+)$",
    flags=re.IGNORECASE,
)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_EDGE = 1280
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_FORMAT_TO_MIME_TYPE = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

# Pillow's global is process-wide. Set it once rather than mutating it per request.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ImageValidationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedImage:
    data_url: str
    mime_type: str
    source_sha256: str
    normalized_sha256: str
    source_bytes: int
    normalized_bytes: int
    width: int
    height: int


def normalize_image_data_url(value: str) -> NormalizedImage:
    match = DATA_URL_RE.fullmatch(value.strip())
    if not match:
        raise ImageValidationError(
            "image_url must be a base64 JPEG, PNG, or WebP data URL"
        )

    mime_type = match.group(1).lower()
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ImageValidationError("unsupported image MIME type")

    try:
        source = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageValidationError("image_url contains invalid base64") from exc
    if not source:
        raise ImageValidationError("image must not be empty")
    if len(source) > MAX_IMAGE_BYTES:
        raise ImageValidationError("image exceeds the 10 MB limit")

    try:
        with Image.open(io.BytesIO(source)) as opened:
            detected_mime_type = IMAGE_FORMAT_TO_MIME_TYPE.get(str(opened.format).upper())
            if detected_mime_type is None:
                raise ImageValidationError("unsupported detected image format")
            if detected_mime_type != mime_type:
                raise ImageValidationError("declared image MIME type does not match image data")
            if opened.width <= 0 or opened.height <= 0:
                raise ImageValidationError("image has invalid dimensions")
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise ImageValidationError("image dimensions are too large")
            opened.load()
            image = ImageOps.exif_transpose(opened)
            if image.width <= 0 or image.height <= 0:
                raise ImageValidationError("image has invalid dimensions")
            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                image = background.convert("RGB")
            else:
                image = image.convert("RGB")
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90, optimize=True)
            normalized = output.getvalue()
            width, height = image.size
    except Image.DecompressionBombError as exc:
        raise ImageValidationError("image dimensions are too large") from exc
    except ImageValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError("image data could not be decoded") from exc
    encoded = base64.b64encode(normalized).decode("ascii")
    return NormalizedImage(
        data_url=f"data:image/jpeg;base64,{encoded}",
        mime_type="image/jpeg",
        source_sha256=hashlib.sha256(source).hexdigest(),
        normalized_sha256=hashlib.sha256(normalized).hexdigest(),
        source_bytes=len(source),
        normalized_bytes=len(normalized),
        width=width,
        height=height,
    )
