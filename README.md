# BusinessGPT Runtime

Infrastructure and deployment repository for the deployment VM. BusinessGPT model
training lives elsewhere; this repository owns runtime images, private service
networks, Caddy routes, and operational scripts.

Do not commit secrets, model files, datasets, generated samples, databases, or
VLESS links.

## Current Shape

| Public name | Service | Container |
| --- | --- | --- |
| `notes.example.com` | service A | `service-a` |
| `service-b.example.com` | service B | `service-b` |
| none | BusinessGPT API | `businessgpt-api` |
| none | BusinessGPT Telegram bot | `businessgpt-bot` |

Only Caddy publishes host ports `80` and `443`. The BusinessGPT API is not a
public service and has no Caddy route.

```text
internet -> caddy -> edge -> public web application containers

businessgpt-bot -> businessgpt-backend (internal) -> businessgpt-api
businessgpt-bot -> businessgpt-proxy (internal) -> businessgpt-xray
businessgpt-xray -> businessgpt-egress -> Telegram through VLESS
```

The isolated BusinessGPT networks prevent Caddy, service-a, and unrelated web
containers from reaching the inference API or its SOCKS proxy.

## Repository Layout

```text
services/businessgpt-api/   FastAPI wrapper plus native llama.cpp server
services/businessgpt-bot/   Telegram bot, settings, and encrypted feedback
services/businessgpt-xray/  Pinned Xray egress image
infra/caddy/                Public reverse-proxy examples
docs/                       Architecture and handoff notes
scripts/                    Local builds, VM updates, exports, and recovery
```

## SSH Usage

Direct connection:

```bash
ssh -i ~/.ssh/businessgpt_ed25519 deploy@192.0.2.10
```

With the host entry from `scripts/ssh_config.example` installed in
`~/.ssh/config`:

```bash
ssh businessgpt
ssh businessgpt 'docker ps'
ssh businessgpt 'docker logs --tail=100 businessgpt-bot'
```

Copy to a user-writable application directory, not directly into a root-owned
path:

```bash
scp -i ~/.ssh/businessgpt_ed25519 businessgpt-api.tar.gz \
  deploy@192.0.2.10:/opt/businessgpt/
scp businessgpt-bot.tar.gz businessgpt:/opt/businessgpt-bot/
```

## BusinessGPT Runtime

The API uses native `llama-server`, not `llama-cpp-python`. The image pins
llama.cpp commit `a4107133a634250c8c9d888bc0bc8520dcfd6105`; model and projector
hashes are pinned in `services/businessgpt-api/model-manifest.json` and verified
at every startup.

Production model files on the VM:

```text
/opt/businessgpt/models/model-Q5_K_M-no-mtp.gguf
/opt/businessgpt/models/mmproj-businessgpt-v16-f16.gguf
```

Build both application images locally for the amd64 VM:

```bash
scripts/build_businessgpt_api_amd64.sh
scripts/build_businessgpt_bot_amd64.sh
```

The service-specific Docker contexts are deny-by-default. Local `.env` files,
databases, and `.artifacts` are not sent to Docker during these builds.

## Harden Existing VM

For a legacy public/flat-network deployment, run once from the Mac:

```bash
scripts/deploy_businessgpt_hardening.sh
```

This command:

1. builds the API, bot, and pinned Xray images locally;
2. uploads images and VM helpers;
3. writes mode-`0600` API and bot env files without printing secrets;
4. discovers the existing group ID and rotates the shared API key;
5. moves BusinessGPT onto three purpose-specific Docker networks;
6. runs both containers as UID/GID `10001` with read-only root filesystems;
7. removes `businessgpt-api.example.com` from Caddy;
8. restores containers, env, SQLite, and Caddy automatically on failure.

The transaction retains a consistent SQLite and Caddy snapshot under
`/opt/businessgpt-bot/hardening-backups/`. Secret-bearing pre-hardening containers
are deleted only after all checks pass.

## Routine Updates

After hardening, code-only updates stay service-specific:

