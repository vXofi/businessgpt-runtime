from __future__ import annotations

import math
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


def normalize_api_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("API URL scheme must be http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("API URL must contain a host and no credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("API URL must not contain a query or fragment")
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
