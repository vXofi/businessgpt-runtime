#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

target="${BUSINESSGPT_DEPLOY_TARGET:?Set BUSINESSGPT_DEPLOY_TARGET to user@host}"
ssh_key="${BUSINESSGPT_SSH_KEY:?Set an explicit SSH identity file}"
remote_dir="/opt/businessgpt-bot"

scripts/build_businessgpt_bot_amd64.sh

rsync -avP -e "ssh -i $ssh_key" \
  businessgpt-bot.tar.gz \
  scripts/run_businessgpt_bot_vm.sh \
  scripts/update_businessgpt_bot_vm.sh \
  scripts/set_businessgpt_image_generation_vm.sh \
  "$target:$remote_dir/"

ssh -i "$ssh_key" "$target" \
  "cd '$remote_dir' && chmod +x run_businessgpt_bot_vm.sh update_businessgpt_bot_vm.sh set_businessgpt_image_generation_vm.sh && ./update_businessgpt_bot_vm.sh"
