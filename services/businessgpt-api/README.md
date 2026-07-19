# BusinessGPT API Service

FastAPI wrapper around `llama-server`.

## Endpoints

```text
GET  /health
GET  /prompt
POST /generate
```

`/generate` and `/prompt` require:

```http
Authorization: Bearer <BUSINESSGPT_API_KEY>
```

unless `BUSINESSGPT_API_KEY` is unset.

## Request Formats

Plain prompt:

```json
{
  "prompt": "string",
  "max_tokens": 256,
  "temperature": 0.7
}
```

Structured messages:

```json
{
  "messages": [
    {"role": "user", "name": "example-user", "content": "text"}
  ],
  "system_prompt": "Ты BusinessGPT...",
  "max_tokens": 128,
  "temperature": 0.9,
  "repetition_penalty": 1.2
}
```

Vision uses OpenAI-style content parts and accepts one JPEG, PNG, or WebP data
URL per request:

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "Что на фото?"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]
  }]
}
```

Images are decoded defensively, EXIF-oriented, converted to RGB JPEG, and
resized to a maximum 1280 px edge. The source limit is 10 MB and one image.

Allowed roles are `user`, `assistant`, and `system`. Caller-provided `system`
messages are ignored. Authenticated callers can override the default system
prompt for one request with the top-level `system_prompt` field.

Response usage includes `prompt_hash`, a SHA-256 hash of the effective system
prompt. The prompt text itself is not returned.

Authenticated `GET /prompt` returns the exact live API-default prompt and its
hash for trusted administration clients such as the Telegram prompt preview.
Generation responses continue to expose only `prompt_hash`.

```json
{
  "response": "model answer",
  "model": "model-Q5_K_M-no-mtp.gguf",
  "elapsed_ms": 1234,
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 40,
    "prompt_hash": "a91f...",
    "prepared_prompt_tokens": 100,
    "context_limit_tokens": 4096,
    "prompt_budget_tokens": 1904,
    "input_truncated": false,
    "dropped_messages": 0,
    "truncated_characters": 0
  }
}
```

## Environment

```text
BUSINESSGPT_MODEL_PATH=/models/model-Q5_K_M-no-mtp.gguf
BUSINESSGPT_MMPROJ_PATH=/models/mmproj-businessgpt-v16-f16.gguf
BUSINESSGPT_API_KEY=
BUSINESSGPT_N_CTX=4096
BUSINESSGPT_CONTEXT_SAFETY_TOKENS=16
BUSINESSGPT_IMAGE_MAX_TOKENS=1024
BUSINESSGPT_N_THREADS=8
BUSINESSGPT_N_GPU_LAYERS=0
BUSINESSGPT_N_PARALLEL=1
BUSINESSGPT_STARTUP_TIMEOUT_SECONDS=900
BUSINESSGPT_SYSTEM_PROMPT=<optional override>
```

Default behavior notes:

- The API does not silently clamp caller-provided `max_tokens` beyond schema
  validation.
- Before generation, the API uses llama-server's tokenizer to enforce
  `input_tokens + max_tokens + safety_tokens <= BUSINESSGPT_N_CTX`.
- If input is too large, the API discards oldest messages first. If the newest
  message still does not fit, it retains its beginning and end and inserts a
  truncation marker. The system prompt is never silently truncated.
- If the system prompt and minimum message envelope cannot fit, the API returns
  a controlled `422` response instead of sending an oversized request to the
  model backend.
- Individual prompts/messages are limited to 65,536 characters and structured
  requests to 64 messages, preventing oversized authenticated requests from
  consuming unbounded memory before token-aware truncation.
- The default system prompt tells the model to answer one current thread, not
  every message in the context.
- Prompt presets and per-chat active prompt state should live in the Telegram
  bot or another caller, not in this API container. The API only accepts the
  trusted per-request prompt override and runs generation.
- Long pasted messages are normal chat context; the model should react to them,
  not summarize them unless asked.
- Image token capacity is reserved before text fitting, so an oversized mixed
  request becomes a controlled `422` or loses old context instead of exceeding
  llama.cpp's context window.

## Runtime And Model Pairing

This service builds and runs native llama.cpp `llama-server`. It does not use
`llama-cpp-python`. The Docker build pins llama.cpp commit
`a4107133a634250c8c9d888bc0bc8520dcfd6105`; model provenance and hashes live in
`model-manifest.json`.

`llama-cpp-python` does provide multimodal bindings, so this is a runtime choice,
not an availability claim. Its releases vendor particular llama.cpp revisions
and add Python-side chat handlers. This service instead calls the native
OpenAI-compatible multimodal endpoint documented by llama.cpp, letting the
container pin and verify the exact backend commit used with Qwen3.5:

- [llama-cpp-python changelog](https://github.com/abetlen/llama-cpp-python/blob/main/CHANGELOG.md)
- [llama.cpp multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)

The v16 Hugging Face repo is a language LoRA adapter with no visual tensors.
Use its merged Q5 GGUF for language and the projector from the exact base model
revision in the manifest. Prepare that projector locally:

```bash
scripts/prepare_businessgpt_language_gguf.sh
scripts/prepare_businessgpt_mmproj.sh
```

The original Q5 GGUF has 32 language-layer tensors but stale MTP metadata that
declares a nonexistent 33rd block. The language helper verifies its source
hash and uses llama.cpp `COPY` mode to correct only `block_count=32` and
`nextn_predict_layers=0`; it does not re-quantize weights.

The script verifies both the llama.cpp commit and current Hugging Face base
revision before remote range conversion. It writes the untracked artifact to
`.artifacts/models/mmproj-businessgpt-v16-f16.gguf` and prints its SHA-256.

Copy it before restarting the API:

```bash
scp -i ~/.ssh/businessgpt_ed25519 \
  .artifacts/models/model-Q5_K_M-no-mtp.gguf \
  .artifacts/models/mmproj-businessgpt-v16-f16.gguf \
  deploy@192.0.2.10:/opt/businessgpt/models/

