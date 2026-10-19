#!/usr/bin/env bash
set -euo pipefail

env_file="${BUSINESSGPT_BOT_ENV_FILE:-/opt/businessgpt-bot/bot.env}"
backend_network="${BUSINESSGPT_BACKEND_NETWORK:-businessgpt-backend}"
proxy_network="${BUSINESSGPT_PROXY_NETWORK:-businessgpt-proxy}"
egress_network="${BUSINESSGPT_BOT_EGRESS_NETWORK:-}"
data_dir="/opt/businessgpt-bot/data"

[[ -f "$env_file" ]] || { echo "Missing bot env file: $env_file" >&2; exit 1; }
[[ "$(stat -c '%a' "$env_file")" =~ ^(400|600)$ ]] || {
  echo "Bot env file must have mode 0600 or 0400: $env_file" >&2
  exit 1
}
if [[ -z "$egress_network" ]]; then
  egress_network="$(sed -n 's/^BUSINESSGPT_BOT_EGRESS_NETWORK=//p' "$env_file" | tail -n 1)"
fi
for name in \
  BUSINESSGPT_BOT_TOKEN \
  BUSINESSGPT_BOT_OWNER_USER_IDS \
  BUSINESSGPT_BOT_ALLOWED_CHAT_IDS \
  BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64
do
  grep -Eq "^${name}=.+$" "$env_file" || {
    echo "$name is missing from $env_file" >&2
    exit 1
  }
done

image_generation_enabled="$({
  sed -n 's/^BUSINESSGPT_IMAGE_GENERATION_ENABLED=//p' "$env_file" || true
} | tail -n 1)"
image_generation_enabled="${image_generation_enabled:-false}"
image_env_args=()
case "$image_generation_enabled" in
  true)
    grep -Eq '^BUSINESSGPT_IMAGE_API_KEY=.+$' "$env_file" || {
      echo "BUSINESSGPT_IMAGE_API_KEY is required when image generation is enabled" >&2
      exit 1
    }
    ;;
  false)
    image_env_args=(-e BUSINESSGPT_IMAGE_API_KEY=)
    ;;
  *)
    echo "BUSINESSGPT_IMAGE_GENERATION_ENABLED must be true or false" >&2
    exit 1
    ;;
esac

docker image inspect businessgpt-bot:latest >/dev/null
# Validate transport-specific keys before replacing a running container.
docker run --rm --network none --env-file "$env_file" \
  "${image_env_args[@]}" --entrypoint python businessgpt-bot:latest \
  -c 'from bot.config import Config; Config.from_env()'
docker network inspect "$backend_network" >/dev/null 2>&1 \
  || docker network create --internal "$backend_network" >/dev/null
docker network inspect "$proxy_network" >/dev/null 2>&1 \
  || docker network create --internal "$proxy_network" >/dev/null
if [[ -n "$egress_network" ]]; then
  docker network inspect "$egress_network" >/dev/null 2>&1 \
    || docker network create "$egress_network" >/dev/null
fi
mkdir -p "$data_dir"
docker run --rm --user 0:0 --entrypoint chown \
  -v "$data_dir:/data" businessgpt-bot:latest -R 10001:10001 /data

docker rm -f businessgpt-bot >/dev/null 2>&1 || true
docker create --name businessgpt-bot --restart unless-stopped \
  --network "$backend_network" \
  --env-file "$env_file" \
  "${image_env_args[@]}" \
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
if [[ -n "$egress_network" ]]; then
  docker network connect "$egress_network" businessgpt-bot
fi
docker start businessgpt-bot >/dev/null
docker inspect businessgpt-bot --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
