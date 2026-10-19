from __future__ import annotations

from typing import Any


def extract_backend_provenance(data: dict[str, Any], *, requested_seed: int | None) -> dict:
    """Normalize optional llama.cpp/OpenAI-compatible provenance fields."""
    choices = data.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    timings = data.get("timings") if isinstance(data.get("timings"), dict) else {}

    prompt_details = usage.get("prompt_tokens_details")
    cached_tokens = (
        prompt_details.get("cached_tokens")
        if isinstance(prompt_details, dict)
        else None
    )
    result = {
        "finish_reason": choice.get("finish_reason"),
        "seed": data.get("seed", requested_seed),
        "cached_prompt_tokens": cached_tokens,
    }
    timing_fields = {
        "prompt_n": "llama_prompt_tokens",
        "prompt_ms": "llama_prompt_ms",
        "prompt_per_token_ms": "llama_prompt_per_token_ms",
        "prompt_per_second": "llama_prompt_tokens_per_second",
        "predicted_n": "llama_predicted_tokens",
        "predicted_ms": "llama_predicted_ms",
        "predicted_per_token_ms": "llama_predicted_per_token_ms",
        "predicted_per_second": "llama_predicted_tokens_per_second",
    }
    for source, target in timing_fields.items():
        if timings.get(source) is not None:
            result[target] = timings[source]
    return result
