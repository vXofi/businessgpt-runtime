from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


def _prompt_endpoint_url(api_url: str) -> str:
    parsed = urlsplit(api_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/generate"):
        path = path[: -len("/generate")]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/prompt", "", ""))


class BusinessGPTClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(360.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def get_default_prompt(self, *, api_url: str) -> dict[str, Any]:
        response = await self._client.get(
            _prompt_endpoint_url(api_url),
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        response.raise_for_status()
        return response.json()

    async def generate(
        self,
        *,
        api_url: str,
        messages: list[dict[str, Any]],
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        repetition_penalty: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "repetition_penalty": repetition_penalty,
        }
        if system_prompt:
            payload["system_prompt"] = system_prompt

        response = await self._client.post(
            api_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return response.json()
