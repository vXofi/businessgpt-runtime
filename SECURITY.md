# Security And Privacy Boundary

## Public Controls

- bearer authentication before request-body parsing;
- fail-closed startup when required credentials or prompt configuration are absent;
- explicit Telegram chat and API endpoint allowlists;
- bounded request bodies, schemas, sampling values, media, and queue capacity;
- model/projector hash verification;
- non-root and read-only containers with isolated networks;
- encrypted feedback payloads and media with bounded retention;
- readiness-gated update and rollback helpers.

## Never Commit

- API keys, bot tokens, private encryption keys, or proxy credentials;
- real user/chat IDs, endpoints, hostnames, IP addresses, or SSH identities;
- exact production prompts or persona text;
- chat exports, requests, responses, ratings, databases, or feedback media;
- model binaries, adapters, image archives, deployment archives, or backups;
- host inventory or details of unrelated services.

Raw chat-derived data remains private. This project does not claim formal
differential privacy or elimination of model memorization risk.

Report a vulnerability through GitHub private vulnerability reporting when
available. Do not include credentials or private data in a public issue.
