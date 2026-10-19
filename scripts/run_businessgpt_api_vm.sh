#!/usr/bin/env bash
set -euo pipefail

env_file="${BUSINESSGPT_API_ENV_FILE:-/opt/businessgpt/api.env}"
network="${BUSINESSGPT_BACKEND_NETWORK:-businessgpt-backend}"

[[ -f "$env_file" ]] || { echo "Missing API env file: $env_file" >&2; exit 1; }
[[ "$(stat -c '%a' "$env_file")" =~ ^(400|600)$ ]] || {
  echo "API env file must have mode 0600 or 0400: $env_file" >&2
  exit 1
}
grep -Eq '^BUSINESSGPT_API_KEY=.+$' "$env_file" || {
  echo "BUSINESSGPT_API_KEY is missing from $env_file" >&2
  exit 1
}
grep -Eq '^BUSINESSGPT_SYSTEM_PROMPT=.+$' "$env_file" || {
  echo "BUSINESSGPT_SYSTEM_PROMPT is missing from $env_file" >&2
  exit 1
}

env_value() {
  local name="$1"
  local fallback="$2"
  local value
  value="$(awk -v key="$name" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' "$env_file")"
  printf '%s' "${value:-$fallback}"
}

model_path="$(env_value BUSINESSGPT_MODEL_PATH /models/model-Q5_K_M-no-mtp.gguf)"
mmproj_path="$(env_value BUSINESSGPT_MMPROJ_PATH '')"
cache_ram_mib="$(env_value BUSINESSGPT_CACHE_RAM_MIB 0)"
[[ "$cache_ram_mib" =~ ^[0-9]+$ ]] || {
  echo "BUSINESSGPT_CACHE_RAM_MIB must be a non-negative integer" >&2
  exit 1
}
ctx_checkpoints="$(env_value BUSINESSGPT_CTX_CHECKPOINTS 1)"
[[ "$ctx_checkpoints" =~ ^[0-9]+$ ]] || {
  echo "BUSINESSGPT_CTX_CHECKPOINTS must be a non-negative integer" >&2
  exit 1
}
host_model="/opt/businessgpt/models/${model_path##*/}"
[[ -f "$host_model" ]] || { echo "Missing model: $host_model" >&2; exit 1; }
if [[ -n "$mmproj_path" ]]; then
  host_mmproj="/opt/businessgpt/models/${mmproj_path##*/}"
  [[ -f "$host_mmproj" ]] || { echo "Missing projector: $host_mmproj" >&2; exit 1; }
fi
docker image inspect businessgpt-api:llama-server >/dev/null

docker network inspect "$network" >/dev/null 2>&1 \
  || docker network create --internal "$network" >/dev/null
docker rm -f businessgpt-api >/dev/null 2>&1 || true
health_cmd='python -c '\''import json,urllib.request; assert json.load(urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=2))["ok"]'\'''

docker create --name businessgpt-api --restart unless-stopped \
  --network "$network" \
  --env-file "$env_file" \
  --env "LLAMA_ARG_CACHE_RAM=$cache_ram_mib" \
  --env "LLAMA_ARG_CTX_CHECKPOINTS=$ctx_checkpoints" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit "${BUSINESSGPT_API_PIDS_LIMIT:-256}" \
  --memory "${BUSINESSGPT_API_MEMORY_LIMIT:-24g}" \
  --memory-swap "${BUSINESSGPT_API_MEMORY_LIMIT:-24g}" \
  --cpus "${BUSINESSGPT_API_CPU_LIMIT:-8}" \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  --health-cmd "$health_cmd" \
  --health-interval 30s \
  --health-timeout 3s \
  --health-retries 3 \
  -v /opt/businessgpt/models:/models:ro \
  businessgpt-api:llama-server >/dev/null

docker start businessgpt-api >/dev/null
docker inspect businessgpt-api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
