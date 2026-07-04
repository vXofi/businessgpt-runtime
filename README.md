# BusinessGPT Runtime

Infrastructure and deploy repo for services hosted on the deployment VM.

This repo is intentionally separate from the BusinessGPT training repo. It owns
runtime services, routing, Docker images, and operational notes. It must not
contain datasets, model artifacts, generations, notebooks, API keys, bot tokens,
or Hugging Face tokens.

## Current Services

| Domain | Service | Container | Public port |
| --- | --- | --- | --- |
| `businessgpt-api.example.com` | BusinessGPT inference API | `businessgpt-api` | via Caddy `443` |
| `notes.example.com` | service A service-a | `service-a` | via Caddy `443` |

Public traffic should enter through one Caddy container on ports `80` and `443`.
Application containers should stay on a private Docker network.

## Target Architecture

```text
internet
  |
  | 80/443
  v
caddy edge proxy
  |-- businessgpt-api.example.com      -> businessgpt-api:8000
  |-- notes.example.com -> service-a:5984
  |-- service1.example.com -> service1:<private-port>
  |-- service2.example.com -> service2:<private-port>
```

Do not expose service ports publicly unless this is a short debug session.

## Repo Layout

```text
services/businessgpt-api/  FastAPI wrapper around llama-server
infra/caddy/               Caddy examples and routing notes
docs/                      New-chat context and operational notes
scripts/                   Local build and VM deploy helpers
examples/                  Client snippets for integrations
```

## SSH Usage

Connect directly to the VM:

```bash
ssh -i ~/.ssh/<key-file> deploy@192.0.2.10
```

Or add the example host from `scripts/ssh_config.example` to your SSH config,
then use the short alias:

```bash
ssh businessgpt
```

Run one command remotely without opening an interactive shell:

```bash
ssh businessgpt 'docker ps'
ssh businessgpt 'cd /opt/businessgpt && docker logs --tail=100 businessgpt-api'
```

Copy files to the VM:

```bash
scp -i ~/.ssh/<key-file> businessgpt-api.tar.gz deploy@192.0.2.10:/opt/businessgpt/
scp businessgpt-api.tar.gz businessgpt:/opt/businessgpt/
```

## First Setup On VM

Create a shared private network if it does not exist:

```bash
docker network create edge
```

Connect existing containers:

```bash
docker network connect edge caddy
docker network connect edge businessgpt-api
```

If a container is already connected, Docker will print an error. That is not
dangerous.

## BusinessGPT API

Build locally for the deployment VM:

```bash
scripts/build_businessgpt_api_amd64.sh
```

Copy the resulting image archive to the VM:

```bash
scp -i ~/.ssh/<key-file> businessgpt-api.tar.gz deploy@192.0.2.10:/opt/businessgpt/
```

Load and run on the VM:

```bash
cd /opt/businessgpt
gunzip -c businessgpt-api.tar.gz | docker load
scripts/run_businessgpt_api_vm.sh
```

## Caddy

The current production Caddy config is managed by the existing Service A Caddy
container. Update that Caddyfile rather than running a second public Caddy.

Expected path on VM:

```text
/etc/caddy/Caddyfile
```

Reload:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## Security Rules

- Public Yandex security group: allow TCP `80` and `443`.
- Restrict SSH `22` to your own IP.
- Close public `8000`.
- Use `BUSINESSGPT_API_KEY` for `/generate`.
- Keep secrets in VM env files or shell history-conscious commands, not in git.
