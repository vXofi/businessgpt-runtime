#!/usr/bin/env bash
set -euo pipefail

XRAY_VERSION="${XRAY_VERSION:-26.6.1}"
XRAY_CONFIG_PATH="${XRAY_CONFIG_PATH:-/opt/businessgpt-xray/config.json}"
proxy_network="${BUSINESSGPT_PROXY_NETWORK:-businessgpt-proxy}"
egress_network="${BUSINESSGPT_EGRESS_NETWORK:-businessgpt-egress}"

[[ -f "$XRAY_CONFIG_PATH" ]] || {
  echo "missing Xray config: $XRAY_CONFIG_PATH" >&2
  exit 1
}

docker network inspect "$proxy_network" >/dev/null 2>&1 \
  || docker network create --internal "$proxy_network" >/dev/null
docker network inspect "$egress_network" >/dev/null 2>&1 \
  || docker network create "$egress_network" >/dev/null

docker rm -f businessgpt-xray >/dev/null 2>&1 || true
docker create --name businessgpt-xray --restart unless-stopped \
  --user "$(id -u):$(id -g)" \
  --network "$egress_network" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit "${BUSINESSGPT_XRAY_PIDS_LIMIT:-128}" \
  --memory "${BUSINESSGPT_XRAY_MEMORY_LIMIT:-256m}" \
  --memory-swap "${BUSINESSGPT_XRAY_MEMORY_LIMIT:-256m}" \
  --cpus "${BUSINESSGPT_XRAY_CPU_LIMIT:-1}" \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  -v "$XRAY_CONFIG_PATH:/etc/xray/config.json:ro" \
  "businessgpt-xray:${XRAY_VERSION}" \
  run -config /etc/xray/config.json >/dev/null
docker network connect "$proxy_network" businessgpt-xray
docker start businessgpt-xray >/dev/null
docker inspect businessgpt-xray --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
