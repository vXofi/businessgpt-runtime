# BusinessGPT Telegram Bot

Private long-polling Telegram integration for BusinessGPT. It owns per-group
prompt state, generation settings, image triggers, and encrypted reaction
samples. It publishes no port and has no Caddy route.

## Runtime Topology

```text
businessgpt-bot
  |-- businessgpt-backend (internal) -> businessgpt-api:8000
  `-- businessgpt-proxy   (internal) -> businessgpt-xray:1080

businessgpt-xray -> businessgpt-egress -> VLESS -> api.telegram.org
```

Only the bot can reach both the inference API and SOCKS proxy. The API cannot
reach the internet; Xray cannot reach the API; unrelated `edge` containers can
reach neither.

Persistent host data is `/opt/businessgpt-bot/data`, mounted at `/data`.
SQLite defaults to `/data/bot.sqlite3`.

## Helper Scripts

Run local scripts from repository root.

| Script | Where | Purpose |
| --- | --- | --- |
| `build_businessgpt_bot_amd64.sh` | Mac | Build and save `businessgpt-bot.tar.gz` |
| `deploy_businessgpt_bot.sh` | Mac | Routine build, upload, backup, update, readiness, rollback |
| `deploy_businessgpt_hardening.sh` | Mac | One-time migration of the existing full stack |
| `run_businessgpt_bot_vm.sh` | VM | Recreate the hardened bot container from `bot.env` |
| `update_businessgpt_bot_vm.sh` | VM | Transactional bot-only image update |
| `vless_link_to_xray_config.py` | VM | Hidden-input VLESS link conversion to mode-`0600` JSON |
| `build_businessgpt_xray_amd64.sh` | Mac | Build/save the pinned Xray image |
| `run_businessgpt_xray_vm.sh` | VM | Recreate isolated Xray networks/container |
| `export_businessgpt_feedback_samples.py` | Mac | Decrypt copied feedback DB and optional media |

Docker builds use only `services/businessgpt-bot` as context. The root and
service `.dockerignore` files prevent local secrets, artifacts, and databases
from entering the build context.

## Required Telegram Setup

1. Create the bot with BotFather.
2. Disable group privacy if the bot should retain ordinary recent messages as
   context.
3. Add the bot only to intended groups; application code still enforces the
   explicit group allowlist.
4. Make it a group administrator so Telegram delivers reaction updates.

At startup, the bot registers command suggestions. Ordinary group members see
`/gpt`; group administrators see the settings commands.

## Authorization Model

`BUSINESSGPT_BOT_ALLOWED_CHAT_IDS` is mandatory and contains negative Telegram
group/supergroup IDs. Updates from every other group are stopped before message
caching, command processing, generation, or reaction storage.

Roles:

- Any member of an allowed group can trigger generation.
- A live Telegram owner/admin can manage that allowed group's prompt presets,
  active model choice, context depth, and sampling settings.
- `BUSINESSGPT_BOT_OWNER_USER_IDS` can recover settings and is the only role
  allowed to create, edit, or delete global model endpoint profiles.
- Private settings links are bound to the encoded target group. Every callback
  repeats the allowlist and live group-admin check, so an old button cannot
  modify a newly selected group.

Endpoint URLs are normalized and must exactly match
`BUSINESSGPT_BOT_ALLOWED_API_URLS`. Credentials, query strings, fragments, and
paths other than `/generate` are rejected. The production bearer key is never
sent before this check and redirects are disabled.

## Configuration

Production file: `/opt/businessgpt-bot/bot.env`, mode `0600` or `0400`.

Required:

```text
BUSINESSGPT_BOT_TOKEN=
BUSINESSGPT_BOT_OWNER_USER_IDS=
BUSINESSGPT_BOT_ALLOWED_CHAT_IDS=
BUSINESSGPT_API_KEY=
BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64=<PC public key>
```

Production routing:

```text
BUSINESSGPT_BOT_TELEGRAM_PROXY_URL=socks5://businessgpt-xray:1080
BUSINESSGPT_API_URL=http://businessgpt-api:8000/generate
BUSINESSGPT_BOT_ALLOWED_API_URLS=http://businessgpt-api:8000/generate
BUSINESSGPT_BOT_DB_PATH=/data/bot.sqlite3
BUSINESSGPT_BOT_MEDIA_CACHE_DIR=/data/pending-media
```

Operational defaults are listed in the root `.env.example`. Database and media
paths must be absolute paths below `/data`; values elsewhere would be lost when
the container is replaced and are rejected at startup.

Do not put literal tokens in `export` commands. The one-time migration helper
recovers values from the existing containers, writes private env files, and
rotates the API key without printing it.

## Feedback Encryption Key

Generate the sealed-box key pair locally from the final bot image:

```bash
docker run --rm businessgpt-bot:latest python -m bot.crypto
```

Store `BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64` on the VM. Keep
`PRIVATE_KEY_B64_KEEP_ON_PC` only on the PC and in a separate backup. The VM
cannot decrypt stored payloads or images.

## Deploy And Update

One-time migration of the currently running stack:

```bash
scripts/deploy_businessgpt_hardening.sh
```

Routine bot-only update:

```bash
scripts/deploy_businessgpt_bot.sh
```

The routine updater obtains the configured DB path from the container, creates
a consistent SQLite backup under `/data/backups`, starts the replacement, and
waits for Telegram's `Application started` log. Failure renames and restarts the
previous container. Success keeps two old bot containers and seven update
backups. Docker logs rotate at 10 MiB with three files.

## Telegram Usage

Generation triggers:

```text
/gpt <message>
reply to a bot answer
mention the bot
```

An attached JPEG, PNG, or WebP is included when `/gpt`, a mention, or a reply
triggers the message. Replying with text to an image also sends the replied-to
image. Standalone unaddressed images remain silent. Input is limited to one
image and 10 MB.

Generated output is split below Telegram's message limit. ASCII `@` is removed
before sending so model output cannot notify usernames.

First checks:

```text
/status
/settings
/gpt test
```

The `/settings` menu shows the active prompt and model names. Press the prompt
button to preview actual prompt text; API-default text is fetched from the
authenticated API. Group settings offer an encoded private-editor link for
prompt creation/editing. Context choices are `0`, `5`, `10`, and `20`, with 10
as the default.

Prompt presets are scoped per group:

```text
/prompts
/prompt_show [name]
/prompt_use <name>
/prompt_new <name> [text]
/prompt_edit <name> [text]
/prompt_delete <name>
/prompt_reset
/prompt_test <name> <message>
```

For a long prompt:

```text
/prompt_new drunk_short
<the next message becomes the prompt text>
```

The pending action expires after 15 minutes. `/cancel` clears it immediately.
Validation failures leave the editor active instead of consuming the next
message.

Model selection is available to group admins; endpoint mutation is owner-only:

```text
/models
/model_show [name]
/model_use <name>
/model_new <name> <api_url> [max] [temp] [top_p] [top_k] [repeat]
/model_edit <name> <api_url> [max] [temp] [top_p] [top_k] [repeat]
/model_delete <name>
```

All persisted sampling values are finite and centrally range-checked. Invalid
or stale endpoint profiles are quarantined at startup.

## Context And Admission

The live context cache stores at most 20 messages per chat in RAM. It has a
64-chat LRU bound and six-hour idle TTL by default. A restart clears it. The
selected context included in a generated answer is copied into that answer's
encrypted feedback payload, so reaction-selected training samples remain
self-contained after the RAM cache expires.

The bot handles ordinary Telegram updates concurrently while generation jobs
run in a bounded in-memory FIFO queue. Generation defaults to one active job,
eight waiting jobs, three accepted requests per user per minute, and ten per
chat per minute. A queued user sees a temporary position message, which is
deleted when their generation starts. Only a full queue or a rate limit rejects
new work, so settings and reactions remain responsive behind a long model call.

## Reaction Data And Retention

Before any reaction, each answer stores sealed schema-v2 ciphertext containing:

- exact system prompt text, name, and hash;
- complete request messages and retained chat context;
- model profile, generation settings, and API URL;
- model/projector hashes, llama.cpp revision, and context-fit metadata;
- complete API response and final Telegram answer.

Image content is represented by a hash in the JSON payload. The encrypted image
bytes are held separately and promoted only if the answer receives a non-empty
reaction.

Every add/change/remove reaction transition is retained. A non-empty reaction
marks the sample for long-term retention; removing it later does not erase the
training event. This creates a labeled feedback corpus. Telegram reactions are
scalar or categorical feedback, not automatically pairwise RLHF preferences;
the exporter preserves enough provenance to build later reward-model, SFT, or
preference datasets intentionally.

Default limits:

| Data | Retention |
| --- | --- |
| Unreacted encrypted samples | 7 days, 5,000 rows, 512 MiB |
| Reacted encrypted samples | 50,000 rows, 4 GiB |
| Pending encrypted image files | 24 hours, 256 MiB |
| Reacted encrypted image media | 2 GiB |

Hourly maintenance removes expired/orphaned data, enforces caps, checkpoints
the WAL, and warns when free disk space falls below 5 GiB.

## Local Export

Create a consistent DB copy on the VM without stopping the bot:

```bash
ssh -i ~/.ssh/businessgpt_ed25519 deploy@192.0.2.10 \
  'docker exec businessgpt-bot python -c '\''import sqlite3; s=sqlite3.connect("/data/bot.sqlite3"); d=sqlite3.connect("/data/feedback-export.sqlite3"); s.backup(d); d.close(); s.close()'\'''

