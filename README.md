# BusinessGPT Runtime

Runtime and operations repository for
[`vXofi/businessgpt`](https://github.com/vXofi/businessgpt). The training
repository contains model adaptation and aggregate evaluation; this repository
contains serving, the Telegram product surface, feedback capture, and generic
deployment tooling.

```text
Telegram
   -> allowlisted bot, bounded context, rate limits, FIFO queue
   -> private FastAPI service or allowlisted Runpod queue endpoint
   -> pinned llama.cpp + hash-verified GGUF/projector
   -> encrypted reaction feedback with request/model provenance
```

The inference API has no public route. Exact prompts, credentials, Telegram
identities, proxy links, runtime databases, feedback records, model files, and
host addresses are deployment configuration and are not stored in Git.

## Engineering Scope

- bearer authentication and inference admission before request-body parsing;
- bounded body, schema, media, generation, and context-window handling;
- model/projector manifest verification at startup;
- allowlisted chats, role-scoped settings, and exact endpoint allowlisting;
- bounded FIFO generation queue and per-user/per-chat request limits;
- encrypted reaction samples with bounded retention;
- non-root, read-only containers on isolated Docker networks;
- text, image, sticker, and video/animation preview inputs;
- optional Runpod transport with bounded waiting and cancellation;
- optional image generation, server-side disable policy, and spoiler delivery;
- polling/update tracing and structured startup readiness;
- generic Caddy migration and Xray egress tooling.

See [Architecture](docs/ARCHITECTURE.md),
[Runtime Updates](docs/RUNTIME_UPDATES.md),
[Engineering Evidence](docs/EVIDENCE.md), and [Security](SECURITY.md).

## Repository Layout

```text
services/businessgpt-api/   inference API, context fitting, provenance
services/businessgpt-bot/   Telegram UX, queue, settings, feedback
services/businessgpt-xray/  pinned private egress container
infra/caddy/                generic reverse-proxy example
scripts/                    builds, updates, migration, verification
```

## Local Verification

```bash
PYTHONPATH=services/businessgpt-api \
  python -m unittest discover -s services/businessgpt-api/tests -v
PYTHONPATH=services/businessgpt-bot \
  python -m unittest discover -s services/businessgpt-bot/tests -v
python -m unittest discover -s scripts/tests -v
python scripts/check_public_boundary.py
```

GitHub Actions runs the same suites, a full-history Gitleaks scan, and the
repository-specific public-boundary policy.

## Runtime Configuration

Copy `.env.example` only as a key reference. Production uses private,
mode-`0600` env files. At minimum, configure:

```text
BUSINESSGPT_API_KEY
BUSINESSGPT_SYSTEM_PROMPT
BUSINESSGPT_BOT_TOKEN
BUSINESSGPT_BOT_OWNER_USER_IDS
BUSINESSGPT_BOT_ALLOWED_CHAT_IDS
```

The API fails closed without its key or configured system prompt. The bot fails
closed without its token, owner IDs, allowed group IDs, exact API URL allowlist,
and the key required by the selected transport. Runpod-only profiles use a
separate `BUSINESSGPT_RUNPOD_API_KEY` instead of the direct API key.

Deployment entry points require an explicit target:

```bash
BUSINESSGPT_DEPLOY_TARGET=user@host \
BUSINESSGPT_SSH_KEY=~/.ssh/businessgpt_ed25519 \
scripts/deploy_businessgpt_api.sh
```

Example addresses use `.example` domains and RFC documentation IP ranges.
They are not usable production defaults.

## Model Boundary

The deployed model described by this baseline is the v16 SFT artifact. A
RuBERT reward model has been trained, but it is not integrated into the
production inference path. The runtime includes Q4/Q5 manifest verification,
GPU benchmark tooling and an optional Runpod worker. No quality improvement or
GPU availability is implied by those capabilities. Best-of-N is not integrated.

The model manifest pins the expected language model, vision projector,
llama.cpp revision, filenames, sizes, and SHA-256 values. Model binaries are
published separately or remain local and are never committed here.

## Contribution Policy

Install the repository hooks before creating commits:

```bash
brew install gitleaks pre-commit
pre-commit install --hook-type pre-commit --hook-type pre-push
```

All changes enter `main` through pull requests with tests, Gitleaks, and
public-boundary checks passing. See [CONTRIBUTING.md](CONTRIBUTING.md).
