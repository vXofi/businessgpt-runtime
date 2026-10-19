#!/usr/bin/env bash
set -euo pipefail

mode="${1:-}"
case "$mode" in
  enable) value=true ;;
  disable) value=false ;;
  *)
    echo "Usage: $0 <enable|disable>" >&2
    exit 2
    ;;
esac

cd "$(dirname "$0")"
env_file="${BUSINESSGPT_BOT_ENV_FILE:-/opt/businessgpt-bot/bot.env}"

[[ -f "$env_file" ]] || { echo "Missing bot env file: $env_file" >&2; exit 1; }
[[ "$(stat -c '%a' "$env_file")" =~ ^(400|600)$ ]] || {
  echo "Bot env file must have mode 0600 or 0400: $env_file" >&2
  exit 1
}
if [[ "$value" == true ]] && ! grep -Eq '^BUSINESSGPT_IMAGE_API_KEY=.+$' "$env_file"; then
  echo "BUSINESSGPT_IMAGE_API_KEY is required before enabling" >&2
  exit 1
fi

backup="$(mktemp "$(dirname "$env_file")/.bot.env.image-toggle.XXXXXX")"
temporary="$(mktemp "$(dirname "$env_file")/.bot.env.image-write.XXXXXX")"
cp -p "$env_file" "$backup"

restore_env() {
  cp -p "$backup" "$env_file"
  rm -f "$backup" "$temporary"
}
trap restore_env EXIT INT TERM

awk -v value="$value" '
  BEGIN { replaced = 0 }
  /^BUSINESSGPT_IMAGE_GENERATION_ENABLED=/ {
    if (!replaced) print "BUSINESSGPT_IMAGE_GENERATION_ENABLED=" value
    replaced = 1
    next
  }
  { print }
  END {
    if (!replaced) print "BUSINESSGPT_IMAGE_GENERATION_ENABLED=" value
  }
' "$env_file" >"$temporary"
chmod --reference="$env_file" "$temporary"
mv "$temporary" "$env_file"

./update_businessgpt_bot_vm.sh

trap - EXIT INT TERM
rm -f "$backup"
echo "Image generation server policy: $value"
