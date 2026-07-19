# BusinessGPT Runtime

Infrastructure and deploy repo for services hosted on the deployment VM.

This repo is intentionally separate from the BusinessGPT training repo. It owns
runtime services, routing, Docker images, and operational notes. It must not
contain datasets, model artifacts, generations, notebooks, API keys, bot tokens,
or Hugging Face tokens.

## Current Services

| Domain | Service | Container | Public port |
| --- | --- | --- | --- |
| `businessgpt-api.example.com` | BusinessGPT inference API | `businessgpt-api` | via Caddy `443` |
| none | BusinessGPT Telegram bot | `businessgpt-bot` | none |
| `notes.example.com` | service A service-a | `service-a` | via Caddy `443` |

Public traffic should enter through one Caddy container on ports `80` and `443`.
Application containers should stay on a private Docker network.

## Target Architecture

```text
internet
  |
  | 80/443
  v
caddy edge proxy
  |-- businessgpt-api.example.com      -> businessgpt-api:8000
  |-- notes.example.com -> service-a:5984
  |-- service1.example.com -> service1:<private-port>
  |-- service2.example.com -> service2:<private-port>

businessgpt-bot uses Telegram long polling and calls businessgpt-api over edge.
It does not need a public domain or Caddy route.
```

Do not expose service ports publicly unless this is a short debug session.

## Repo Layout

```text
services/businessgpt-api/  FastAPI wrapper around llama-server
services/businessgpt-bot/  Telegram bot for prompts, model profiles, reactions
infra/caddy/               Caddy examples and routing notes
docs/                      New-chat context and operational notes
scripts/                   Local build and VM deploy helpers
examples/                  Client snippets for integrations
```

## SSH Usage

Connect directly to the VM:

```bash
ssh -i ~/.ssh/<key-file> deploy@192.0.2.10
```

Or add the example host from `scripts/ssh_config.example` to your SSH config,
then use the short alias:

```bash
ssh businessgpt
```

Run one command remotely without opening an interactive shell:

```bash
ssh businessgpt 'docker ps'
ssh businessgpt 'cd /opt/businessgpt && docker logs --tail=100 businessgpt-api'
```

Copy files to the VM:

```bash
scp -i ~/.ssh/<key-file> businessgpt-api.tar.gz deploy@192.0.2.10:/opt/businessgpt/
scp businessgpt-api.tar.gz businessgpt:/opt/businessgpt/
```

## First Setup On VM

Create a shared private network if it does not exist:

```bash
docker network create edge
```

Connect existing containers:

```bash
docker network connect edge caddy
docker network connect edge businessgpt-api
```

If a container is already connected, Docker will print an error. That is not
dangerous.

## BusinessGPT API

The runtime is native llama.cpp, not `llama-cpp-python`. The Qwen3.5 v16
language GGUF and base-model vision projector are pinned in
`services/businessgpt-api/model-manifest.json`. `llama-cpp-python` has
multimodal support, but the native server is used here so its exact backend
revision can be pinned and tested directly with these artifacts.

Prepare the projector locally before enabling vision:

```bash
scripts/prepare_businessgpt_language_gguf.sh
scripts/prepare_businessgpt_mmproj.sh
```

Build locally for the deployment VM:

```bash
scripts/build_businessgpt_api_amd64.sh
```

After the first deployment, API-only code updates are one command:

```bash
scripts/deploy_businessgpt_api.sh
```

Copy the resulting image archive to the VM:

```bash
scp -i ~/.ssh/<key-file> businessgpt-api.tar.gz deploy@192.0.2.10:/opt/businessgpt/
```

Load and run on the VM:

```bash
cd /opt/businessgpt
gunzip -c businessgpt-api.tar.gz | docker load
scripts/run_businessgpt_api_vm.sh
```

## BusinessGPT Telegram Bot

Build locally for the deployment VM:

```bash
scripts/build_businessgpt_bot_amd64.sh
```

After the first deployment, bot-only updates are one command:

```bash
scripts/deploy_businessgpt_bot.sh
```

The automated updater backs up SQLite, preserves the previous container, checks
real application readiness, and rolls back automatically on failure.

Copy the image archive and VM run helper:

```bash
ssh -i ~/.ssh/<key-file> deploy@192.0.2.10 'mkdir -p /opt/businessgpt-bot'
scp -i ~/.ssh/<key-file> businessgpt-bot.tar.gz scripts/run_businessgpt_bot_vm.sh \
  deploy@192.0.2.10:/opt/businessgpt-bot/
```

Load and run on the VM:

```bash
cd /opt/businessgpt-bot
gunzip -c businessgpt-bot.tar.gz | docker load
chmod +x run_businessgpt_bot_vm.sh

export BUSINESSGPT_BOT_TOKEN='<telegram-bot-token>'
export BUSINESSGPT_BOT_ADMIN_USER_IDS='<your-telegram-user-id>'
export BUSINESSGPT_BOT_TELEGRAM_PROXY_URL='socks5://businessgpt-xray:1080'
export BUSINESSGPT_API_KEY='<same-token-used-by-businessgpt-api>'
export BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64='<public-key-from-your-pc>'

./run_businessgpt_bot_vm.sh
```

The bot uses long polling, publishes no ports, and stores its SQLite DB under
`/opt/businessgpt-bot/data`. On networks that block Telegram, use the private
Xray/VLESS sidecar documented in `services/businessgpt-bot/README.md`; do not
route the whole VM through the tunnel.

After deployment, use `/settings` in the Telegram group for prompt, endpoint,
context, and sampling controls. JPEG/PNG/WebP input is handled only on explicit
`/gpt`, mention, or reply triggers.

## Caddy

The current production Caddy config is managed by the existing Service A Caddy
container. Update that Caddyfile rather than running a second public Caddy.

Expected path on VM:

```text
/etc/caddy/Caddyfile
```

Reload:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## Security Rules

- Public Yandex security group: allow TCP `80` and `443`.
- Restrict SSH `22` to your own IP.
- Close public `8000`.
- Use `BUSINESSGPT_API_KEY` for `/generate`.
- Keep secrets in VM env files or shell history-conscious commands, not in git.
