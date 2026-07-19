from __future__ import annotations

import atexit
import hashlib
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException

from api_security import SecurityLimitsMiddleware
from context_window import ContextBudgetError, PromptMessage, fit_messages_to_context
from media import ImageValidationError, NormalizedImage, normalize_image_data_url
from model_verification import verify_model_files
from request_models import (
    ChatMessage,
    GenerateRequest,
    GenerateResponse,
    TextContentPart,
)


MODEL_PATH = os.environ.get("BUSINESSGPT_MODEL_PATH", "/models/businessgpt.gguf")
MMPROJ_PATH = os.environ.get("BUSINESSGPT_MMPROJ_PATH", "").strip() or None
API_KEY = os.environ.get("BUSINESSGPT_API_KEY", "").strip()
N_CTX = int(os.environ.get("BUSINESSGPT_N_CTX", "4096"))
CONTEXT_SAFETY_TOKENS = int(os.environ.get("BUSINESSGPT_CONTEXT_SAFETY_TOKENS", "16"))
IMAGE_MAX_TOKENS = int(os.environ.get("BUSINESSGPT_IMAGE_MAX_TOKENS", "1024"))
N_THREADS = int(os.environ.get("BUSINESSGPT_N_THREADS", str(os.cpu_count() or 4)))
N_GPU_LAYERS = int(os.environ.get("BUSINESSGPT_N_GPU_LAYERS", "0"))
N_PARALLEL = int(os.environ.get("BUSINESSGPT_N_PARALLEL", "1"))
STARTUP_TIMEOUT_SECONDS = int(os.environ.get("BUSINESSGPT_STARTUP_TIMEOUT_SECONDS", "900"))
TOKENIZE_TIMEOUT_SECONDS = float(
    os.environ.get("BUSINESSGPT_TOKENIZE_TIMEOUT_SECONDS", "30")
)
GENERATION_TIMEOUT_SECONDS = float(
    os.environ.get("BUSINESSGPT_GENERATION_TIMEOUT_SECONDS", "300")
)
HEALTH_TIMEOUT_SECONDS = float(os.environ.get("BUSINESSGPT_HEALTH_TIMEOUT_SECONDS", "2"))
MAX_REQUEST_BODY_BYTES = int(
    os.environ.get("BUSINESSGPT_MAX_REQUEST_BODY_BYTES", str(16 * 1024 * 1024))
)
IMAGE_DECODE_CONCURRENCY = int(
    os.environ.get("BUSINESSGPT_IMAGE_DECODE_CONCURRENCY", "1")
)
LLAMA_SERVER_BIN = os.environ.get("LLAMA_SERVER_BIN", "/opt/llama.cpp/build/bin/llama-server")
LLAMA_HOST = os.environ.get("LLAMA_HOST", "127.0.0.1")
LLAMA_PORT = int(os.environ.get("LLAMA_PORT", "8081"))
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}"
LLAMA_CPP_REF = os.environ.get(
    "LLAMA_CPP_REF", "a4107133a634250c8c9d888bc0bc8520dcfd6105"
)
MODEL_MANIFEST_PATH = os.environ.get(
    "BUSINESSGPT_MODEL_MANIFEST_PATH", "/app/model-manifest.json"
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


ALLOW_INSECURE_NO_AUTH = _env_flag("BUSINESSGPT_ALLOW_INSECURE_NO_AUTH")
ALLOW_UNVERIFIED_MODEL = _env_flag("BUSINESSGPT_ALLOW_UNVERIFIED_MODEL")

if N_CTX <= 0:
    raise RuntimeError("BUSINESSGPT_N_CTX must be positive")
if CONTEXT_SAFETY_TOKENS < 0 or CONTEXT_SAFETY_TOKENS >= N_CTX:
    raise RuntimeError("BUSINESSGPT_CONTEXT_SAFETY_TOKENS must be between 0 and N_CTX")
if IMAGE_MAX_TOKENS <= 0 or IMAGE_MAX_TOKENS >= N_CTX:
    raise RuntimeError("BUSINESSGPT_IMAGE_MAX_TOKENS must be between 1 and N_CTX - 1")
if N_PARALLEL <= 0:
    raise RuntimeError("BUSINESSGPT_N_PARALLEL must be positive")
if STARTUP_TIMEOUT_SECONDS <= 0:
    raise RuntimeError("BUSINESSGPT_STARTUP_TIMEOUT_SECONDS must be positive")
if min(TOKENIZE_TIMEOUT_SECONDS, GENERATION_TIMEOUT_SECONDS, HEALTH_TIMEOUT_SECONDS) <= 0:
    raise RuntimeError("backend timeouts must be positive")
if MAX_REQUEST_BODY_BYTES <= 0:
    raise RuntimeError("BUSINESSGPT_MAX_REQUEST_BODY_BYTES must be positive")
if IMAGE_DECODE_CONCURRENCY <= 0:
    raise RuntimeError("BUSINESSGPT_IMAGE_DECODE_CONCURRENCY must be positive")
if not API_KEY:
    bind_host = os.environ.get("HOST", "0.0.0.0")
    if not ALLOW_INSECURE_NO_AUTH or bind_host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "BUSINESSGPT_API_KEY is required; insecure mode requires "
            "BUSINESSGPT_ALLOW_INSECURE_NO_AUTH=true and loopback HOST"
        )

