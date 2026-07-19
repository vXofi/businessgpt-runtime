#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

XRAY_VERSION="${XRAY_VERSION:-26.6.1}"
UPSTREAM_IMAGE="ghcr.io/xtls/xray-core:${XRAY_VERSION}"
LOCAL_IMAGE="businessgpt-xray:${XRAY_VERSION}"

docker buildx build --platform linux/amd64 --load \
  --build-arg XRAY_IMAGE="${UPSTREAM_IMAGE}" \
  -f services/businessgpt-xray/Dockerfile \
  -t "${LOCAL_IMAGE}" .
docker save -o businessgpt-xray.tar "${LOCAL_IMAGE}"
gzip -f businessgpt-xray.tar
ls -lh businessgpt-xray.tar.gz
