# Engineering Evidence

This table maps the main system-design claims to reviewable code and tests.

| Claim | Primary paths |
| --- | --- |
| Auth before body parsing and bounded admission | `services/businessgpt-api/api_security.py`, API security tests |
| Context-window fitting | `services/businessgpt-api/context_window.py`, context tests |
| Image normalization and bounds | `services/businessgpt-api/media.py`, media tests |
| Model artifact verification | `services/businessgpt-api/model_verification.py`, manifest tests |
| Chat and endpoint authorization | bot config/validation modules and tests |
| Bounded FIFO queue and rate limits | bot limits module and queue/limits tests |
| Encrypted feedback and retention | bot DB, crypto, media cache, and export tests |
| Container/network hardening | Dockerfiles and generic run/update scripts |
| Caddy route removal and rollback | Caddy helper and deployment-helper tests |

The test suites validate code behavior. They do not reproduce private model
quality evaluation, production uptime, latency, or capacity. This repository
does not claim an SLO, automated model-process recovery, a completed
quantization comparison, or production use of the trained reward model.