DEFAULT_SYSTEM_PROMPT = "Configure the system prompt at deployment time."
SYSTEM_PROMPT = os.environ.get("BUSINESSGPT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)


@dataclass(frozen=True)
class PreparedMessage:
    source_index: int
    role: str
    text: str
    name: str | None
    image: NormalizedImage | None = None
    had_text: bool = True


def _require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    if not API_KEY and ALLOW_INSECURE_NO_AUTH:
        return
    expected = f"Bearer {API_KEY}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid api key")


def _effective_system_prompt(req: GenerateRequest) -> str:
    if req.system_prompt is not None:
        prompt = req.system_prompt.strip()
        if not prompt:
            raise HTTPException(status_code=422, detail="system_prompt must not be blank")
        return prompt
    return SYSTEM_PROMPT


def _prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def _prepare_request_messages(req: GenerateRequest) -> list[PreparedMessage]:
    if req.messages:
        request_messages = req.messages
    elif req.prompt:
        request_messages = [ChatMessage(role="user", content=req.prompt)]
    else:
        raise HTTPException(status_code=422, detail="either prompt or messages is required")

    prepared: list[PreparedMessage] = []
    image_count = 0
    for source_index, message in enumerate(request_messages):
        if message.role == "system":
            continue
        image: NormalizedImage | None = None
        if isinstance(message.content, str):
            text = message.content.strip()
            had_text = bool(text)
        else:
            text_parts: list[str] = []
            for part in message.content:
                if isinstance(part, TextContentPart):
                    text_parts.append(part.text.strip())
                    continue
                image_count += 1
                if image_count > 1:
                    raise HTTPException(status_code=422, detail="at most one image is supported")
                try:
                    if not image_decode_slots.acquire(blocking=False):
                        raise HTTPException(status_code=503, detail="image decoder is busy")
                    try:
                        image = normalize_image_data_url(part.image_url.url)
                    finally:
                        image_decode_slots.release()
                except ImageValidationError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            text = "\n".join(part for part in text_parts if part).strip()
            had_text = bool(text)

        if not text and image is None:
            raise HTTPException(status_code=422, detail="message content must not be blank")
        prepared.append(
            PreparedMessage(
                source_index=source_index,
                role=message.role,
                text=text or "[image]",
                name=message.name.strip() if message.name else None,
                image=image,
                had_text=had_text,
            )
        )

    if not prepared:
        raise HTTPException(status_code=422, detail="at least one non-system message is required")
    return prepared


