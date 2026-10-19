"""Content-free, ephemeral readiness and a per-data-directory polling lock."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
from pathlib import Path
import time


class Readiness:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self.initialized = False
        self.last_poll = 0.0

    def poll(self, success: bool) -> None:
        self.last_poll = time.time() if success else 0.0

    def write(self, running: bool) -> None:
        if self.path is None:
            return
        payload = {"initialized": self.initialized, "running": running,
                   "last_poll": self.last_poll, "updated_at": time.time()}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    async def monitor(self, application) -> None:
        while True:
            self.write(bool(application.running and application.updater.running))
            await asyncio.sleep(5)


def healthy(path: Path, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    try:
        data = json.loads(path.read_text())
        return (data["initialized"] is True and data["running"] is True
                and 0 <= now - data["updated_at"] < 20
                and 0 <= now - data["last_poll"] < 90)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def polling_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a")
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError("Another polling process owns this data directory") from None
    return stream


if __name__ == "__main__":
    raise SystemExit(0 if healthy(Path(os.environ.get(
        "BUSINESSGPT_READINESS_FILE", "/tmp/businessgpt-ready.json"))) else 1)
