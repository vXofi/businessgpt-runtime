# Architecture

## Runtime Topology

```text
Telegram
   |
   v
businessgpt-bot
   |  chat/role checks, settings, context, queue, encrypted feedback
   +-------------------------> businessgpt-xray -> private proxy egress
   |
   v
businessgpt-api
   |  auth, admission, context fitting, provenance
   v
pinned native llama.cpp + verified GGUF/projector
```

The API has no Caddy route or published host port. The bot, API, and optional
Xray egress run as non-root containers with read-only root filesystems, dropped
capabilities, bounded resources, and purpose-specific Docker networks.

## Request Lifecycle

1. The bot rejects non-allowlisted chats before caching or processing.
2. Per-chat and per-user limits are checked before entering the bounded queue.
3. The API authenticates and acquires an inference slot before reading a body.
4. Schema, aggregate text, sampling, and media bounds are validated.
5. Context fitting reserves output, safety, and image tokens.
6. The request reaches a pinned native `llama-server` process.
7. The response records artifact hashes and context-fit decisions.
8. The bot stores a sealed feedback record and later attaches reaction state.

## Network Boundary

```text
businessgpt-backend: bot <-> API
businessgpt-proxy:   bot <-> Xray
businessgpt-egress:  Xray -> external proxy
```

The API network is internal. Caddy and unrelated containers do not join these
networks. The bot sends a bearer key only to an exact allowlisted API URL.

## Model Lifecycle Boundary

Training, temporal holdout construction, and aggregate blinded evaluation live
in the public training repository. Runtime consumes an exported GGUF whose
filename and SHA-256 are pinned in a manifest. Startup fails closed on an
unknown or modified artifact.

The deployed baseline is v16 SFT. A reward model has been trained but is not
part of the production inference path. Best-of-N and Q4/Q5 benchmark work are
outside this committed baseline.