def _count_tokens(text: str) -> int:
    response = client.post(
        "/tokenize",
        json={"content": text, "add_special": True, "parse_special": True},
        timeout=TOKENIZE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return len(response.json()["tokens"])


def _backend_messages(
    *,
    prepared: list[PreparedMessage],
    retained: tuple[PromptMessage, ...],
    system_prompt: str,
) -> list[dict]:
    by_index = {message.source_index: message for message in prepared}
    result: list[dict] = [{"role": "system", "content": system_prompt}]
    for fitted in retained:
        if fitted.source_index is None or fitted.source_index not in by_index:
            continue
        source = by_index[fitted.source_index]
        text = fitted.content.strip()
        if source.name and source.role == "user":
            text = f"{source.name}: {text}"
        if source.image is None:
            result.append({"role": source.role, "content": text})
            continue
        if not source.had_text and fitted.content.strip() == "[image]":
            text = "Отреагируй на изображение."
            if source.name and source.role == "user":
                text = f"{source.name}: {text}"
        result.append(
            {
                "role": source.role,
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": source.image.data_url},
                    },
                ],
            }
        )
    return result


def _clean_response(text: str) -> str:
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|im_start\|>.*", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|im_end\|>", "", text)
    text = re.sub(r"^\s*(?:<bot>|bot:|businessgpt:|name:)\s*", "", text, flags=re.I)
    return text.strip()


