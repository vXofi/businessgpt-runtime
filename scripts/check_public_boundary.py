#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import re
import subprocess
from pathlib import Path


FORBIDDEN_PATH_PATTERNS = (
    "*.age",
    "*.crt",
    "*.db",
    "*.db-*",
    "*.enc",
    "*.gguf",
    "*.jsonl",
    "*.key",
    "*.log",
    "*.pem",
    "*.prompt",
    "*.safetensors",
    "*.sqlite",
    "*.sqlite-*",
    "*.sqlite3",
    "*.sqlite3-*",
    "*.tar",
    "*.tar.gz",
)
FORBIDDEN_PATH_PARTS = {
    ".artifacts",
    ".cache",
    "backups",
    "feedback",
    "media-cache",
    "secrets",
    "tokens",
}
FORBIDDEN_LITERALS = (
    "/Users/" + "xofi",
    "/home/" + "xofi",
    "~/ssh/" + "yavm",
    "xofi" + "@",
    "@gmail" + ".com",
    "huge" + "balls",
    "obsi" + "dian",
    "couch" + "db",
    "ezbook" + "keeping",
)
SECRET_ENV_KEYS = {
    "BUSINESSGPT_API_KEY",
    "BUSINESSGPT_BOT_TOKEN",
    "BUSINESSGPT_BOT_FEEDBACK_PRIVATE_KEY_B64",
    "BUSINESSGPT_VLESS_URL",
}
IDENTITY_ENV_KEYS = {
    "BUSINESSGPT_BOT_ADMIN_USER_IDS",
    "BUSINESSGPT_BOT_OWNER_USER_IDS",
    "BUSINESSGPT_BOT_ALLOWED_CHAT_IDS",
}
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
ENV_ASSIGNMENT_RE = re.compile(
    r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$",
    flags=re.MULTILINE,
)
VLESS_CREDENTIAL_RE = re.compile(r"vless://[^\s/@]+@")
SAFE_DEFAULT_PROMPT = "Configure the system prompt at deployment time."
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)


def tracked_paths(root: Path, commit: str | None = None) -> list[str]:
    command = ["git", "ls-files", "-z"]
    if commit is not None:
        command = ["git", "ls-tree", "-r", "--name-only", "-z", commit]
    raw = subprocess.check_output(command, cwd=root)
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def blob_at(root: Path, path: str, commit: str | None = None) -> bytes:
    if commit is None:
        return (root / path).read_bytes()
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=root)


def path_findings(path: str) -> list[str]:
    findings: list[str] = []
    candidate = Path(path)
    if candidate.name.startswith(".env") and candidate.name != ".env.example":
        findings.append("tracked private environment file")
    if any(part in FORBIDDEN_PATH_PARTS for part in candidate.parts):
        findings.append("tracked private/runtime directory")
    if any(fnmatch.fnmatch(candidate.name, pattern) for pattern in FORBIDDEN_PATH_PATTERNS):
        findings.append("tracked private/runtime artifact")
    if "prompts/private" in candidate.as_posix():
        findings.append("tracked private prompt")
    return findings


def allowed_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.is_loopback
        or address.is_unspecified
        or any(address in network for network in DOCUMENTATION_NETWORKS)
    )


def content_findings(path: str, data: bytes) -> list[str]:
    if b"\0" in data:
        return []
    text = data.decode("utf-8", errors="replace")
    lowered = text.lower()
    findings: list[str] = []

    for literal in FORBIDDEN_LITERALS:
        if literal.lower() in lowered:
            findings.append(f"forbidden public value: {literal}")

    for match in IPV4_RE.finditer(text):
        value = match.group(0)
        if not allowed_ip(value):
            findings.append(f"non-documentation IPv4 address: {value}")

    for key, raw_value in ENV_ASSIGNMENT_RE.findall(text):
        value = raw_value.strip().strip('"').strip("'")
        placeholder = (
            not value
            or (value.startswith("<") and value.endswith(">"))
            or value.startswith("${")
        )
        if key in SECRET_ENV_KEYS and not placeholder:
            findings.append(f"populated secret setting: {key}")
        if key in IDENTITY_ENV_KEYS and not placeholder:
            findings.append(f"populated identity setting: {key}")

    if "BUSINESSGPT_DEPLOY_" + "TARGET:-" in text:
        findings.append("compiled deployment target fallback")
    if "DEFAULT_SYSTEM_PROMPT" in text and SAFE_DEFAULT_PROMPT not in text:
        findings.append("literal default system prompt")
    if VLESS_CREDENTIAL_RE.search(text) and not path.endswith(
        "tests/test_vless_config.py"
    ):
        findings.append("embedded VLESS credential link")
    return sorted(set(findings))


def scan_tree(root: Path, commit: str | None = None) -> list[str]:
    findings: list[str] = []
    for path in tracked_paths(root, commit):
        prefix = f"{commit}:" if commit else ""
        for finding in path_findings(path):
            findings.append(f"{prefix}{path}: {finding}")
        try:
            data = blob_at(root, path, commit)
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        for finding in content_findings(path, data):
            findings.append(f"{prefix}{path}: {finding}")
    return findings


def history_findings(root: Path) -> list[str]:
    commits = subprocess.check_output(
        ["git", "rev-list", "--all"], cwd=root, text=True
    ).splitlines()
    findings: list[str] = []
    for commit in commits:
        findings.extend(scan_tree(root, commit))

    emails = subprocess.check_output(
        ["git", "log", "--all", "--format=%ae%n%ce"], cwd=root, text=True
    ).splitlines()
    for email in sorted(set(emails)):
        if email.lower().endswith("@gmail" + ".com"):
            findings.append("commit metadata: personal Gmail address")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject private deployment data and artifacts from public Git."
    )
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    findings = history_findings(root) if args.history else scan_tree(root)
    for finding in findings:
        print(finding)
    if findings:
        print(f"public-boundary check failed: {len(findings)} finding(s)")
        return 1
    print("public-boundary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
