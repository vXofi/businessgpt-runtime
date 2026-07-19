#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
remote_dir="$PWD"

archive="${BUSINESSGPT_BOT_ARCHIVE:-businessgpt-bot.tar.gz}"
timeout_seconds="${BUSINESSGPT_UPDATE_TIMEOUT_SECONDS:-120}"
container="businessgpt-bot"

[[ -f "$archive" ]] || { echo "Missing archive: $PWD/$archive" >&2; exit 1; }
docker container inspect "$container" >/dev/null

current_env_json="$(docker inspect "$container" --format '{{json .Config.Env}}')"
container_env() {
  CURRENT_ENV_JSON="$current_env_json" python3 -c \
    'import json, os, sys; p=sys.argv[1] + "="; print(next((v[len(p):] for v in json.loads(os.environ["CURRENT_ENV_JSON"]) if v.startswith(p)), ""), end="")' \
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
  BUSINESSGPT_BOT_TOKEN \
  BUSINESSGPT_BOT_ADMIN_USER_IDS \
  BUSINESSGPT_BOT_TELEGRAM_PROXY_URL \
  BUSINESSGPT_API_URL \
  BUSINESSGPT_API_KEY \
  BUSINESSGPT_BOT_DB_PATH \
  BUSINESSGPT_BOT_MEDIA_CACHE_DIR \
  BUSINESSGPT_BOT_MEDIA_CACHE_TTL_SECONDS \
  BUSINESSGPT_BOT_MEDIA_CACHE_MAX_BYTES \
  BUSINESSGPT_BOT_IMAGE_MAX_BYTES \
  BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64 \
  BUSINESSGPT_BOT_MAX_TOKENS \
  BUSINESSGPT_BOT_TEMPERATURE \
  BUSINESSGPT_BOT_TOP_P \
  BUSINESSGPT_BOT_TOP_K \
  BUSINESSGPT_BOT_REPETITION_PENALTY
do
  preserve_env "$name"
done

: "${BUSINESSGPT_BOT_TOKEN:?Could not recover bot token}"
: "${BUSINESSGPT_BOT_ADMIN_USER_IDS:?Could not recover global admin IDs}"
: "${BUSINESSGPT_API_KEY:?Could not recover API key}"
: "${BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64:?Could not recover feedback public key}"

gunzip -c "$archive" | docker load

backup_name="bot-before-update-$(date -u +%Y%m%d-%H%M%S).sqlite3"
docker exec -e BACKUP_NAME="$backup_name" "$container" python -c \
  'import os, sqlite3; s=sqlite3.connect("/data/bot.sqlite3"); d=sqlite3.connect("/data/" + os.environ["BACKUP_NAME"]); s.backup(d); d.close(); s.close()'

rollback="${container}-old-$(date -u +%Y%m%d-%H%M%S)"
docker stop "$container" >/dev/null
docker rename "$container" "$rollback"

rollback_update() {
  rollback_armed=0
  echo "Bot update failed; restoring $rollback" >&2
  docker logs --tail=100 "$container" >&2 2>/dev/null || true
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker rename "$rollback" "$container"
  docker start "$container" >/dev/null
}

rollback_armed=1
trap 'if [[ "$rollback_armed" == 1 ]]; then rollback_update; fi' EXIT
trap 'exit 130' INT TERM

if ! ./run_businessgpt_bot_vm.sh >/dev/null; then
  rollback_update
  exit 1
fi

ready=0
for ((elapsed = 0; elapsed < timeout_seconds; elapsed += 2)); do
  if [[ "$(docker inspect "$container" --format '{{.State.Running}}' 2>/dev/null || true)" != "true" ]]; then
    break
  fi
  logs="$(docker logs "$container" 2>&1 || true)"
  if [[ "$logs" == *"Application started"* ]]; then
    ready=1
    break
  fi
  sleep 2
done

if [[ "$ready" != 1 ]]; then
  rollback_update
  exit 1
fi

rollback_armed=0
trap - EXIT INT TERM
echo "Bot update complete"
echo "SQLite backup: $remote_dir/data/$backup_name"
echo "Rollback container retained: $rollback"
docker logs --tail=10 "$container"
