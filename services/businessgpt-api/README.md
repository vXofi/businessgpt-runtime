# BusinessGPT API Service

FastAPI wrapper around `llama-server`.

## Endpoints

```text
GET  /health
POST /generate
```

`/generate` requires:

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
  "max_tokens": 128,
  "temperature": 0.9,
  "repetition_penalty": 1.2
}
```

Allowed roles are `user`, `assistant`, and `system`. Caller-provided `system`
messages are ignored; the deployment owns the system prompt.

## Environment

```text
BUSINESSGPT_MODEL_PATH=/models/model-Q5_K_M.gguf
BUSINESSGPT_API_KEY=
BUSINESSGPT_N_CTX=2048
BUSINESSGPT_N_THREADS=8
BUSINESSGPT_N_GPU_LAYERS=0
BUSINESSGPT_SYSTEM_PROMPT=<optional override>
```

Default behavior notes:

- The API does not silently clamp caller-provided `max_tokens` beyond schema
  validation.
- The default system prompt tells the model to answer one current thread, not
  every message in the context.
- Long pasted messages are normal chat context; the model should react to them,
  not summarize them unless asked.

## Build

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
  -e BUSINESSGPT_MODEL_PATH=/models/model-Q5_K_M.gguf \
  -e BUSINESSGPT_API_KEY='<token>' \
  -e BUSINESSGPT_N_CTX=2048 \
  -e BUSINESSGPT_N_THREADS=8 \
  -v /opt/businessgpt/models:/models:ro \
  businessgpt-api:llama-server
```
