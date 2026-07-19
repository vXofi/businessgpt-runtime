# Caddy

Use the existing `caddy` container as the VM's only public reverse
proxy. It publishes ports `80` and `443` and joins Docker network `edge` with
web applications that need a public hostname.

Production config path:

```text
/etc/caddy/Caddyfile
```

Validate and reload after editing:

```bash
docker exec caddy caddy validate --config /etc/caddy/Caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

The Caddyfile is bind-mounted read-only. Edit its existing inode in place; an
atomic rename can leave a running container attached to the old inode. If an
editor replaces the file, restart `caddy` once before validating so
Docker remounts the current host path. The BusinessGPT hardening helper handles
both cases and keeps at most seven timestamped Caddy backups.

Example public route:

```caddyfile
service-b.example.com {
    reverse_proxy service-b:8080
}
```

Caddy and the target container must both join `edge`; the application port must
not be published on the host.

BusinessGPT is intentionally different. `businessgpt-api`, `businessgpt-bot`,
and `businessgpt-xray` use isolated service networks and must not join `edge`.
There is no `businessgpt-api.example.com` Caddy block in the hardened architecture.
