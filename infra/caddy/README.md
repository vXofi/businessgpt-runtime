# Caddy

Use one Caddy container as the public edge proxy for the whole VM.

Current production Caddy appears to be the Service A Caddy container:

```text
caddy
```

Do not run a separate `businessgpt-caddy` on ports `80`/`443`; it will conflict
with the existing Caddy.

## Production Config Path

The current Caddyfile on the VM is expected at:

```text
/etc/caddy/Caddyfile
```

After editing it, reload:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## Private Docker Networking

Caddy must share a Docker network with the services it proxies:

```bash
docker network create edge
docker network connect edge caddy
docker network connect edge businessgpt-api
docker network connect edge service-a
```

Then Caddy can route to container names:

```caddyfile
businessgpt-api.example.com {
    reverse_proxy businessgpt-api:8000
}
```

## Public Ports

Only these should be public:

```text
80/tcp
443/tcp
```

Close public `8000/tcp` after local routing works.

