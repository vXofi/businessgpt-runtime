# BusinessGPT Prompt State

Keep `businessgpt-api` mostly stateless. It should run generation, enforce API
auth, accept an optional trusted `system_prompt` override, and return
`usage.prompt_hash`.

Prompt presets, active prompt selection, model profile selection, admin UX, and
reaction feedback should live in the Telegram bot or another caller.

## Target Flow

```text
Telegram chat
  |
  v
businessgpt-bot
  - admin commands
  - prompt presets
  - model profiles
  - active prompt per chat
  - active model per chat
  - encrypted reaction samples
  - SQLite storage
  |
  v
businessgpt-api
  - generation only
  - accepts trusted system_prompt override
  - ignores role=system inside messages
  |
  v
llama-server
```

## API Contract

`POST /generate` accepts:

```json
{
  "messages": [
    {"role": "user", "name": "example-user", "content": "text"}
  ],
  "system_prompt": "Ты BusinessGPT...",
  "max_tokens": 256,
  "temperature": 0.9
}
```

Rules:

- If `system_prompt` is provided by the authenticated caller, use it.
- Otherwise use the API default prompt.
- Ignore `role=system` inside `messages`.
- Return `usage.prompt_hash`, not the prompt text.
- Expose the live default text only through authenticated `GET /prompt` for
  trusted administration clients; normal generation responses still return
  only the hash.

## Implemented Bot Storage

SQLite is enough. Current implementation lives in
`services/businessgpt-bot/bot/db.py`.

```sql
create table prompt_presets (
    id integer primary key,
    name text not null unique,
    text text not null,
    created_by integer,
    created_at text not null,
    updated_at text not null,
    deleted_at text
);

create table chat_settings (
    chat_id integer primary key,
    active_prompt_id integer references prompt_presets(id),
    active_model_profile_id integer references model_profiles(id)
);

create table model_profiles (
    id integer primary key,
    name text not null unique,
    api_url text not null,
    model_label text not null,
    max_tokens integer not null,
    temperature real not null,
    top_p real not null,
    top_k integer not null,
    repetition_penalty real not null,
    created_at text not null,
    updated_at text not null,
    deleted_at text
);

create table outbound_messages (
    id integer primary key,
    chat_id integer not null,
    telegram_message_id integer not null,
    prompt_preset_id integer references prompt_presets(id),
    model_profile_id integer references model_profiles(id),
    prompt_hash text,
    created_at text not null,
    unique(chat_id, telegram_message_id)
);

create table reaction_events (
    id integer primary key,
    chat_id integer not null,
    telegram_message_id integer not null,
    user_id integer,
    old_reaction_json text not null,
    new_reaction_json text not null,
    created_at text not null
);

create table encrypted_feedback_samples (
    id integer primary key,
    chat_id integer not null,
    telegram_message_id integer not null,
    reaction_event_id integer references reaction_events(id),
    payload_json_sha256 text not null,
    encrypted_payload_b64 text not null,
    created_at text not null
);
```

`chat_settings` also contains per-chat generation overrides and
`recent_context_messages`. Recent message bodies are deliberately absent from
SQLite: the bot keeps a bounded 20-message process-local RAM buffer.

Image feedback uses `pending_feedback_media` for paths to short-lived sealed
ciphertext and `encrypted_feedback_media` for reaction-selected ciphertext.
Plain image bytes are never written on the VM.

Use soft-delete for prompt presets and model profiles so mistakes can be
recovered.

Generation samples are encrypted before they are stored. The VM needs only the
PC public key. Keep the private key off the VM.

## Telegram Commands

Admin-only:

```text
/prompts
/prompt_show
/prompt_use <name>
/prompt_new <name>
/prompt_edit <name>
/prompt_delete <name>
/prompt_reset
/prompt_test <name> <message>
```

Authorization is scoped to Telegram: a group owner or administrator can manage
that group's settings, and configured `BUSINESSGPT_BOT_ADMIN_USER_IDS` are
global recovery administrators. Private editor sessions retain the target
group for 24 hours but re-check live group administration on every action.
Telegram command menus are scoped similarly: ordinary group members see
`/gpt`, while group administrators and private chats receive the management
commands.

Admin-only model commands:

```text
/models
/model_show <name>
/model_use <name>
/model_new <name> <api_url> [max_tokens] [temperature] [top_p] [top_k] [repetition_penalty]
/model_edit <name> <api_url> [max_tokens] [temperature] [top_p] [top_k] [repetition_penalty]
/model_delete <name>
```

Other admin commands:

```text
/status
/settings
/tools
```

For long prompt text, use a two-step flow:

```text
/prompt_new drunk_short
<next message is saved as prompt text>
```

or:

```text
/prompt_edit drunk_short
<next message replaces prompt text>
```

## Avoid

Do not make prompt editing modify env vars, Docker config, or mounted files as
the main path. That only works for one global prompt and becomes awkward for
per-chat presets, history, testing, delete/recover, and Telegram UX.

Do not put the Telegram bot behind Caddy. Long polling keeps it private and
avoids another public route.
