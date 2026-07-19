#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

XRAY_VERSION="${XRAY_VERSION:-26.6.1}"
XRAY_DIGEST="${XRAY_DIGEST:-sha256:3943bddece5fab72308a4415917f0391917b7b81ddbf50708988b89e6e2bd213}"
UPSTREAM_IMAGE="ghcr.io/xtls/xray-core:${XRAY_VERSION}@${XRAY_DIGEST}"
LOCAL_IMAGE="businessgpt-xray:${XRAY_VERSION}"

docker buildx build --platform linux/amd64 --load \
  --build-arg XRAY_IMAGE="${UPSTREAM_IMAGE}" \
  -f services/businessgpt-xray/Dockerfile \
  -t "${LOCAL_IMAGE}" services/businessgpt-xray
docker save -o businessgpt-xray.tar "${LOCAL_IMAGE}"
gzip -f businessgpt-xray.tar
ls -lh businessgpt-xray.tar.gz