scp -i ~/.ssh/businessgpt_ed25519 \
  deploy@192.0.2.10:/opt/businessgpt-bot/data/feedback-export.sqlite3 \
  ./bot.sqlite3
```

Read the private key without putting it in shell history, then export reacted
records:

```bash
read -r -s BUSINESSGPT_BOT_FEEDBACK_PRIVATE_KEY_B64
export BUSINESSGPT_BOT_FEEDBACK_PRIVATE_KEY_B64
printf '\n'
scripts/export_businessgpt_feedback_samples.py ./bot.sqlite3 \
  --media-output-dir ./feedback-media \
  -o feedback.jsonl
unset BUSINESSGPT_BOT_FEEDBACK_PRIVATE_KEY_B64
```

Add `--include-pending` only when intentionally inspecting the unreacted
seven-day buffer. Exported JSONL schema v2 contains full reaction history and
the final reaction state. The exporter remains compatible with schema-v1 rows.

## Troubleshooting

```bash
docker ps --filter name=businessgpt \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker logs --tail=100 businessgpt-bot
docker inspect businessgpt-bot \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

Expected bot networks: `businessgpt-backend businessgpt-proxy`.

Verify private DNS from the running bot:

```bash
docker exec businessgpt-bot python -c \
  'import socket; print(socket.gethostbyname("businessgpt-api")); print(socket.gethostbyname("businessgpt-xray"))'
```

If reactions are absent, confirm the bot is a Telegram group admin and that
reaction updates are enabled for the bot. If ordinary context is absent,
confirm BotFather privacy mode is disabled.
