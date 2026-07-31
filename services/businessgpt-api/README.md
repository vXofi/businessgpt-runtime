# BusinessGPT API

Private FastAPI wrapper around a pinned native llama.cpp `llama-server`.

The production API is reachable only from `businessgpt-bot` on the internal
`businessgpt-backend` Docker network. It has no published port and no Caddy
route.

## Endpoints

| Method | Path | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | none | Process liveness only |
| `GET` | `/health/ready` | bearer | Backend/model readiness and hashes |
| `GET` | `/prompt` | bearer | Exact configured default prompt |
| `POST` | `/generate` | bearer | Text or vision generation |

Protected routes require:

```http
Authorization: Bearer <BUSINESSGPT_API_KEY>
```

Production startup fails when the key is empty. A no-auth mode exists only for
explicit loopback development: set both `HOST=127.0.0.1` and
`BUSINESSGPT_ALLOW_INSECURE_NO_AUTH=true`. Do not publish that container.

## Request

```json
{
  "messages": [
    {"role": "user", "name": "example-user", "content": "text"}
  ],
  "system_prompt": "optional trusted override",
  "max_tokens": 128,
  "temperature": 0.9,
  "top_p": 0.9,
  "top_k": 50,
  "repetition_penalty": 1.2
}
```

The legacy top-level `prompt` string is also accepted. Caller-supplied
`role=system` messages count toward request limits but are discarded; only the
authenticated top-level `system_prompt` can override the default.

Vision uses one OpenAI-style JPEG, PNG, or WebP data URL:

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "What is in this image?"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]
  }]
}
```

Images are limited to 10 MB compressed and 40 million source pixels. The
declared MIME type must match the detected format. Decode concurrency is
bounded; valid images are EXIF-oriented, converted to RGB JPEG, and resized to
a 1280-pixel maximum edge before llama.cpp receives them.

## Response And Provenance

```json
{
  "response": "model answer",
  "model": "model-Q5_K_M-no-mtp.gguf",
  "elapsed_ms": 1234,
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 40,
    "prompt_hash": "sha256...",
    "model_sha256": "sha256...",
    "projector_sha256": "sha256...",
    "llama_cpp_revision": "a4107133...",
    "context_fit_version": 1,
    "retained_source_indices": [0, 1],
    "input_truncated": false,
    "dropped_messages": 0,
    "truncated_characters": 0
  }
}
```

The normal generation response never returns prompt text. The bot records the
prompt and verifies `prompt_hash` inside its encrypted training sample.

## Request And Admission Limits

Limits are enforced at several layers:

- bearer authentication occurs in ASGI middleware before the request body is
  read or JSON-decoded;
- raw request bodies default to 16 MiB, with both `Content-Length` and streamed
  byte enforcement;
- generation admission is capped at `BUSINESSGPT_N_PARALLEL`; excess work gets
  `429` and `Retry-After` before body parsing;
- request schema: 64 messages, 8 parts per message, 128 parts total;
- each text string: 65,536 characters; all request text: 262,144 characters;
- finite bounded sampling values and `max_tokens` from 1 through 1024;
- tokenizer, generation, readiness, and startup phases have separate timeouts.

Token fitting reserves output, safety, and image tokens inside
`BUSINESSGPT_N_CTX`. It drops oldest messages first and, when necessary, keeps
the beginning and end of the newest message with an explicit truncation marker.
The system prompt is never silently shortened. Impossible requests return
controlled `422` errors.

## Model Pairing

The image pins native llama.cpp commit:

```text
a4107133a634250c8c9d888bc0bc8520dcfd6105
```

It does not use `llama-cpp-python`. The production language model is the merged
v16 Qwen 3.5 Q5 GGUF. The v16 adapter did not modify visual tensors, so the
vision projector comes from the exact pinned base-model revision. Provenance,
filenames, sizes, and SHA-256 values are in `model-manifest.json`.

At startup the API hashes every configured model file and refuses to load an
unknown filename or mismatched hash. `BUSINESSGPT_ALLOW_UNVERIFIED_MODEL=true`
is a development escape hatch and must not be used on the VM.

Prepare local model artifacts when they change:

```bash
scripts/prepare_businessgpt_language_gguf.sh
scripts/prepare_businessgpt_mmproj.sh
```

Code deploys never upload model files automatically.

## Environment

Production file: `/opt/businessgpt/api.env`, mode `0600` or `0400`.

```text
BUSINESSGPT_API_KEY=
BUSINESSGPT_MODEL_PATH=/models/model-Q5_K_M-no-mtp.gguf
BUSINESSGPT_MMPROJ_PATH=/models/mmproj-businessgpt-v16-f16.gguf
BUSINESSGPT_MODEL_MANIFEST_PATH=/app/model-manifest.json
BUSINESSGPT_N_CTX=4096
BUSINESSGPT_CONTEXT_SAFETY_TOKENS=16
BUSINESSGPT_IMAGE_MAX_TOKENS=1024
BUSINESSGPT_N_THREADS=8
BUSINESSGPT_N_GPU_LAYERS=0
BUSINESSGPT_N_PARALLEL=1
BUSINESSGPT_STARTUP_TIMEOUT_SECONDS=900
BUSINESSGPT_TOKENIZE_TIMEOUT_SECONDS=30
BUSINESSGPT_GENERATION_TIMEOUT_SECONDS=300
BUSINESSGPT_HEALTH_TIMEOUT_SECONDS=2
BUSINESSGPT_MAX_REQUEST_BODY_BYTES=16777216
BUSINESSGPT_IMAGE_DECODE_CONCURRENCY=1
BUSINESSGPT_SYSTEM_PROMPT=<required private runtime prompt>
```

## Build And Deploy

From repository root on the Mac:

```bash
scripts/build_businessgpt_api_amd64.sh
```

This uses only `services/businessgpt-api` as the Docker context and writes the
untracked `businessgpt-api.tar.gz` archive.

Routine update:

```bash
scripts/deploy_businessgpt_api.sh
```

The VM updater loads the archive, preserves the previous container, starts the
replacement with the private env file, and waits for authenticated readiness.
It rolls back automatically on failure and retains only two old API containers.

## VM Verification

```bash
docker inspect businessgpt-api \
  --format 'user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} networks={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'

docker exec businessgpt-api python -c \
  'import json,os,urllib.request; r=urllib.request.Request("http://127.0.0.1:8000/health/ready",headers={"Authorization":"Bearer "+os.environ["BUSINESSGPT_API_KEY"]}); print(json.load(urllib.request.urlopen(r,timeout=5)))'
```

Expected: user `10001:10001`, read-only root filesystem, only
`businessgpt-backend`, and readiness with vision plus verified hashes.
