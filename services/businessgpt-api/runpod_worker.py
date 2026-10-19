"""Queue worker around the unchanged, authenticated loopback BusinessGPT API."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from request_models import GenerateRequest


logger = logging.getLogger(__name__)
API_DIR = Path(__file__).resolve().parent
MAX_INPUT_BYTES = 9_500_000
OFFLOAD = re.compile(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU")


def validate_input(job: Any) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(job, dict) or not isinstance(job.get("input"), dict):
        raise ValueError("job input must be an object")
    data = job["input"]
    try:
        size = len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("job input must be valid JSON") from None
    if size > MAX_INPUT_BYTES:
        raise ValueError("job input exceeds the size limit")
    operation = data.get("operation")
    if not isinstance(operation, str):
        raise ValueError("job operation must be a string")
    if operation in {"prompt", "health"} and set(data) == {"operation"}:
        return operation, None
    if operation != "generate" or set(data) != {"operation", "request"}:
        raise ValueError("unsupported job operation or fields")
    if not isinstance(data["request"], dict):
        raise ValueError("generation request must be an object")
    if set(data["request"]) - GenerateRequest.model_fields.keys():
        raise ValueError("unsupported generation request fields")
    try:
        request = GenerateRequest.model_validate(data["request"])
    except ValidationError:
        # Pydantic errors contain input values; do not expose them to SDK logs.
        raise ValueError("generation request failed validation") from None
    return operation, request.model_dump(exclude_none=True)


class APIRuntime:
    def __init__(self, *, startup_timeout: float = 180) -> None:
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen | None = None
        self.log_thread: threading.Thread | None = None
        self.key = secrets.token_urlsafe(32)
        self.base_url = "http://127.0.0.1:8000"
        self.ready: dict[str, Any] | None = None
        self.prompt: dict[str, str] | None = None
        self.offload: tuple[int, int] | None = None
        self.cuda_seen = False

    def environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "HOST": "127.0.0.1",
            "LLAMA_HOST": "127.0.0.1",
            "LLAMA_PORT": "8081",
            "BUSINESSGPT_API_KEY": self.key,
            "BUSINESSGPT_ALLOW_INSECURE_NO_AUTH": "false",
            "BUSINESSGPT_ALLOW_UNVERIFIED_MODEL": "false",
            "BUSINESSGPT_N_GPU_LAYERS": "99",
            "BUSINESSGPT_N_PARALLEL": "1",
            "BUSINESSGPT_GENERATION_TIMEOUT_SECONDS": "120",
            "BUSINESSGPT_STARTUP_TIMEOUT_SECONDS": str(int(self.startup_timeout)),
            "LLAMA_ARG_LOG_VERBOSITY": "4",
        }

    def _consume_logs(self, pipe) -> None:
        # Trace confirms offload but can include chat text later. Extract only
        # these flags; never forward, retain or write the raw backend log.
        while line := pipe.readline(8192):
            self.cuda_seen |= b"CUDA" in line
            match = OFFLOAD.search(line.decode("utf-8", errors="replace"))
            if match:
                self.offload = tuple(map(int, match.groups()))

    def start(self, cancelled: threading.Event | None = None) -> None:
        if self.process is not None and self.process.poll() is None and self.ready:
            return
        self.stop()
        self.offload = None
        self.cuda_seen = False
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError("local API startup cancelled")
        started = time.monotonic()
        self.process = subprocess.Popen(
            [
                os.environ.get("BUSINESSGPT_API_PYTHON", sys.executable),
                "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
                "--port", "8000", "--workers", "1", "--no-access-log",
                "--no-server-header",
            ],
            cwd=API_DIR,
            env=self.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.log_thread = threading.Thread(
            target=self._consume_logs, args=(self.process.stdout,), daemon=True,
        )
        self.log_thread.start()
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.key}"},
                timeout=2,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                while time.monotonic() - started < self.startup_timeout:
                    if cancelled is not None and cancelled.is_set():
                        raise RuntimeError("local API startup cancelled")
                    if self.process.poll() is not None:
                        raise RuntimeError("local API exited during startup")
                    try:
                        response = client.get("/health/ready")
                        response.raise_for_status()
                        ready = response.json()
                    except (httpx.TransportError, ValueError):
                        time.sleep(0.25)
                        continue
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code != 503:
                            raise RuntimeError("local API readiness rejected") from None
                        time.sleep(0.25)
                        continue
                    if not (
                        ready.get("ok") and ready.get("vision_enabled")
                        and ready.get("model_sha256") and ready.get("projector_sha256")
                    ):
                        raise RuntimeError("verified model and vision readiness are required")
                    if not self.cuda_seen or not self.offload or self.offload[0] != self.offload[1] or self.offload[1] <= 0:
                        # The bounded log reader can lag the readiness response.
                        time.sleep(0.25)
                        continue
                    response = client.get("/prompt")
                    response.raise_for_status()
                    prompt = response.json()
                    if hashlib.sha256(prompt["text"].encode("utf-8")).hexdigest() != prompt["prompt_hash"]:
                        raise RuntimeError("default prompt hash mismatch")
                    self.prompt = prompt
                    self.ready = ready
                    logger.info(
                        "worker_backend_ready startup_ms=%s offloaded_layers=%s",
                        int((time.monotonic() - started) * 1000), self.offload[0],
                    )
                    return
            raise RuntimeError("local API startup or full CUDA offload timed out")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        self.ready = None
        self.prompt = None
        process, self.process = self.process, None
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)
        if self.log_thread is not None:
            self.log_thread.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()

    async def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=httpx.Timeout(130, connect=2),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            async with asyncio.timeout(130):
                response = await client.post("/generate", json=payload)
                if response.is_error:
                    raise RuntimeError(f"local API rejected generation ({response.status_code})")
                result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("response"), str):
            raise RuntimeError("local API returned an invalid generation")
        return result

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=3, trust_env=False, follow_redirects=False,
        ) as client:
            response = await client.get("/health/ready")
            response.raise_for_status()
            ready = response.json()
        if not isinstance(ready, dict) or not ready.get("ok"):
            raise RuntimeError("local API is not ready")
        return ready


class Worker:
    def __init__(self, runtime: APIRuntime) -> None:
        self.runtime = runtime
        self.lock = asyncio.Lock()

    async def handle(self, job: dict[str, Any]) -> dict[str, Any]:
        operation, payload = validate_input(job)
        async with self.lock:
            try:
                if not self.runtime.ready:
                    # Only needed after a cancelled job killed the API process.
                    # Shield startup so cleanup cannot race a still-spawning thread.
                    cancelled = threading.Event()
                    startup = asyncio.create_task(asyncio.to_thread(self.runtime.start, cancelled))
                    try:
                        await asyncio.shield(startup)
                    except asyncio.CancelledError:
                        cancelled.set()
                        with suppress(Exception):
                            await startup
                        raise
                if operation == "prompt":
                    return dict(self.runtime.prompt)
                if operation == "health":
                    return await self.runtime.health()
                assert payload is not None
                result = await self.runtime.generate(payload)
                if payload.get("system_prompt") is None:
                    # Preserve the exact default for encrypted RLHF provenance
                    # without a separate prompt job waking the GPU.
                    result["default_prompt"] = dict(self.runtime.prompt)
                return result
            except asyncio.CancelledError:
                self.runtime.stop()
                raise
            except Exception:
                self.runtime.stop()
                raise RuntimeError("BusinessGPT worker generation failed") from None


def main() -> None:
    import runpod

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    runtime = APIRuntime()

    def shutdown(signum, frame) -> None:
        runtime.stop()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        runtime.start()
        worker = Worker(runtime)
        runpod.serverless.start({
            "handler": worker.handle,
            "concurrency_modifier": lambda current: 1,
        })
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
