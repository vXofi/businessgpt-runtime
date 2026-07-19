from __future__ import annotations

import atexit
import hashlib
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Literal, Optional, Union

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from context_window import ContextBudgetError, PromptMessage, fit_messages_to_context
from media import ImageValidationError, NormalizedImage, normalize_image_data_url


MODEL_PATH = os.environ.get("BUSINESSGPT_MODEL_PATH", "/models/businessgpt.gguf")
MMPROJ_PATH = os.environ.get("BUSINESSGPT_MMPROJ_PATH", "").strip() or None
API_KEY = os.environ.get("BUSINESSGPT_API_KEY")
N_CTX = int(os.environ.get("BUSINESSGPT_N_CTX", "4096"))
CONTEXT_SAFETY_TOKENS = int(os.environ.get("BUSINESSGPT_CONTEXT_SAFETY_TOKENS", "16"))
IMAGE_MAX_TOKENS = int(os.environ.get("BUSINESSGPT_IMAGE_MAX_TOKENS", "1024"))
N_THREADS = int(os.environ.get("BUSINESSGPT_N_THREADS", str(os.cpu_count() or 4)))
N_GPU_LAYERS = int(os.environ.get("BUSINESSGPT_N_GPU_LAYERS", "0"))
N_PARALLEL = int(os.environ.get("BUSINESSGPT_N_PARALLEL", "1"))
STARTUP_TIMEOUT_SECONDS = int(os.environ.get("BUSINESSGPT_STARTUP_TIMEOUT_SECONDS", "900"))
LLAMA_SERVER_BIN = os.environ.get("LLAMA_SERVER_BIN", "/opt/llama.cpp/build/bin/llama-server")
LLAMA_HOST = os.environ.get("LLAMA_HOST", "127.0.0.1")
LLAMA_PORT = int(os.environ.get("LLAMA_PORT", "8081"))
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}"
MAX_MESSAGE_CHARACTERS = 65_536
MAX_IMAGE_DATA_URL_CHARACTERS = 14_500_000
MAX_MESSAGES = 64

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

DEFAULT_SYSTEM_PROMPT = "Configure the system prompt at deployment time."
SYSTEM_PROMPT = os.environ.get("BUSINESSGPT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)


class TextContentPart(BaseModel):
    type: Literal["text"]
    text: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARACTERS)


class ImageURLValue(BaseModel):
    url: str = Field(..., min_length=1, max_length=MAX_IMAGE_DATA_URL_CHARACTERS)


class ImageURLContentPart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageURLValue


ContentPart = Union[TextContentPart, ImageURLContentPart]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Union[
        str,
        list[ContentPart],
    ]
    name: Optional[str] = Field(None, max_length=128)


class GenerateRequest(BaseModel):
    prompt: Optional[str] = Field(None, min_length=1, max_length=MAX_MESSAGE_CHARACTERS)
    messages: Optional[list[ChatMessage]] = Field(None, max_length=MAX_MESSAGES)
    system_prompt: Optional[str] = Field(
        None,
        min_length=1,
        max_length=MAX_MESSAGE_CHARACTERS,
    )
    max_tokens: int = Field(256, ge=1, le=1024)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    top_k: int = Field(50, ge=0, le=200)
    repetition_penalty: float = Field(1.1, ge=0.0, le=3.0)


class GenerateResponse(BaseModel):
    response: str
    model: str
    elapsed_ms: int
    usage: dict


@dataclass(frozen=True)
class PreparedMessage:
    source_index: int
    role: str
    text: str
    name: str | None
    image: NormalizedImage | None = None
    had_text: bool = True


def _require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
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
                    image = normalize_image_data_url(part.image_url.url)
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


llama_proc = _start_llama_server()
atexit.register(lambda: llama_proc.terminate() if llama_proc.poll() is None else None)
_wait_for_port(LLAMA_HOST, LLAMA_PORT)

client = httpx.Client(base_url=LLAMA_URL, timeout=httpx.Timeout(360.0))


def _backend_props() -> dict:
    try:
        response = client.get("/props")
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

app = FastAPI(title="BusinessGPT API", version="0.5.0")


@app.get("/health")
def health() -> dict:
    props = _backend_props()
    modalities = props.get("modalities", {})
    return {
        "ok": llama_proc.poll() is None,
        "model": os.path.basename(MODEL_PATH),
        "projector": os.path.basename(MMPROJ_PATH) if MMPROJ_PATH else None,
        "backend": "native-llama-server",
        "backend_build": props.get("build_info"),
        "backend_url": LLAMA_URL,
        "modalities": modalities,
        "vision_enabled": bool(modalities.get("vision", False)),
        "context_limit_tokens": N_CTX,
        "context_safety_tokens": CONTEXT_SAFETY_TOKENS,
        "image_max_tokens": IMAGE_MAX_TOKENS,
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
        result = client.post("/v1/chat/completions", json=payload)
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
