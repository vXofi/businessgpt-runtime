#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

archive="${BUSINESSGPT_API_ARCHIVE:-businessgpt-api.tar.gz}"
env_file="${BUSINESSGPT_API_ENV_FILE:-/opt/businessgpt/api.env}"
timeout_seconds="${BUSINESSGPT_UPDATE_TIMEOUT_SECONDS:-900}"
container="businessgpt-api"

[[ -f "$archive" ]] || { echo "Missing archive: $PWD/$archive" >&2; exit 1; }
[[ -f "$env_file" ]] || { echo "Missing API env file: $env_file" >&2; exit 1; }
docker container inspect "$container" >/dev/null

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

if ! BUSINESSGPT_API_ENV_FILE="$env_file" ./run_businessgpt_api_vm.sh >/dev/null; then
  rollback_update
  exit 1
fi

health=""
for ((elapsed = 0; elapsed < timeout_seconds; elapsed += 2)); do
  state="$(
    docker inspect "$container" \
      --format '{{.State.Status}} {{.RestartCount}}' 2>/dev/null || true
  )"
  read -r state_status restart_count <<<"$state"
  if [[ "$state_status" != "running" || "$restart_count" != "0" ]]; then
    break
  fi
  if health="$(docker exec "$container" python -c \
    'import json, os, urllib.request; r=urllib.request.Request("http://127.0.0.1:8000/health/ready",headers={"Authorization":"Bearer "+os.environ["BUSINESSGPT_API_KEY"]}); d=json.load(urllib.request.urlopen(r,timeout=3)); assert d.get("ok"); assert not os.environ.get("BUSINESSGPT_MMPROJ_PATH") or d.get("vision_enabled"); print(json.dumps(d,separators=(",",":")))' 2>/dev/null)"; then
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

mapfile -t old_containers < <(
  docker ps -a --format '{{.Names}}' | awk '/^businessgpt-api-old-/' | sort -r
)
for old in "${old_containers[@]:2}"; do
  docker rm -f "$old" >/dev/null
done

echo "API update complete"
echo "Rollback container retained: $rollback"
echo "$health"
