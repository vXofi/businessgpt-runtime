from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., str]


def noop_tool(*args: object, **kwargs: object) -> str:
    return "noop"


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "noop": ToolSpec(
        name="noop",
        description="Example placeholder tool. Not exposed to the model in v1.",
        handler=noop_tool,
    )
}
