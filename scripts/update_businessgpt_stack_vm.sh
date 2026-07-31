#!/usr/bin/env bash
set -euo pipefail

api_dir="/opt/businessgpt"
bot_dir="/opt/businessgpt-bot"
xray_dir="/opt/businessgpt-xray"
caddyfile="${BUSINESSGPT_CADDYFILE:?Set BUSINESSGPT_CADDYFILE}"
caddy_container="${BUSINESSGPT_CADDY_CONTAINER:-caddy}"
legacy_caddy_hostname="${BUSINESSGPT_LEGACY_CADDY_HOSTNAME:-}"
stamp="$(date -u +%Y%m%d-%H%M%S)"
state_root="$bot_dir/hardening-backups"
state_dir="$state_root/hardening-$stamp"
api_env="$api_dir/api.env"
bot_env="$bot_dir/bot.env"
bot_db="$bot_dir/data/bot.sqlite3"
api_archive="$api_dir/businessgpt-api.tar.gz"
bot_archive="$bot_dir/businessgpt-bot.tar.gz"
xray_archive="$xray_dir/businessgpt-xray.tar.gz"
api_timeout_seconds="${BUSINESSGPT_UPDATE_TIMEOUT_SECONDS:-900}"
bot_timeout_seconds="${BUSINESSGPT_BOT_UPDATE_TIMEOUT_SECONDS:-120}"

for container in businessgpt-api businessgpt-bot businessgpt-xray; do
  docker container inspect "$container" >/dev/null
done
[[ -f "$bot_db" ]] || { echo "Missing bot database: $bot_db" >&2; exit 1; }
[[ -f "$caddyfile" ]] || { echo "Missing Caddyfile: $caddyfile" >&2; exit 1; }
[[ -f "$api_archive" ]] || { echo "Missing API archive: $api_archive" >&2; exit 1; }
[[ -f "$bot_archive" ]] || { echo "Missing bot archive: $bot_archive" >&2; exit 1; }
[[ -f "$xray_archive" ]] || { echo "Missing Xray archive: $xray_archive" >&2; exit 1; }

mkdir -p "$state_root" "$state_dir"
chmod 700 "$state_root"
chmod 700 "$state_dir"
python3 -c \
  'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' \
  "$bot_db" "$state_dir/bot.sqlite3"
cp -p "$caddyfile" "$state_dir/Caddyfile"

api_env_existed=0
bot_env_existed=0
if [[ -f "$api_env" ]]; then
  cp -p "$api_env" "$state_dir/api.env"
  api_env_existed=1
fi
if [[ -f "$bot_env" ]]; then
  cp -p "$bot_env" "$state_dir/bot.env"
  bot_env_existed=1
fi

prehard_name() {
  printf '%s-prehardening-%s' "$1" "$stamp"
}

restore_file() {
  local existed="$1"
  local backup="$2"
  local target="$3"
  if [[ "$existed" == 1 ]]; then
    cp -p "$backup" "$target"
  else
    rm -f "$target"
  fi
}

rollback_stack() {
  rollback_armed=0
  trap - EXIT INT TERM
  set +e
  echo "Hardening update failed; restoring the pre-update stack" >&2

  for container in businessgpt-bot businessgpt-api businessgpt-xray; do
    original="$(prehard_name "$container")"
    if docker container inspect "$original" >/dev/null 2>&1; then
      docker rm -f "$container" >/dev/null 2>&1 || true
      docker rename "$original" "$container" >/dev/null 2>&1 || true
    fi
  done

  restore_file "$api_env_existed" "$state_dir/api.env" "$api_env"
  restore_file "$bot_env_existed" "$state_dir/bot.env" "$bot_env"
  if [[ "$transition_started" == 1 ]]; then
    docker run --rm --user 0:0 \
      -v "$state_dir:/rollback:ro" \
      -v "$bot_dir/data:/data" \
      --entrypoint python businessgpt-bot:latest -c \
      'import pathlib,shutil; source=pathlib.Path("/rollback/bot.sqlite3"); target=pathlib.Path("/data/bot.sqlite3"); target.unlink(missing_ok=True); pathlib.Path(str(target)+"-wal").unlink(missing_ok=True); pathlib.Path(str(target)+"-shm").unlink(missing_ok=True); shutil.copy2(source,target)' \
      >/dev/null 2>&1 || true
  fi
  cp -p "$state_dir/Caddyfile" "$caddyfile"
  docker exec "$caddy_container" caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
    && docker exec "$caddy_container" caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1

  docker start businessgpt-xray >/dev/null 2>&1 || true
  docker start businessgpt-api >/dev/null 2>&1 || true
  docker start businessgpt-bot >/dev/null 2>&1 || true
  echo "Rollback state retained at: $state_dir" >&2
}

