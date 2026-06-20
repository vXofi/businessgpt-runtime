#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker buildx build --platform linux/amd64 --load \
  -f services/businessgpt-api/Dockerfile \
  -t businessgpt-api:llama-server .

docker save -o businessgpt-api.tar businessgpt-api:llama-server
gzip -f businessgpt-api.tar
ls -lh businessgpt-api.tar.gz

