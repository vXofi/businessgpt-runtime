#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

target="${BUSINESSGPT_DEPLOY_TARGET:?Set BUSINESSGPT_DEPLOY_TARGET to user@host}"
ssh_key="${BUSINESSGPT_SSH_KEY:-$HOME/.ssh/businessgpt_ed25519}"

scripts/build_businessgpt_api_amd64.sh
scripts/build_businessgpt_bot_amd64.sh
scripts/build_businessgpt_xray_amd64.sh

rsync -avP -e "ssh -i $ssh_key" \
  businessgpt-api.tar.gz \
  scripts/run_businessgpt_api_vm.sh \
  scripts/update_businessgpt_api_vm.sh \
  scripts/remove_caddy_site_vm.py \
  "$target:/opt/businessgpt/"

rsync -avP -e "ssh -i $ssh_key" \
  businessgpt-bot.tar.gz \
  scripts/run_businessgpt_bot_vm.sh \
  scripts/update_businessgpt_bot_vm.sh \
  scripts/migrate_businessgpt_env_vm.py \
  scripts/update_businessgpt_stack_vm.sh \
  "$target:/opt/businessgpt-bot/"

rsync -avP -e "ssh -i $ssh_key" \
  businessgpt-xray.tar.gz \
  scripts/run_businessgpt_xray_vm.sh \
  "$target:/opt/businessgpt-xray/"

ssh -i "$ssh_key" "$target" \
  "chmod +x /opt/businessgpt/*.sh /opt/businessgpt/*.py /opt/businessgpt-bot/*.sh /opt/businessgpt-bot/*.py /opt/businessgpt-xray/*.sh && /opt/businessgpt-bot/update_businessgpt_stack_vm.sh"
