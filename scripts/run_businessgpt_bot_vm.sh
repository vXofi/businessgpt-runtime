#!/usr/bin/env bash
set -euo pipefail

env_file="${BUSINESSGPT_BOT_ENV_FILE:-/opt/businessgpt-bot/bot.env}"
backend_network="${BUSINESSGPT_BACKEND_NETWORK:-businessgpt-backend}"
proxy_network="${BUSINESSGPT_PROXY_NETWORK:-businessgpt-proxy}"
data_dir="/opt/businessgpt-bot/data"

[[ -f "$env_file" ]] || { echo "Missing bot env file: $env_file" >&2; exit 1; }
[[ "$(stat -c '%a' "$env_file")" =~ ^(400|600)$ ]] || {
  echo "Bot env file must have mode 0600 or 0400: $env_file" >&2
  exit 1
}
for name in \
  BUSINESSGPT_BOT_TOKEN \
  BUSINESSGPT_BOT_OWNER_USER_IDS \
  BUSINESSGPT_BOT_ALLOWED_CHAT_IDS \
  BUSINESSGPT_API_KEY \
  BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64
do
  grep -Eq "^${name}=.+$" "$env_file" || {
    echo "$name is missing from $env_file" >&2
    exit 1
  }
done

docker image inspect businessgpt-bot:latest >/dev/null
docker network inspect "$backend_network" >/dev/null 2>&1 \
  || docker network create --internal "$backend_network" >/dev/null
docker network inspect "$proxy_network" >/dev/null 2>&1 \
  || docker network create --internal "$proxy_network" >/dev/null
mkdir -p "$data_dir"
docker run --rm --user 0:0 --entrypoint chown \
  -v "$data_dir:/data" businessgpt-bot:latest -R 10001:10001 /data

docker rm -f businessgpt-bot >/dev/null 2>&1 || true
docker create --name businessgpt-bot --restart unless-stopped \
  --network "$backend_network" \
  --env-file "$env_file" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit "${BUSINESSGPT_BOT_PIDS_LIMIT:-128}" \
  --memory "${BUSINESSGPT_BOT_MEMORY_LIMIT:-512m}" \
  --memory-swap "${BUSINESSGPT_BOT_MEMORY_LIMIT:-512m}" \
  --cpus "${BUSINESSGPT_BOT_CPU_LIMIT:-1}" \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  -v "$data_dir:/data" \
  businessgpt-bot:latest >/dev/null
docker network connect "$proxy_network" businessgpt-bot
docker start businessgpt-bot >/dev/null
docker inspect businessgpt-bot --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
