from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .runpod_client import RunpodQueueClient, validate_prompt
from .validation import is_runpod_url, validate_allowed_api_url


def _prompt_endpoint_url(api_url: str) -> str:
    parsed = urlsplit(api_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/generate"):
        path = path[: -len("/generate")]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/prompt", "", ""))


class BusinessGPTClient:
    def __init__(
        self, api_key: str, allowed_api_urls: frozenset[str], *,
        runpod_api_key: str | None = None,
        runpod_timeout_seconds: int = 600,
        runpod_proxy_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._allowed_api_urls = allowed_api_urls
        self._runpod = (
            RunpodQueueClient(
                runpod_api_key or "", timeout_seconds=runpod_timeout_seconds,
                proxy_url=runpod_proxy_url,
            )
            if any(is_runpod_url(url) for url in allowed_api_urls)
            else None
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(310.0, connect=5.0, write=20.0, pool=5.0),
            follow_redirects=False,
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()
        if self._runpod is not None:
            await self._runpod.close()

    async def get_default_prompt(self, *, api_url: str) -> dict[str, Any]:
        api_url = validate_allowed_api_url(api_url, self._allowed_api_urls)
        if is_runpod_url(api_url):
            assert self._runpod is not None
            return validate_prompt(await self._runpod.run(api_url, {"operation": "prompt"}))
        response = await self._client.get(
            _prompt_endpoint_url(api_url),
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(10.0, connect=5.0),
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
        seed: int | None = None,
    ) -> dict[str, Any]:
        api_url = validate_allowed_api_url(api_url, self._allowed_api_urls)
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
        if seed is not None:
            payload["seed"] = seed

        if is_runpod_url(api_url):
            assert self._runpod is not None
            return await self._runpod.run(
                api_url, {"operation": "generate", "request": payload}
            )

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
