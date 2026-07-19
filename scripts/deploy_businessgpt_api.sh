#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

target="${BUSINESSGPT_DEPLOY_TARGET:?Set BUSINESSGPT_DEPLOY_TARGET to user@host}"
ssh_key="${BUSINESSGPT_SSH_KEY:-$HOME/.ssh/businessgpt_ed25519}"
remote_dir="/opt/businessgpt"

scripts/build_businessgpt_api_amd64.sh

rsync -avP -e "ssh -i $ssh_key" \
  businessgpt-api.tar.gz \
  scripts/run_businessgpt_api_vm.sh \
  scripts/update_businessgpt_api_vm.sh \
  "$target:$remote_dir/"

ssh -i "$ssh_key" "$target" \
  "cd '$remote_dir' && chmod +x run_businessgpt_api_vm.sh update_businessgpt_api_vm.sh && ./update_businessgpt_api_vm.sh"
