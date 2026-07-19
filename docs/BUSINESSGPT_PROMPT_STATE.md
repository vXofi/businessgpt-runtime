# BusinessGPT Prompt And Feedback State

`businessgpt-api` stays generation-focused. Telegram-specific state belongs to
`businessgpt-bot` and persists in SQLite.

```text
Telegram group
  -> businessgpt-bot
       group-scoped prompts and settings
       global owner-managed endpoint profiles
       bounded RAM context
       encrypted feedback/provenance
  -> businessgpt-api
       authenticated generation
       context-window fitting
       model provenance
  -> llama-server
```

## API Contract

`POST /generate` accepts an optional authenticated `system_prompt` override.
Without it, the API uses its compiled/env default. `role=system` inside
`messages` is ignored. Generation returns `usage.prompt_hash` and provenance,
not prompt text. Authenticated `GET /prompt` exists for trusted bot preview and
exact prompt capture.

## Authorization Boundaries

- Only chats in `BUSINESSGPT_BOT_ALLOWED_CHAT_IDS` reach handlers.
- Telegram group owners/admins manage prompt presets and generation settings
  for that allowed group.
- `BUSINESSGPT_BOT_OWNER_USER_IDS` can recover any allowed group and is the only
  role that mutates global model endpoint profiles.
- Model endpoints must exactly match `BUSINESSGPT_BOT_ALLOWED_API_URLS` before
  the API key is sent.
- Private editor sessions and every inline callback are bound to an explicit
  target group and re-check live group administration.

## SQLite Ownership

Current implementation: `services/businessgpt-bot/bot/db.py`.

Important tables:

| Table | Ownership and purpose |
| --- | --- |
| `prompt_presets_v2` | Prompt text keyed by `(chat_id, name)`, soft delete |
| `model_profiles` | Global allowlisted endpoint and bounded defaults, soft delete |
| `chat_settings` | Active prompt/model plus per-group generation overrides |
| `pending_admin_actions` | Target-bound two-message editor state with TTL |
| `admin_sessions` | Private editor target with live membership rechecks |
| `outbound_messages` | Telegram answer IDs eligible for reactions |
| `encrypted_feedback_samples` | Sealed schema-v1/v2 generation records |
| `reaction_events` | Every old/new reaction transition |
| `pending_feedback_media` | Paths to short-lived sealed image files |
| `encrypted_feedback_media` | Reaction-selected sealed image bytes |
| `schema_metadata` | One-time migration markers |

The legacy global `prompt_presets` columns remain during compatibility
migration. Startup copies active legacy prompts into `prompt_presets_v2` for
their group without deleting v1 rows, so old exports and foreign keys remain
readable.

Prompt and model soft deletion supports recovery from administrative mistakes.
The active prompt is always resolved inside its group; IDs from another group
are rejected.

## Context Model

Ordinary chat bodies are not persisted as a general message archive. The bot
keeps a bounded in-process cache:

```text
20 messages/chat
64 chats
6-hour idle TTL
10 messages included by default
```

The cache clears on restart. When generation occurs, the exact selected context
is copied into that answer's encrypted feedback payload. Consequently, a
reaction-selected sample remains self-contained for future training without
turning SQLite into an unlimited plaintext group-message log.

## Feedback Schema V2

Each encrypted payload contains:

```text
feedback_schema_version
trigger message/user metadata
complete API request messages
system prompt name/text/hash
model profile name and API URL
effective generation settings
model and projector SHA-256
llama.cpp revision
context-fit version and retained source indexes
token/truncation metadata
complete API response
final Telegram response text
```

Image bytes are not embedded in this JSON. The request record contains image
hash provenance, while sealed image bytes live in the pending/promoted media
tables.

The first non-empty reaction marks the encrypted sample as selected for
long-term retention. Every later add/change/remove event is still appended to
`reaction_events`; the exporter returns both `reaction_history` and
`final_reaction_state`.

This is labeled feedback, not automatically pairwise RLHF. Building a reward
model or DPO dataset still requires an explicit transformation policy. Exact
request and runtime provenance is retained so that transformation can be
reproducible later.

## Retention

```text
unreacted samples: 7-day TTL, 5,000 rows, 512 MiB
reacted samples:   50,000 rows, 4 GiB
pending media:     24-hour TTL, 256 MiB
reacted media:     2 GiB
```

Hourly maintenance removes expired/orphaned rows and files, enforces newest-
first caps, removes metadata no longer referenced by samples/media, checkpoints
the WAL, performs incremental vacuuming, and warns on low disk space.

## Telegram UX

Primary interface:

```text
/settings
```

The settings view displays active prompt/model names and bounded generation
values. Pressing the prompt button previews actual text. Group views provide an
encoded private-editor link for multi-message prompt operations.

Prompt commands:

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

Model commands:

```text
/models
/model_show [name]
/model_use <name>
/model_new <name> <api_url> [max] [temp] [top_p] [top_k] [repeat]
/model_edit <name> <api_url> [max] [temp] [top_p] [top_k] [repeat]
/model_delete <name>
```

Only owners can run the mutation commands for models. Pending editors expire
after 15 minutes; `/cancel` clears one immediately.

## Design Constraints

Do not move prompt editing into environment variables, Docker rebuilds, or
mounted global prompt files. Those approaches cannot represent independent
group presets, atomic edits, soft deletion, testing, or reaction provenance.

Do not persist general plaintext chat history for convenience. Context needed
for selected training examples is already captured inside sealed per-generation
payloads.