rollback_armed=1
transition_started=0
trap 'if [[ "$rollback_armed" == 1 ]]; then rollback_stack; fi' EXIT
trap 'exit 130' INT TERM

python3 "$bot_dir/migrate_businessgpt_env_vm.py" \
  --api-env "$api_env" \
  --bot-env "$bot_env" \
  --bot-db "$bot_db" \
  --rotate-api-key

# Load all final tags while the exact live containers are still available for
# rollback. Existing containers retain their immutable image IDs.
gunzip -c "$api_archive" | docker load >/dev/null
gunzip -c "$bot_archive" | docker load >/dev/null
gunzip -c "$xray_archive" | docker load >/dev/null

# Preserve the exact live containers until the hardened replacements pass all checks.
transition_started=1
for container in businessgpt-bot businessgpt-api businessgpt-xray; do
  docker stop "$container" >/dev/null
  docker rename "$container" "$(prehard_name "$container")"
done

"$xray_dir/run_businessgpt_xray_vm.sh"
"$api_dir/run_businessgpt_api_vm.sh"

api_health=""
for ((elapsed = 0; elapsed < api_timeout_seconds; elapsed += 2)); do
  if [[ "$(docker inspect businessgpt-api --format '{{.State.Running}}' 2>/dev/null || true)" != "true" ]]; then
    break
  fi
  if api_health="$(docker exec businessgpt-api python -c \
    'import json,os,urllib.request; r=urllib.request.Request("http://127.0.0.1:8000/health/ready",headers={"Authorization":"Bearer "+os.environ["BUSINESSGPT_API_KEY"]}); d=json.load(urllib.request.urlopen(r,timeout=3)); assert d.get("ok"); assert not os.environ.get("BUSINESSGPT_MMPROJ_PATH") or d.get("vision_enabled"); print(json.dumps(d,separators=(",",":")))' 2>/dev/null)"; then
    break
  fi
  api_health=""
  sleep 2
done
if [[ -z "$api_health" ]]; then
  docker logs --tail=100 businessgpt-api >&2 2>/dev/null || true
  exit 1
fi

"$bot_dir/run_businessgpt_bot_vm.sh"
bot_ready=0
for ((elapsed = 0; elapsed < bot_timeout_seconds; elapsed += 2)); do
  if [[ "$(docker inspect businessgpt-bot --format '{{.State.Running}}' 2>/dev/null || true)" != "true" ]]; then
    break
  fi
  if docker logs businessgpt-bot 2>&1 | grep -q 'Application started'; then
    bot_ready=1
    break
  fi
  sleep 2
done
if [[ "$bot_ready" != 1 ]]; then
  docker logs --tail=100 businessgpt-bot >&2 2>/dev/null || true
  exit 1
fi

if [[ -n "$legacy_caddy_hostname" ]]; then
  python3 "$api_dir/remove_caddy_site_vm.py" \
    --caddyfile "$caddyfile" \
    --hostname "$legacy_caddy_hostname" \
    --container "$caddy_container"
fi

for container in businessgpt-api businessgpt-bot businessgpt-xray; do
  if docker inspect "$container" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | grep -qw edge; then
    echo "$container is still attached to edge" >&2
    exit 1
  fi
done

docker exec businessgpt-bot python -c \
  'import socket; assert socket.gethostbyname("businessgpt-api"); assert socket.gethostbyname("businessgpt-xray")'

# Every old application container contains pre-rotation secrets. Remove them
# only after the final stack and Caddy route have passed all checks.
mapfile -t obsolete_containers < <(
  docker ps -a --format '{{.Names}}' \
    | grep -E '^(businessgpt-(api|bot|xray)-prehardening-|businessgpt-(api|bot)-old-)' \
    || true
)
for container in "${obsolete_containers[@]}"; do
  docker rm -f "$container" >/dev/null
done

# Remove backups left by the obsolete in-volume hardening transaction. Routine
# bot update backups remain untouched.
docker run --rm --user 0:0 \
  -v "$bot_dir/data/backups:/backups" \
  --entrypoint python businessgpt-bot:latest -c \
  'import pathlib,shutil; root=pathlib.Path("/backups"); [shutil.rmtree(path) for path in root.glob("hardening-*") if path.is_dir()]' \
  >/dev/null
rm -f "$state_dir/api.env" "$state_dir/bot.env"

rollback_armed=0
trap - EXIT INT TERM

echo "BusinessGPT hardening update complete"
echo "Pre-hardening SQLite and Caddy backup: $state_dir"
echo "$api_health"
docker ps --filter name=businessgpt --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
