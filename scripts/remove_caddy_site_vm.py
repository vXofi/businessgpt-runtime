#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def remove_site_block(text: str, hostname: str) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    removed = False
    while index < len(lines):
        line = lines[index]
        if line.strip() != f"{hostname} {{":
            output.append(line)
            index += 1
            continue
        if removed:
            raise ValueError(f"multiple Caddy blocks found for {hostname}")
        removed = True
        depth = line.count("{") - line.count("}")
        index += 1
        while index < len(lines) and depth > 0:
            depth += lines[index].count("{") - lines[index].count("}")
            index += 1
        if depth != 0:
            raise ValueError(f"unbalanced Caddy block for {hostname}")
        while output and not output[-1].strip():
            output.pop()
        output.append("\n")
    if not removed:
        return text
    return "".join(output).lstrip("\n")


def write_in_place(path: Path, text: str) -> None:
    # Replacing a bind-mounted file creates a new host inode while a running
    # container keeps the old one. Truncate the existing inode instead.
    with path.open("w", encoding="utf-8") as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())


def container_config(container: str) -> str:
    return subprocess.check_output(
        ["docker", "exec", container, "cat", "/etc/caddy/Caddyfile"],
        text=True,
    )


def write_container_config(container: str, text: str) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "sh",
            "-c",
            "cat > /etc/caddy/Caddyfile",
        ],
        input=text,
        text=True,
        check=True,
    )


def validate_text(container: str, text: str) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "caddy",
            "validate",
            "--config",
            "-",
            "--adapter",
            "caddyfile",
        ],
        input=text,
        text=True,
        check=True,
    )


def synchronize_container_config(container: str, text: str) -> None:
    if container_config(container) == text:
        return
    try:
        write_container_config(container, text)
    except subprocess.CalledProcessError:
        # A read-only bind mount cannot be rewritten after an earlier atomic
        # host replacement detached its inode. Validate the host copy first,
        # then restart once so Docker remounts the current source path.
        validate_text(container, text)
        subprocess.run(["docker", "restart", container], check=True)
        if container_config(container) != text:
            raise RuntimeError("Caddy container did not remount the host Caddyfile")


def validate_and_reload(container: str) -> None:
    for command in ("validate", "reload"):
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "caddy",
                command,
                "--config",
                "/etc/caddy/Caddyfile",
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely remove one Caddy site block.")
    parser.add_argument("--caddyfile", required=True, type=Path)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--container", default="caddy")
    args = parser.parse_args()

    original = args.caddyfile.read_text(encoding="utf-8")
    updated = remove_site_block(original, args.hostname)
    mounted_original = container_config(args.container)
    backup: Path | None = None
    if updated != original:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup = args.caddyfile.with_name(f"{args.caddyfile.name}.backup.{stamp}")
        shutil.copy2(args.caddyfile, backup)
        write_in_place(args.caddyfile, updated)

    try:
        synchronize_container_config(args.container, updated)
        validate_and_reload(args.container)
    except BaseException:
        if updated != original:
            write_in_place(args.caddyfile, original)
        try:
            synchronize_container_config(args.container, mounted_original)
            validate_and_reload(args.container)
        except BaseException:
            pass
        raise

    if updated == original:
        print(f"Caddy block already absent: {args.hostname}")
        print("running Caddy configuration synchronized")
    else:
        print(f"removed Caddy block: {args.hostname}")
        print(f"Caddy backup: {backup}")
    backups = sorted(
        args.caddyfile.parent.glob(f"{args.caddyfile.name}.backup.*"), reverse=True
    )
    for expired in backups[7:]:
        expired.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
