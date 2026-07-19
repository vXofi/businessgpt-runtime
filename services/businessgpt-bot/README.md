# BusinessGPT Telegram Bot Service

Private Telegram bot container for BusinessGPT chat integration.

The bot uses Telegram long polling, so it does not expose a public HTTP port and
does not need a Caddy route. It runs on the VM's private `edge` Docker network
and calls `businessgpt-api` directly. When Telegram is unavailable from the VM,
an optional Xray sidecar sends only Telegram traffic through VLESS:

```text
Telegram <-> [businessgpt-xray] <-> businessgpt-bot
                                      |
                                      +-> http://businessgpt-api:8000/generate
```

## Helper Scripts

Use these from the repo root unless noted otherwise:

```text
scripts/build_businessgpt_bot_amd64.sh
```

Builds `businessgpt-bot:latest` for Linux amd64, saves it as
`businessgpt-bot.tar.gz`.

```text
scripts/run_businessgpt_bot_vm.sh
```

Runs on the VM. It creates `/opt/businessgpt-bot/data`, ensures Docker network
`edge` exists, replaces the old `businessgpt-bot` container, mounts SQLite data
at `/data`, and publishes no ports.

```text
scripts/vless_link_to_xray_config.py
```

Reads a VLESS XHTTP/TLS share link from a hidden prompt and writes a private
Xray client configuration with file mode `0600`. The link and credentials are
not printed.

```text
scripts/build_businessgpt_xray_amd64.sh
scripts/run_businessgpt_xray_vm.sh
```

Pulls and exports the official pinned Xray image locally, then runs it on the
VM as a private SOCKS sidecar with no published ports.

```text
scripts/export_businessgpt_feedback_samples.py
```

Runs on your PC against a copied `bot.sqlite3` file and decrypts reaction
feedback samples with the private key kept on your PC.

## Prerequisites

- `businessgpt-api` must already be deployed with the new `/generate`
  `system_prompt` support.
- `businessgpt-api` and `businessgpt-bot` must share Docker network `edge`.
- You need a Telegram bot token from BotFather.
- Configure at least one numeric Telegram user ID as a global recovery admin.
  Telegram owners and administrators are authorized automatically inside each
  group they manage.
- In BotFather, disable privacy mode if the bot should see normal group
  context.
- In the target group, make the bot admin if you want reaction updates.

## One-Time Key Setup

The bot encrypts stored generation samples with a public key. The VM gets only
the public key. The private key stays on your PC.

After building the image, generate keys without installing Python dependencies
on your Mac:

```bash
cd /Users/developer/Documents/businessgpt-server
scripts/build_businessgpt_bot_amd64.sh
docker run --rm businessgpt-bot:latest python -m bot.crypto
```

Output shape:

```text
BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64=...
PRIVATE_KEY_B64_KEEP_ON_PC=...
```

Use `BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64` on the VM. Keep
`PRIVATE_KEY_B64_KEEP_ON_PC` off the VM.

## Deploy Or Update

### Routine update

After the first deployment, run one command on the Mac:

```bash
cd /Users/developer/Documents/businessgpt-server
scripts/deploy_businessgpt_bot.sh
```

It builds Linux amd64 locally, uploads the image and VM helpers, recovers the
running container's environment without printing secrets, creates a timestamped
SQLite backup, starts the replacement, and waits for Telegram application
startup. A failed startup automatically restores the prior container.

The previous container and SQLite backup are retained after success. The script
prints their names so they can be removed manually after verification.

Connection defaults can be overridden without editing the script:

```bash
BUSINESSGPT_DEPLOY_TARGET=user@host \
BUSINESSGPT_SSH_KEY=~/.ssh/server-key \
scripts/deploy_businessgpt_bot.sh
```

### First deployment

Run on your Mac:

```bash
cd /Users/developer/Documents/businessgpt-server
scripts/build_businessgpt_bot_amd64.sh

ssh -i ~/.ssh/businessgpt_ed25519 deploy@192.0.2.10 'mkdir -p /opt/businessgpt-bot'
scp -i ~/.ssh/businessgpt_ed25519 businessgpt-bot.tar.gz scripts/run_businessgpt_bot_vm.sh \
  deploy@192.0.2.10:/opt/businessgpt-bot/
```