```bash
scripts/deploy_businessgpt_api.sh
scripts/deploy_businessgpt_bot.sh
```

Both scripts build locally, upload an image archive, keep two rollback
containers, and verify readiness. Bot updates also create a SQLite backup and
retain the seven newest update backups.

Override the connection without editing scripts:

```bash
BUSINESSGPT_DEPLOY_TARGET=user@host \
BUSINESSGPT_SSH_KEY=~/.ssh/server-key \
scripts/deploy_businessgpt_bot.sh
```

Model uploads remain explicit and are never part of code-only deploys.

## Runtime Configuration

Production configuration is stored here:

```text
/opt/businessgpt/api.env
/opt/businessgpt-bot/bot.env
```

Both files must be mode `0600` or `0400`. Use `.env.example` only as a key
reference; its required secret and identity values are intentionally empty.

Do not export tokens in literal shell commands. For manual entry, use a hidden
prompt and write the private file with an editor or the migration helper.

Required bot policy:

- `BUSINESSGPT_BOT_OWNER_USER_IDS` identifies global infrastructure owners.
- `BUSINESSGPT_BOT_ALLOWED_CHAT_IDS` is an explicit negative-ID group allowlist.
- Telegram group owners/admins manage prompts and generation settings only for
  their own allowed group.
- Only configured owners can create, edit, or delete global model endpoints.
- Every endpoint must exactly match `BUSINESSGPT_BOT_ALLOWED_API_URLS` before
  the bearer key is attached.

## Telegram Usage

The bot answers `/gpt`, replies to the bot, mentions, and supported image
triggers. JPEG, PNG, and WebP inputs are limited to 10 MB. Generated `@`
characters are stripped to avoid username notifications.

Generation uses one model worker and a bounded FIFO queue by default. Concurrent
requests receive a temporary queue position instead of a model-busy error;
`/status` shows active and queued job counts.

Use these first:

```text
/status
/settings
/gpt test
```

`/settings` exposes per-group prompt selection and preview, model selection,
context depth (`0`, `5`, `10`, or `20`), and bounded sampling controls. Prompt
presets are scoped per group. Pending two-message editors expire after 15
minutes and can be cancelled with `/cancel`.

The default context depth is 10 recent messages. The live context cache is RAM
only, limited by chat count and TTL, and cleared on restart.

## Encrypted Feedback

Every answer gets an encrypted generation sample so a later Telegram reaction
can select it for training. Schema v2 records the exact prompt, complete request
context, generation settings, model/projector hashes, context-fit decisions,
response, and complete reaction transition history.

- Unreacted samples: 7-day TTL, 5,000-row/512 MiB caps.
- Reacted samples: retained up to 50,000 rows or 4 GiB.
- Reacted encrypted image media: retained up to 2 GiB.
- Pending encrypted image media: 24-hour TTL and 256 MiB cache cap.

The VM stores only the sealed-box public key. Keep the private key on the PC.
See `services/businessgpt-bot/README.md` for backup and export commands.

## Verification

On the VM:

```bash
docker ps --filter name=businessgpt \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker inspect businessgpt-api \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker inspect businessgpt-bot \
  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker logs --tail=100 businessgpt-api
docker logs --tail=100 businessgpt-bot
```

Expected networks:

```text
businessgpt-api:   businessgpt-backend
businessgpt-bot:   businessgpt-backend businessgpt-proxy
businessgpt-xray:  businessgpt-egress businessgpt-proxy
```

No BusinessGPT container should be attached to `edge` or publish a host port.

## Security Rules

- Public security group: TCP `80` and `443` only; restrict SSH `22` by source.
- Do not publish application ports `8000`, `8080`, `5984`, or `1080`.
- Keep API keys, bot tokens, feedback private keys, VLESS credentials, databases,
  model files, archives, and generated samples out of Git.
- Use one uvicorn worker; API admission is tied to llama.cpp `N_PARALLEL`.
- Do not restore the public BusinessGPT Caddy route for convenience. Diagnose
  through `docker exec` or the bot network instead.
