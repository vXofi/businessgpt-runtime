from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx

from .validation import is_runpod_url, normalize_api_url


logger = logging.getLogger(__name__)
# /run is limited to 10 MB, including Base64 and the JSON envelope.
MAX_REQUEST_BYTES = 9_500_000
MAX_RESPONSE_BYTES = 1_048_576
JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
PENDING = {"IN_QUEUE", "IN_PROGRESS"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


class RunpodError(httpx.HTTPError):
    """A safe, bounded message suitable for the Telegram error response."""


def validate_prompt(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RunpodError("Runpod returned no default prompt metadata")
    text, digest = value.get("text"), value.get("prompt_hash")
    if (
        not isinstance(text, str)
        or not 1 <= len(text) <= 65_536
        or hashlib.sha256(text.encode("utf-8")).hexdigest() != digest
    ):
        raise RunpodError("Runpod default prompt hash mismatch")
    return {"name": "API default", "text": text, "prompt_hash": digest}


class RunpodQueueClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 600,
        proxy_url: str | None = None,
        poll_seconds: float = 1,
    ) -> None:
        if not api_key:
            raise ValueError("BUSINESSGPT_RUNPOD_API_KEY is required")
        self._key = api_key
        self._timeout = timeout_seconds
        self._poll_seconds = poll_seconds
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=5, write=20, pool=5),
            proxy=proxy_url,
            follow_redirects=False,
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, url: str, *, content: bytes | None = None
    ) -> dict[str, Any]:
        async with self._client.stream(
            method,
            url,
            content=content,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        ) as response:
            # Never echo provider error bodies: they can contain request text.
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RunpodError("Runpod response exceeds the size limit")
        try:
            result = json.loads(body)
        except (ValueError, UnicodeError):
            raise RunpodError("Runpod returned invalid JSON") from None
        if not isinstance(result, dict):
            raise RunpodError("Runpod returned an invalid response")
        return result

    async def _cancel(self, base_url: str, job_id: str) -> None:
        try:
            async with asyncio.timeout(5):
                result = await self._request("POST", f"{base_url}/cancel/{job_id}")
                if result.get("id") != job_id or result.get("status") != "CANCELLED":
                    raise RunpodError("cancellation was not confirmed")
            logger.info("runpod_job_cancel_requested job_id=%s", job_id)
        except (httpx.HTTPError, TimeoutError):
            logger.warning("runpod_job_cancel_unconfirmed job_id=%s", job_id)

    async def run(self, base_url: str, job_input: dict[str, Any]) -> dict[str, Any]:
        base_url = normalize_api_url(base_url)
        if not is_runpod_url(base_url):
            raise ValueError("Runpod credentials are restricted to api.runpod.ai")
        try:
            body = json.dumps(
                {
                    "input": job_input,
                    "policy": {
                        "executionTimeout": 180_000,
                        "ttl": max(10_000, int((self._timeout + 30) * 1000)),
                    },
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise RunpodError("invalid Runpod request") from None
        if len(body) > MAX_REQUEST_BYTES:
            raise RunpodError("request is too large for Runpod; use a smaller image or context")

        job_id: str | None = None
        terminal = False
        started = time.monotonic()
        previous_status = None
        try:
            async with asyncio.timeout(self._timeout):
                # Do not retry submission: a lost acknowledgement can still mean
                # the paid job was accepted. Its TTL bounds this ambiguous case.
                state = await self._request("POST", f"{base_url}/run", content=body)
                candidate = state.get("id")
                if not isinstance(candidate, str) or not JOB_ID.fullmatch(candidate):
                    raise RunpodError("Runpod returned an invalid job ID")
                job_id = candidate
                failures = 0
                while True:
                    if state.get("id") != job_id:
                        raise RunpodError("Runpod returned a different job ID")
                    status = state.get("status")
                    if not isinstance(status, str) or status not in PENDING | TERMINAL:
                        raise RunpodError("Runpod returned an unknown job status")
                    if status != previous_status:
                        logger.info(
                            "runpod_job_status job_id=%s status=%s elapsed_ms=%s",
                            job_id, status, int((time.monotonic() - started) * 1000),
                        )
                        previous_status = status
                    terminal = status in TERMINAL
                    if status == "COMPLETED":
                        output = state.get("output")
                        if not isinstance(output, dict) or "error" in output:
                            raise RunpodError("Runpod worker returned an invalid result")
                        if job_input.get("operation") == "generate":
                            if not isinstance(output.get("response"), str) or not isinstance(output.get("usage"), dict):
                                raise RunpodError("Runpod worker returned an invalid generation")
                            output["usage"] = {
                                **output["usage"],
                                "runpod_job_id": job_id,
                                "runpod_delay_ms": state.get("delayTime"),
                                "runpod_execution_ms": state.get("executionTime"),
                            }
                            if job_input["request"].get("system_prompt") is None:
                                prompt = validate_prompt(output.get("default_prompt"))
                                if output["usage"].get("prompt_hash") != prompt["prompt_hash"]:
                                    raise RunpodError("Runpod generation prompt hash mismatch")
                                output["default_prompt"] = prompt
                        return output
                    if terminal:
                        # Ignore state["error"], which is untrusted free-form text.
                        raise RunpodError(f"Runpod job {status.lower()}")
                    await asyncio.sleep(self._poll_seconds)
                    try:
                        state = await self._request("GET", f"{base_url}/status/{job_id}")
                        failures = 0
                    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                        retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {
                            429, 500, 502, 503, 504,
                        }
                        failures += 1
                        if not retryable or failures > 3:
                            raise RunpodError("could not retrieve Runpod job status") from None
                        await asyncio.sleep(min(failures, 3))
        except TimeoutError:
            raise RunpodError("Runpod job timed out while waiting for GPU or generation") from None
        except httpx.HTTPStatusError as exc:
            raise RunpodError(f"Runpod HTTP {exc.response.status_code}") from None
        except httpx.TransportError:
            raise RunpodError("Runpod connection failed; request was not automatically resubmitted") from None
        finally:
            if job_id is not None and not terminal:
                await self._cancel(base_url, job_id)
