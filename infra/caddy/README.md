# Caddy Boundary

BusinessGPT does not require a public Caddy route. The Telegram bot reaches the
API through an internal Docker network, and the API publishes no host port.

`Caddyfile.example` demonstrates only the syntax for another service sharing
the host. All example hostnames use the reserved `.example` domain.

The legacy hardening helper can remove a former BusinessGPT route when both
`BUSINESSGPT_CADDYFILE` and `BUSINESSGPT_LEGACY_CADDY_HOSTNAME` are
explicitly configured. It validates and reloads the container selected through
`BUSINESSGPT_CADDY_CONTAINER`; no production path, container name, or
hostname is compiled into the public repository.
