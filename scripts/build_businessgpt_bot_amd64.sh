#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker buildx build --platform linux/amd64 --load \
  -f services/businessgpt-bot/Dockerfile \
  -t businessgpt-bot:latest services/businessgpt-bot

docker save -o businessgpt-bot.tar businessgpt-bot:latest
gzip -f businessgpt-bot.tar
ls -lh businessgpt-bot.tar.gz
