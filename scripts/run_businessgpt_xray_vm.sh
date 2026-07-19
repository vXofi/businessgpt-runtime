#!/usr/bin/env bash
set -euo pipefail

XRAY_VERSION="${XRAY_VERSION:-26.6.1}"
XRAY_CONFIG_PATH="${XRAY_CONFIG_PATH:-/opt/businessgpt-xray/config.json}"

if [[ ! -f "${XRAY_CONFIG_PATH}" ]]; then
  echo "missing Xray config: ${XRAY_CONFIG_PATH}" >&2
  exit 1
fi

docker network inspect edge >/dev/null 2>&1 || docker network create edge

docker rm -f businessgpt-xray >/dev/null 2>&1 || true

docker run -d --name businessgpt-xray --restart unless-stopped \
  --user "$(id -u):$(id -g)" \
  --network edge \
  -v "${XRAY_CONFIG_PATH}:/etc/xray/config.json:ro" \
  "businessgpt-xray:${XRAY_VERSION}" \
  run -config /etc/xray/config.json