Run on the VM:

```bash
cd /opt/businessgpt-bot
gunzip -c businessgpt-bot.tar.gz | docker load
chmod +x run_businessgpt_bot_vm.sh

export BUSINESSGPT_BOT_TOKEN='<telegram-bot-token>'
export BUSINESSGPT_BOT_ADMIN_USER_IDS='<your-telegram-user-id>'
export BUSINESSGPT_API_KEY='<same-token-used-by-businessgpt-api>'
export BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64='<public-key-from-your-pc>'

./run_businessgpt_bot_vm.sh
docker logs --tail=100 businessgpt-bot
```

Optional runtime defaults:

```bash
export BUSINESSGPT_BOT_TELEGRAM_PROXY_URL='socks5://businessgpt-xray:1080'
export BUSINESSGPT_API_URL='http://businessgpt-api:8000/generate'
export BUSINESSGPT_BOT_DB_PATH='/data/bot.sqlite3'
export BUSINESSGPT_BOT_MAX_TOKENS=128
export BUSINESSGPT_BOT_TEMPERATURE=0.9
export BUSINESSGPT_BOT_TOP_P=0.9
export BUSINESSGPT_BOT_TOP_K=50
export BUSINESSGPT_BOT_REPETITION_PENALTY=1.2
```

For manual updates, `update_businessgpt_bot_vm.sh` performs the backup,
readiness check, and rollback around `run_businessgpt_bot_vm.sh`.

## VLESS XHTTP/TLS Egress

Use this only when the VM cannot connect directly to `api.telegram.org`. The
VLESS endpoint must be outside the blocked network. Do not put the VLESS share
link in a command argument, shell history, Git, or chat.

Build and export Xray on the Mac:

```bash
cd /Users/developer/Documents/businessgpt-server
scripts/build_businessgpt_xray_amd64.sh
```

Copy the archive, converter, and runner to the VM:

```bash
ssh -i ~/.ssh/businessgpt_ed25519 deploy@192.0.2.10 \
  'sudo install -d -o xofi -g xofi /opt/businessgpt-xray'

scp -i ~/.ssh/businessgpt_ed25519 \
  businessgpt-xray.tar.gz \
  scripts/vless_link_to_xray_config.py \
  scripts/run_businessgpt_xray_vm.sh \
  deploy@192.0.2.10:/opt/businessgpt-xray/
```

On the VM, load the image and generate the private config. The converter asks
for the share link without echoing it:

```bash
cd /opt/businessgpt-xray
gunzip -c businessgpt-xray.tar.gz | docker load
chmod +x vless_link_to_xray_config.py run_businessgpt_xray_vm.sh
./vless_link_to_xray_config.py --output /opt/businessgpt-xray/config.json
./run_businessgpt_xray_vm.sh
```

Verify Telegram through the private SOCKS endpoint without displaying the bot
token:

```bash
docker run --rm --network edge curlimages/curl:8.15.0 \
  --proxy socks5h://businessgpt-xray:1080 \
  --connect-timeout 15 \
  https://api.telegram.org
```

Then restart the bot with its Telegram proxy configured:

```bash
export BUSINESSGPT_BOT_TELEGRAM_PROXY_URL='socks5://businessgpt-xray:1080'
./run_businessgpt_bot_vm.sh
```

The bot-to-API request remains direct on `edge`; only Telegram SDK requests use
the proxy.

## Runtime Shape

Container:

```text
businessgpt-bot
```

Docker network:

```text
edge
```

Persistent VM mount:

```text
/opt/businessgpt-bot/data:/data
```

SQLite path inside container:

```text
/data/bot.sqlite3
```

There is no `-p` port publish and no Caddy route.

Optional private proxy container:

```text
businessgpt-xray
```

## Telegram Usage

Generation:

```text
/gpt <message>
```

The bot also answers when someone replies to a bot message or mentions the bot.
An image is sent to the model when a JPEG, PNG, or WebP is attached to `/gpt`
or a mention, when an image is sent as a reply to the bot, or when text replies
directly to an image. Standalone unaddressed images remain silent. The bot
accepts one image up to 10 MB.

First checks:

```text
/status
/settings
/gpt test
```

