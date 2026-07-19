#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LLAMA_CPP_REF="${LLAMA_CPP_REF:-a4107133a634250c8c9d888bc0bc8520dcfd6105}"

docker buildx build --platform linux/amd64 --load \
  --build-arg LLAMA_CPP_REF="$LLAMA_CPP_REF" \
  -f services/businessgpt-api/Dockerfile \
  -t businessgpt-api:llama-server services/businessgpt-api

docker save -o businessgpt-api.tar businessgpt-api:llama-server
gzip -f businessgpt-api.tar
ls -lh businessgpt-api.tar.gz
