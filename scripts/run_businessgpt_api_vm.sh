#!/usr/bin/env bash
set -euo pipefail

: "${BUSINESSGPT_API_KEY:?Set BUSINESSGPT_API_KEY before running}"

model_path="${BUSINESSGPT_MODEL_PATH:-/models/model-Q5_K_M-no-mtp.gguf}"
mmproj_path="${BUSINESSGPT_MMPROJ_PATH:-}"
host_model="/opt/businessgpt/models/${model_path##*/}"
[[ -f "$host_model" ]] || { echo "Missing model: $host_model" >&2; exit 1; }
if [[ -n "$mmproj_path" ]]; then
  host_mmproj="/opt/businessgpt/models/${mmproj_path##*/}"
  [[ -f "$host_mmproj" ]] || { echo "Missing projector: $host_mmproj" >&2; exit 1; }
fi
docker image inspect businessgpt-api:llama-server >/dev/null

docker network inspect edge >/dev/null 2>&1 || docker network create edge

docker rm -f businessgpt-api >/dev/null 2>&1 || true

docker_args=(run -d --name businessgpt-api --restart unless-stopped \
  --network edge \
  -e BUSINESSGPT_MODEL_PATH="$model_path" \
  -e BUSINESSGPT_MMPROJ_PATH="$mmproj_path" \
  -e BUSINESSGPT_API_KEY="${BUSINESSGPT_API_KEY}" \
  -e BUSINESSGPT_N_CTX="${BUSINESSGPT_N_CTX:-4096}" \
  -e BUSINESSGPT_CONTEXT_SAFETY_TOKENS="${BUSINESSGPT_CONTEXT_SAFETY_TOKENS:-16}" \
  -e BUSINESSGPT_N_THREADS="${BUSINESSGPT_N_THREADS:-8}" \
  -e BUSINESSGPT_N_GPU_LAYERS="${BUSINESSGPT_N_GPU_LAYERS:-0}" \
  -e BUSINESSGPT_N_PARALLEL="${BUSINESSGPT_N_PARALLEL:-1}" \
  -e BUSINESSGPT_IMAGE_MAX_TOKENS="${BUSINESSGPT_IMAGE_MAX_TOKENS:-1024}" \
  -e BUSINESSGPT_STARTUP_TIMEOUT_SECONDS="${BUSINESSGPT_STARTUP_TIMEOUT_SECONDS:-900}" \
  -v /opt/businessgpt/models:/models:ro)

if [[ -n "${BUSINESSGPT_SYSTEM_PROMPT+x}" ]]; then
  docker_args+=(-e BUSINESSGPT_SYSTEM_PROMPT="$BUSINESSGPT_SYSTEM_PROMPT")
fi

docker_args+=(businessgpt-api:llama-server)
docker "${docker_args[@]}"
