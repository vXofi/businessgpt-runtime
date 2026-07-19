#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
remote_dir="$PWD"

archive="${BUSINESSGPT_BOT_ARCHIVE:-businessgpt-bot.tar.gz}"
env_file="${BUSINESSGPT_BOT_ENV_FILE:-/opt/businessgpt-bot/bot.env}"
timeout_seconds="${BUSINESSGPT_UPDATE_TIMEOUT_SECONDS:-120}"
container="businessgpt-bot"

[[ -f "$archive" ]] || { echo "Missing archive: $PWD/$archive" >&2; exit 1; }
[[ -f "$env_file" ]] || { echo "Missing bot env file: $env_file" >&2; exit 1; }
docker container inspect "$container" >/dev/null

gunzip -c "$archive" | docker load

backup_name="bot-before-update-$(date -u +%Y%m%d-%H%M%S).sqlite3"
docker exec -e BACKUP_NAME="$backup_name" "$container" python -c \
  'import os, pathlib, sqlite3; source=pathlib.Path(os.environ.get("BUSINESSGPT_BOT_DB_PATH","/data/bot.sqlite3")); assert source.is_absolute() and source.is_relative_to("/data") and source.name; target=pathlib.Path("/data/backups")/os.environ["BACKUP_NAME"]; target.parent.mkdir(parents=True,exist_ok=True); s=sqlite3.connect(source); d=sqlite3.connect(target); s.backup(d); d.close(); s.close()'

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

if ! BUSINESSGPT_BOT_ENV_FILE="$env_file" ./run_businessgpt_bot_vm.sh >/dev/null; then
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

mapfile -t old_containers < <(
  docker ps -a --format '{{.Names}}' | awk '/^businessgpt-bot-old-/' | sort -r
)
for old in "${old_containers[@]:2}"; do
  docker rm -f "$old" >/dev/null
done
docker exec "$container" python -c \
  'import pathlib; backups=sorted(pathlib.Path("/data/backups").glob("bot-before-update-*.sqlite3"),key=lambda path:path.name,reverse=True); [path.unlink() for path in backups[7:]]'

echo "Bot update complete"
echo "SQLite backup: $remote_dir/data/backups/$backup_name"
echo "Rollback container retained: $rollback"
docker logs --tail=10 "$container"
