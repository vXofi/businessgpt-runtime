#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


SUPPORTED_MODES = {"auto", "packet-up", "stream-one", "stream-up"}


def _one(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name)
    return values[0].strip() if values else default


def parse_vless_link(link: str) -> dict:
    parsed = urlsplit(link.strip())
    if parsed.scheme.lower() != "vless":
        raise ValueError("link must start with vless://")
    if not parsed.username or not parsed.hostname or parsed.port is None:
        raise ValueError("VLESS link must contain UUID, server, and port")

    try:
        user_id = str(uuid.UUID(unquote(parsed.username)))
    except ValueError as exc:
        raise ValueError("VLESS link contains an invalid UUID") from exc

    query = parse_qs(parsed.query, keep_blank_values=True)
    transport = _one(query, "type").lower()
    security = _one(query, "security").lower()
    if transport != "xhttp" or security != "tls":
        raise ValueError("this helper supports VLESS XHTTP with TLS only")

    encryption = _one(query, "encryption", "none")
    if encryption != "none":
        raise ValueError("expected encryption=none for VLESS over TLS")

    mode = _one(query, "mode", "auto")
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported XHTTP mode: {mode}")

    path = unquote(_one(query, "path", "/")) or "/"
    if not path.startswith("/"):
        path = f"/{path}"

    alpn = [value.strip() for value in _one(query, "alpn", "h2,http/1.1").split(",")]
    alpn = [value for value in alpn if value]
    server_name = _one(query, "sni", parsed.hostname)
    xhttp_host = _one(query, "host")

    xhttp_settings: dict[str, object] = {"path": path, "mode": mode}
    if xhttp_host:
        xhttp_settings["host"] = xhttp_host

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "0.0.0.0",
                "port": 1080,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }
        ],
        "outbounds": [
            {
                "tag": "vless-out",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": parsed.hostname,
                            "port": parsed.port,
                            "users": [
                                {
                                    "id": user_id,
                                    "encryption": encryption,
                                    "flow": _one(query, "flow"),
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "tls",
                    "tlsSettings": {
                        "serverName": server_name,
                        "allowInsecure": False,
                        "alpn": alpn,
                        "fingerprint": _one(query, "fp", "chrome"),
                    },
                    "xhttpSettings": xhttp_settings,
                },
            }
        ],
    }


def write_private_json(output: Path, config: dict, *, force: bool) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        raise FileExistsError(f"refusing to overwrite {output}; pass --force to replace it")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(config, file, ensure_ascii=True, indent=2)
            file.write("\n")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a VLESS XHTTP/TLS share link into a private Xray client config."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    link = getpass.getpass("VLESS link: ")
    config = parse_vless_link(link)
    write_private_json(args.output, config, force=args.force)
    print(f"wrote private Xray config: {args.output}")
    print("local SOCKS endpoint: socks5://businessgpt-xray:1080")


if __name__ == "__main__":
    main()
