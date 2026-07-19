#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

archive="${BUSINESSGPT_API_ARCHIVE:-businessgpt-api.tar.gz}"
timeout_seconds="${BUSINESSGPT_UPDATE_TIMEOUT_SECONDS:-900}"
container="businessgpt-api"

[[ -f "$archive" ]] || { echo "Missing archive: $PWD/$archive" >&2; exit 1; }
docker container inspect "$container" >/dev/null

current_env_json="$(docker inspect "$container" --format '{{json .Config.Env}}')"
container_env() {
  CURRENT_ENV_JSON="$current_env_json" python3 -c \
    'import json, os, sys; p=sys.argv[1] + "="; print(next((v[len(p):] for v in json.loads(os.environ["CURRENT_ENV_JSON"]) if v.startswith(p)), ""), end="")' \
    "$1"
}
container_has_env() {
  CURRENT_ENV_JSON="$current_env_json" python3 -c \
    'import json, os, sys; p=sys.argv[1] + "="; raise SystemExit(0 if any(v.startswith(p) for v in json.loads(os.environ["CURRENT_ENV_JSON"])) else 1)' \
    "$1"
}
preserve_env() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" ]]; then
    value="$(container_env "$name")"
    printf -v "$name" '%s' "$value"
    export "$name"
  fi
}

for name in \
  BUSINESSGPT_MODEL_PATH \
  BUSINESSGPT_MMPROJ_PATH \
  BUSINESSGPT_API_KEY \
  BUSINESSGPT_N_CTX \
  BUSINESSGPT_CONTEXT_SAFETY_TOKENS \
  BUSINESSGPT_N_THREADS \
  BUSINESSGPT_N_GPU_LAYERS \
  BUSINESSGPT_N_PARALLEL \
  BUSINESSGPT_IMAGE_MAX_TOKENS \
  BUSINESSGPT_STARTUP_TIMEOUT_SECONDS
do
  preserve_env "$name"
done

if container_has_env BUSINESSGPT_SYSTEM_PROMPT; then
  preserve_env BUSINESSGPT_SYSTEM_PROMPT
fi

: "${BUSINESSGPT_API_KEY:?Could not recover API key}"

gunzip -c "$archive" | docker load

rollback="${container}-old-$(date -u +%Y%m%d-%H%M%S)"
docker stop "$container" >/dev/null
docker rename "$container" "$rollback"

rollback_update() {
  rollback_armed=0
  echo "API update failed; restoring $rollback" >&2
  docker logs --tail=100 "$container" >&2 2>/dev/null || true
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker rename "$rollback" "$container"
  docker start "$container" >/dev/null
}

rollback_armed=1
trap 'if [[ "$rollback_armed" == 1 ]]; then rollback_update; fi' EXIT
trap 'exit 130' INT TERM

if ! ./run_businessgpt_api_vm.sh >/dev/null; then
  rollback_update
  exit 1
fi

health=""
for ((elapsed = 0; elapsed < timeout_seconds; elapsed += 2)); do
  if [[ "$(docker inspect "$container" --format '{{.State.Running}}' 2>/dev/null || true)" != "true" ]]; then
    break
  fi
  if health="$(docker exec "$container" python -c \
    'import json, os, urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)); assert d.get("ok"); assert not os.environ.get("BUSINESSGPT_MMPROJ_PATH") or d.get("vision_enabled"); print(json.dumps(d, separators=(",", ":")))' 2>/dev/null)"; then
    break
  fi
  health=""
  sleep 2
done

if [[ -z "$health" ]]; then
  rollback_update
  exit 1
fi

rollback_armed=0
trap - EXIT INT TERM
echo "API update complete"
echo "Rollback container retained: $rollback"
echo "$health"
