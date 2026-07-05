import atexit
import os
import re
import socket
import subprocess
import time
from typing import Literal
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


MODEL_PATH = os.environ.get("BUSINESSGPT_MODEL_PATH", "/models/businessgpt.gguf")
API_KEY = os.environ.get("BUSINESSGPT_API_KEY")
N_CTX = int(os.environ.get("BUSINESSGPT_N_CTX", "2048"))
N_THREADS = int(os.environ.get("BUSINESSGPT_N_THREADS", str(os.cpu_count() or 4)))
N_GPU_LAYERS = int(os.environ.get("BUSINESSGPT_N_GPU_LAYERS", "0"))
LLAMA_SERVER_BIN = os.environ.get("LLAMA_SERVER_BIN", "/opt/llama.cpp/build/bin/llama-server")
LLAMA_HOST = os.environ.get("LLAMA_HOST", "127.0.0.1")
LLAMA_PORT = int(os.environ.get("LLAMA_PORT", "8081"))
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}"

DEFAULT_SYSTEM_PROMPT = "Configure the system prompt at deployment time."
SYSTEM_PROMPT = os.environ.get("BUSINESSGPT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., min_length=1)
    name: Optional[str] = None


class GenerateRequest(BaseModel):
    prompt: Optional[str] = Field(None, min_length=1)
    messages: Optional[list[ChatMessage]] = None
    max_tokens: int = Field(256, ge=1, le=1024)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    top_k: int = Field(50, ge=0)
    repetition_penalty: float = Field(1.1, ge=0.0, le=3.0)


class GenerateResponse(BaseModel):
    response: str
    model: str
    elapsed_ms: int
    usage: dict


def _require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


def _format_prompt(user_prompt: str) -> str:
    return (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt.strip()}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def _message_content(message: ChatMessage) -> str:
    content = message.content.strip()
    if message.role == "user" and message.name:
        return f"{message.name.strip()}: {content}"
    return content


def _format_messages(messages: list[ChatMessage]) -> str:
    parts = [f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>"]
    for message in messages:
        # The deployment owns the system prompt; caller-provided system messages
        # are ignored to keep behavior stable across bot integrations.
        if message.role == "system":
            continue
        parts.append(f"<|im_start|>{message.role}\n{_message_content(message)}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def _format_request(req: GenerateRequest) -> str:
    if req.messages:
        return _format_messages(req.messages)
    if req.prompt:
        return _format_prompt(req.prompt)
    raise HTTPException(status_code=422, detail="either prompt or messages is required")


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
        "--host",
        LLAMA_HOST,
        "--port",
        str(LLAMA_PORT),
    ]
    print("Starting llama-server:", " ".join(cmd), flush=True)
    return subprocess.Popen(cmd)


llama_proc = _start_llama_server()
atexit.register(lambda: llama_proc.terminate() if llama_proc.poll() is None else None)
_wait_for_port(LLAMA_HOST, LLAMA_PORT)

client = httpx.Client(base_url=LLAMA_URL, timeout=httpx.Timeout(300.0))
app = FastAPI(title="BusinessGPT API", version="0.2.0")


@app.get("/health")
def health() -> dict:
    return {
        "ok": llama_proc.poll() is None,
        "model_path": MODEL_PATH,
        "backend": "llama-server",
        "backend_url": LLAMA_URL,
    }


@app.post("/generate", response_model=GenerateResponse, dependencies=[Depends(_require_auth)])
def generate(req: GenerateRequest) -> GenerateResponse:
    if llama_proc.poll() is not None:
        raise HTTPException(status_code=503, detail=f"llama-server exited with code {llama_proc.returncode}")

    started = time.perf_counter()
    payload = {
        "prompt": _format_request(req),
        "n_predict": req.max_tokens,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "top_k": req.top_k,
        "repeat_penalty": req.repetition_penalty,
        "stop": ["<|im_end|>", "<|im_start|>"],
        "stream": False,
    }
    try:
        result = client.post("/completion", json=payload)
        result.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"llama-server request failed: {e}") from e

    data = result.json()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return GenerateResponse(
        response=_clean_response(data.get("content", "")),
        model=os.path.basename(MODEL_PATH),
        elapsed_ms=elapsed_ms,
        usage={
            "prompt_tokens": data.get("tokens_evaluated"),
            "completion_tokens": data.get("tokens_predicted"),
        },
    )