`/settings` is the primary admin UI. Telegram group owners/admins can use it
for their own group; `BUSINESSGPT_BOT_ADMIN_USER_IDS` remains a global
break-glass override. The private editor link checks live group membership
before opening and on subsequent actions. The menu selects prompt and model profiles and
sets per-chat recent context depth, max tokens, temperature, top P, top K, and
repeat penalty. Group settings include an **Edit in private chat** button; its
BotFather deep link binds private prompt/model commands to that group for 24
hours. Recent context defaults to zero and, when enabled, stores at most 20
messages in RAM only. A bot restart clears it.

Pressing the active prompt button opens its text preview. Saved presets are
read from SQLite; `API default` is fetched from the authenticated model API so
the preview reflects the prompt currently used by that endpoint.

The bot registers Telegram command suggestions at startup. Group members see
`/gpt`; Telegram group administrators see the management commands as well.
Button callbacks are included explicitly in the long-polling update list.

Prompt presets, group-admin or global-admin only:

```text
/prompts
/prompt_show <name>
/prompt_use <name>
/prompt_new <name>
/prompt_edit <name>
/prompt_delete <name>
/prompt_reset
/prompt_test <name> <message>
```

For long prompt text:

```text
/prompt_new drunk_short
<send next message as prompt text>
```

Model profiles, group-admin or global-admin only:

```text
/models
/model_show <name>
/model_use <name>
/model_new <name> <api_url> [max_tokens] [temperature] [top_p] [top_k] [repetition_penalty]
/model_edit <name> <api_url> [max_tokens] [temperature] [top_p] [top_k] [repetition_penalty]
/model_delete <name>
```

The default model profile is created only when missing. After that, model
profile edits made from Telegram persist across restarts.

Other admin commands:

```text
/tools
```

## Reaction Feedback

When a user adds a reaction to a bot answer, the bot records the reaction event
and links it to the encrypted generation sample for that answer. Removing a
reaction does not select a previously unselected sample.

Text generation samples are sealed-box encrypted immediately. Image bytes are
also encrypted immediately, then kept as pending ciphertext for at most 24
hours under `/data/pending-media` with a 256 MB cap. Only a non-empty reaction
promotes the encrypted image into SQLite; expired or evicted pending images are
deleted. The VM has only the public key and cannot decrypt either form.

Only bot outbound messages are eligible for reaction feedback. Reactions to
other group messages are ignored.

To inspect reacted samples, copy the SQLite DB to your PC and decrypt locally:

```bash
cd /Users/developer/Documents/businessgpt-server
ssh -i ~/.ssh/businessgpt_ed25519 deploy@192.0.2.10 \
  'docker exec businessgpt-bot python -c "import sqlite3; s=sqlite3.connect(\"/data/bot.sqlite3\"); d=sqlite3.connect(\"/data/bot-backup.sqlite3\"); s.backup(d); d.close(); s.close()"'
scp -i ~/.ssh/businessgpt_ed25519 \
  deploy@192.0.2.10:/opt/businessgpt-bot/data/bot-backup.sqlite3 \
  ./bot.sqlite3

export BUSINESSGPT_BOT_FEEDBACK_PRIVATE_KEY_B64='<private-key-kept-on-pc>'
scripts/export_businessgpt_feedback_samples.py ./bot.sqlite3 -o feedback.jsonl
```

Export retained reacted images on the PC:

```bash
scripts/export_businessgpt_feedback_samples.py ./bot.sqlite3 \
  --media-output-dir ./feedback-media \
  -o feedback.jsonl
```

To include samples that do not have reactions yet:

```bash
scripts/export_businessgpt_feedback_samples.py ./bot.sqlite3 \
  --include-pending \
  -o feedback-all.jsonl
```

## Troubleshooting

Check container status:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker logs --tail=100 businessgpt-bot
```

Check Docker network membership:

```bash
docker inspect businessgpt-bot --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker inspect businessgpt-api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

Check API reachability from the bot container:

```bash
docker exec businessgpt-bot python - <<'PY'
import httpx
print(httpx.get('http://businessgpt-api:8000/health', timeout=10).text)
PY
```

If reaction feedback does not appear, confirm the bot is an admin in the group
and that BotFather privacy mode is disabled.
