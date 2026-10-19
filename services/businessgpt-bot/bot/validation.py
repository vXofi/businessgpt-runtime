from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


GENERATION_RANGES: dict[str, tuple[float, float]] = {
    "recent_context_messages": (0, 20),
    "max_tokens": (1, 1024),
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 1.0),
    "top_k": (0, 200),
    "repetition_penalty": (0.0, 3.0),
}
INTEGER_GENERATION_FIELDS = {
    "recent_context_messages",
    "max_tokens",
    "top_k",
}
ADMISSION_RANGES: dict[str, tuple[int, int]] = {
    "rate_limit_user_requests": (1, 100),
    "rate_limit_chat_requests": (1, 200),
}
IMAGE_GENERATION_RANGES: dict[str, tuple[float, float]] = {
    "image_width": (256, 512),
    "image_height": (256, 512),
    "image_steps": (2, 16),
    "image_guidance_scale": (0.0, 5.0),
}
INTEGER_IMAGE_GENERATION_FIELDS = {
    "image_width",
    "image_height",
    "image_steps",
}
BOOLEAN_IMAGE_GENERATION_FIELDS = {
    "image_generation_enabled",
    "image_auto_translate",
}


def is_runpod_url(value: str) -> bool:
    return urlsplit(value).hostname == "api.runpod.ai"


def normalize_api_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("API URL scheme must be http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("API URL must contain a host and no credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("API URL must not contain a query or fragment")
    if parsed.hostname == "api.runpod.ai":
        if parsed.scheme != "https" or parsed.port not in {None, 443}:
            raise ValueError("Runpod requires HTTPS on api.runpod.ai port 443")
        path = parsed.path.rstrip("/")
        if not re.fullmatch(r"/v2/[A-Za-z0-9_-]{1,64}", path):
            raise ValueError("Runpod API URL must be https://api.runpod.ai/v2/ENDPOINT_ID")
        return urlunsplit(("https", "api.runpod.ai", path, "", ""))
    if parsed.path.rstrip("/") != "/generate":
        raise ValueError("API URL path must be /generate")
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, "/generate", "", ""))


def validate_allowed_api_url(value: str, allowed_urls: frozenset[str]) -> str:
    normalized = normalize_api_url(value)
    if normalized not in allowed_urls:
        raise ValueError("API URL is not in BUSINESSGPT_BOT_ALLOWED_API_URLS")
    return normalized


def validate_generation_value(field: str, value: Any) -> int | float:
    bounds = GENERATION_RANGES.get(field)
    if bounds is None:
        raise ValueError(f"unsupported generation setting: {field}")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    minimum, maximum = bounds
    if number < minimum or number > maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}")
    if field in INTEGER_GENERATION_FIELDS:
        if not number.is_integer():
            raise ValueError(f"{field} must be an integer")
        return int(number)
    return number


def validate_generation_settings(**values: Any) -> dict[str, int | float]:
    return {name: validate_generation_value(name, value) for name, value in values.items()}


def validate_admission_value(field: str, value: Any) -> int:
    bounds = ADMISSION_RANGES.get(field)
    if bounds is None:
        raise ValueError(f"unsupported admission setting: {field}")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"{field} must be a finite integer")
    minimum, maximum = bounds
    if number < minimum or number > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return int(number)


def validate_admission_settings(**values: Any) -> dict[str, int]:
    return {name: validate_admission_value(name, value) for name, value in values.items()}


def validate_image_generation_value(field: str, value: Any) -> int | float:
    if field in BOOLEAN_IMAGE_GENERATION_FIELDS:
        if isinstance(value, bool):
            return int(value)
        if str(value) not in {"0", "1"}:
            raise ValueError(f"{field} must be 0 or 1")
        return int(value)

    bounds = IMAGE_GENERATION_RANGES.get(field)
    if bounds is None:
        raise ValueError(f"unsupported image generation setting: {field}")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    minimum, maximum = bounds
    if number < minimum or number > maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}")
    if field in INTEGER_IMAGE_GENERATION_FIELDS:
        if not number.is_integer():
            raise ValueError(f"{field} must be an integer")
        integer = int(number)
        if field in {"image_width", "image_height"} and integer % 64 != 0:
            raise ValueError(f"{field} must be a multiple of 64")
        return integer
    return number


def validate_image_generation_settings(**values: Any) -> dict[str, int | float]:
    return {
        name: validate_image_generation_value(name, value)
        for name, value in values.items()
    }
