from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace


TRUNCATION_MARKER = "\n[message truncated to fit context]\n"


class ContextBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class PromptMessage:
    role: str
    content: str
    name: str | None = None
    source_index: int | None = None


@dataclass(frozen=True)
class ContextFit:
    prompt: str
    prompt_tokens: int
    messages: tuple[PromptMessage, ...]
    input_truncated: bool
    dropped_messages: int
    truncated_characters: int


def _message_content(message: PromptMessage) -> str:
    content = message.content.strip()
    if message.role == "user" and message.name:
        return f"{message.name.strip()}: {content}"
    return content


def format_messages(messages: list[PromptMessage], system_prompt: str) -> str:
    parts = [f"<|im_start|>system\n{system_prompt}<|im_end|>"]
    for message in messages:
        if message.role == "system":
            continue
        parts.append(f"<|im_start|>{message.role}\n{_message_content(message)}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def _truncated_content(content: str, retained_characters: int) -> str:
    content = content.strip()
    if retained_characters <= 0:
        return TRUNCATION_MARKER.strip()
    if retained_characters >= len(content):
        return content

    head_characters = retained_characters // 4
    tail_characters = retained_characters - head_characters
    head = content[:head_characters].rstrip() if head_characters else ""
    tail = content[-tail_characters:].lstrip() if tail_characters else ""
    return f"{head}{TRUNCATION_MARKER}{tail}".strip()


def _trim_newest_message(
    messages: list[PromptMessage],
    system_prompt: str,
    prompt_budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> tuple[list[PromptMessage], str, int, int]:
    newest = messages[-1]
    original_content = newest.content.strip()

    def candidate(retained_characters: int) -> tuple[list[PromptMessage], str, int]:
        trimmed = replace(
            newest,
            content=_truncated_content(original_content, retained_characters),
        )
        candidate_messages = [*messages[:-1], trimmed]
        prompt = format_messages(candidate_messages, system_prompt)
        return candidate_messages, prompt, count_tokens(prompt)

    best_messages, best_prompt, best_tokens = candidate(0)
    if best_tokens > prompt_budget_tokens:
        raise ContextBudgetError(
            "system prompt, message envelope, and requested completion exceed context window"
        )

    low = 1
    high = max(0, len(original_content) - 1)
    best_retained = 0
    while low <= high:
        middle = (low + high) // 2
        candidate_messages, prompt, prompt_tokens = candidate(middle)
        if prompt_tokens <= prompt_budget_tokens:
            best_messages = candidate_messages
            best_prompt = prompt
            best_tokens = prompt_tokens
            best_retained = middle
            low = middle + 1
        else:
            high = middle - 1

    return (
        best_messages,
        best_prompt,
        best_tokens,
        len(original_content) - best_retained,
    )


def fit_messages_to_context(
    *,
    messages: list[PromptMessage],
    system_prompt: str,
    prompt_budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> ContextFit:
    if prompt_budget_tokens <= 0:
        raise ContextBudgetError("requested completion leaves no room for input")

    retained = [message for message in messages if message.role != "system"]
    if not retained:
        raise ContextBudgetError("at least one non-system message is required")

    original_characters = sum(len(message.content.strip()) for message in retained)
    dropped_messages = 0
    prompt = format_messages(retained, system_prompt)
    prompt_tokens = count_tokens(prompt)

    while prompt_tokens > prompt_budget_tokens and len(retained) > 1:
        retained.pop(0)
        dropped_messages += 1
        prompt = format_messages(retained, system_prompt)
        prompt_tokens = count_tokens(prompt)

    truncated_characters = sum(
        len(message.content.strip()) for message in messages if message.role != "system"
    ) - sum(len(message.content.strip()) for message in retained)

    if prompt_tokens > prompt_budget_tokens:
        retained, prompt, prompt_tokens, trimmed_characters = _trim_newest_message(
            retained,
            system_prompt,
            prompt_budget_tokens,
            count_tokens,
        )
        truncated_characters += trimmed_characters

    return ContextFit(
        prompt=prompt,
        prompt_tokens=prompt_tokens,
        messages=tuple(retained),
        input_truncated=dropped_messages > 0 or truncated_characters > 0,
        dropped_messages=dropped_messages,
        truncated_characters=min(truncated_characters, original_characters),
    )
