#!/usr/bin/env bash
set -euo pipefail

: "${BUSINESSGPT_API_KEY:?Set BUSINESSGPT_API_KEY before running}"

docker network inspect edge >/dev/null 2>&1 || docker network create edge

docker rm -f businessgpt-api >/dev/null 2>&1 || true

docker run -d --name businessgpt-api --restart unless-stopped \
  --network edge \
  -e BUSINESSGPT_MODEL_PATH="${BUSINESSGPT_MODEL_PATH:-/models/model-Q5_K_M.gguf}" \
  -e BUSINESSGPT_API_KEY="${BUSINESSGPT_API_KEY}" \
  -e BUSINESSGPT_N_CTX="${BUSINESSGPT_N_CTX:-2048}" \
  -e BUSINESSGPT_N_THREADS="${BUSINESSGPT_N_THREADS:-8}" \
  -e BUSINESSGPT_N_GPU_LAYERS="${BUSINESSGPT_N_GPU_LAYERS:-0}" \
  -v /opt/businessgpt/models:/models:ro \
  businessgpt-api:llama-server