def _wait_for_port(host: str, port: int, timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            if llama_proc.poll() is not None:
                raise RuntimeError(f"llama-server exited early with code {llama_proc.returncode}")
            time.sleep(0.5)
    raise RuntimeError(f"llama-server did not open {host}:{port} within {timeout_s}s")


def _start_llama_server() -> subprocess.Popen:
    if not os.path.exists(MODEL_PATH):
        raise RuntimeError(
            f"BUSINESSGPT_MODEL_PATH does not exist: {MODEL_PATH}. "
            "Mount or download the GGUF file before starting the server."
        )
    if MMPROJ_PATH and not os.path.exists(MMPROJ_PATH):
        raise RuntimeError(f"BUSINESSGPT_MMPROJ_PATH does not exist: {MMPROJ_PATH}")
    if not os.path.exists(LLAMA_SERVER_BIN):
        raise RuntimeError(f"llama-server binary does not exist: {LLAMA_SERVER_BIN}")

    cmd = [
        LLAMA_SERVER_BIN,
        "-m",
        MODEL_PATH,
        "-c",
        str(N_CTX),
        "-t",
        str(N_THREADS),
        "-ngl",
        str(N_GPU_LAYERS),
        "--parallel",
        str(N_PARALLEL),
        "--host",
        LLAMA_HOST,
        "--port",
        str(LLAMA_PORT),
    ]
    if MMPROJ_PATH:
        cmd.extend(
            [
                "--mmproj",
                MMPROJ_PATH,
                "--image-max-tokens",
                str(IMAGE_MAX_TOKENS),
            ]
        )
    print("Starting llama-server:", " ".join(cmd), flush=True)
    return subprocess.Popen(cmd)


verified_model_sha256, verified_projector_sha256 = verify_model_files(
    manifest_path=MODEL_MANIFEST_PATH,
    model_path=MODEL_PATH,
    projector_path=MMPROJ_PATH,
    allow_unverified=ALLOW_UNVERIFIED_MODEL,
)
image_decode_slots = threading.BoundedSemaphore(IMAGE_DECODE_CONCURRENCY)

llama_proc = _start_llama_server()
atexit.register(lambda: llama_proc.terminate() if llama_proc.poll() is None else None)
_wait_for_port(LLAMA_HOST, LLAMA_PORT)

client = httpx.Client(base_url=LLAMA_URL, timeout=httpx.Timeout(GENERATION_TIMEOUT_SECONDS))


def _backend_props() -> dict:
    try:
        response = client.get("/props", timeout=HEALTH_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return {}


def _wait_for_backend_props() -> dict:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if llama_proc.poll() is not None:
            raise RuntimeError(f"llama-server exited with code {llama_proc.returncode}")
        props = _backend_props()
        if props:
            return props
        time.sleep(1)
    raise RuntimeError(
        f"llama-server did not become ready within {STARTUP_TIMEOUT_SECONDS} seconds"
    )


startup_props = _wait_for_backend_props()
if MMPROJ_PATH and not startup_props.get("modalities", {}).get("vision", False):
    llama_proc.terminate()
    raise RuntimeError("llama-server loaded the projector but did not report vision capability")

app = FastAPI(
    title="BusinessGPT API",
    version="0.6.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    SecurityLimitsMiddleware,
    api_key=API_KEY,
    max_body_bytes=MAX_REQUEST_BODY_BYTES,
    max_generations=N_PARALLEL,
)


@app.get("/health")
def health() -> dict:
    return {"ok": llama_proc.poll() is None}


@app.get("/health/ready", dependencies=[Depends(_require_auth)])
def readiness() -> dict:
    props = _backend_props()
    modalities = props.get("modalities", {})
    ready = llama_proc.poll() is None and bool(props)
    if not ready:
        raise HTTPException(status_code=503, detail="backend is not ready")
    return {
        "ok": True,
        "vision_enabled": bool(modalities.get("vision", False)),
        "model_sha256": verified_model_sha256,
        "projector_sha256": verified_projector_sha256,
    }


@app.get("/prompt", dependencies=[Depends(_require_auth)])
def default_prompt() -> dict:
    return {
        "name": "API default",
        "text": SYSTEM_PROMPT,
        "prompt_hash": _prompt_hash(SYSTEM_PROMPT),
    }


@app.post("/generate", response_model=GenerateResponse, dependencies=[Depends(_require_auth)])
def generate(req: GenerateRequest) -> GenerateResponse:
    if llama_proc.poll() is not None:
        raise HTTPException(status_code=503, detail=f"llama-server exited with code {llama_proc.returncode}")

    system_prompt = _effective_system_prompt(req)
    started = time.perf_counter()
    prepared = _prepare_request_messages(req)
    images = [message.image for message in prepared if message.image is not None]
    if images and not MMPROJ_PATH:
        raise HTTPException(status_code=422, detail="image input is not enabled on this server")

    image_reserve_tokens = IMAGE_MAX_TOKENS * len(images)
    prompt_budget_tokens = (
        N_CTX - req.max_tokens - CONTEXT_SAFETY_TOKENS - image_reserve_tokens
    )
    prompt_messages = [
        PromptMessage(
            role=message.role,
            content=message.text,
            name=message.name,
            source_index=message.source_index,
        )
        for message in prepared
    ]
    try:
        context_fit = fit_messages_to_context(
            messages=prompt_messages,
            system_prompt=system_prompt,
            prompt_budget_tokens=prompt_budget_tokens,
            count_tokens=_count_tokens,
        )
    except ContextBudgetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"llama-server tokenization failed: {exc}") from exc

    payload = {
        "messages": _backend_messages(
            prepared=prepared,
            retained=context_fit.messages,
            system_prompt=system_prompt,
        ),
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repeat_penalty": req.repetition_penalty,
        "stream": False,
        "reasoning_format": "none",
    }
    try:
        result = client.post(
            "/v1/chat/completions",
            json=payload,
            timeout=GENERATION_TIMEOUT_SECONDS,
        )
        result.raise_for_status()
        data = result.json()
        response_text = data["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"llama-server request failed: {exc}") from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    backend_usage = data.get("usage") or {}
    image = images[0] if images else None
    return GenerateResponse(
        response=_clean_response(str(response_text or "")),
        model=os.path.basename(MODEL_PATH),
        elapsed_ms=elapsed_ms,
        usage={
            "prompt_tokens": backend_usage.get("prompt_tokens"),
            "completion_tokens": backend_usage.get("completion_tokens"),
            "total_tokens": backend_usage.get("total_tokens"),
            "prompt_hash": _prompt_hash(system_prompt),
            "model_sha256": verified_model_sha256,
            "projector_sha256": verified_projector_sha256,
            "llama_cpp_revision": LLAMA_CPP_REF,
            "context_fit_version": 1,
            "retained_source_indices": [
                message.source_index for message in context_fit.messages
            ],
            "prepared_prompt_tokens": context_fit.prompt_tokens,
            "context_limit_tokens": N_CTX,
            "prompt_budget_tokens": prompt_budget_tokens,
            "image_count": len(images),
            "image_reserved_tokens": image_reserve_tokens,
            "image_source_sha256": image.source_sha256 if image else None,
            "image_normalized_sha256": image.normalized_sha256 if image else None,
            "input_truncated": context_fit.input_truncated,
            "dropped_messages": context_fit.dropped_messages,
            "truncated_characters": context_fit.truncated_characters,
        },
    )