# On the VM:
export BUSINESSGPT_MMPROJ_PATH=/models/mmproj-businessgpt-v16-f16.gguf
scripts/run_businessgpt_api_vm.sh
docker run --rm --network edge curlimages/curl:8.15.0 \
  -fsS http://businessgpt-api:8000/health
```

Do not set `BUSINESSGPT_MMPROJ_PATH` until the file exists. With it unset, the
same image remains text-only and rejects image requests with `422`.

## Build

For routine API-only code updates after the first deployment:

```bash
cd /Users/developer/Documents/businessgpt-server
scripts/deploy_businessgpt_api.sh
```

This builds and uploads the amd64 image, recovers the current runtime settings,
starts the replacement, and waits until `/health` succeeds. When a projector is
configured, readiness also requires `vision_enabled=true`. Failure restores the
previous container automatically.

The deploy helper deliberately does not upload GGUF or projector files. Model
changes remain explicit; prepare and transfer those artifacts before running
the API deploy command. Override connection defaults with
`BUSINESSGPT_DEPLOY_TARGET` and `BUSINESSGPT_SSH_KEY`.

From repo root:

```bash
docker buildx build --platform linux/amd64 --load \
  -f services/businessgpt-api/Dockerfile \
  -t businessgpt-api:llama-server .
```

## Run On VM

```bash
docker rm -f businessgpt-api
docker run -d --name businessgpt-api --restart unless-stopped \
  --network edge \
  -e BUSINESSGPT_MODEL_PATH=/models/model-Q5_K_M-no-mtp.gguf \
  -e BUSINESSGPT_MMPROJ_PATH=/models/mmproj-businessgpt-v16-f16.gguf \
  -e BUSINESSGPT_API_KEY='<token>' \
  -e BUSINESSGPT_N_CTX=4096 \
  -e BUSINESSGPT_CONTEXT_SAFETY_TOKENS=16 \
  -e BUSINESSGPT_N_THREADS=8 \
  -v /opt/businessgpt/models:/models:ro \
  businessgpt-api:llama-server
```
