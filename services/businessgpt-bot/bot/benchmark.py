from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .businessgpt_client import BusinessGPTClient
from .limits import GenerationQueue


def _sampling(item: dict[str, Any]) -> dict[str, Any]:
    values = {
        "max_tokens": 256,
        "temperature": 0.9,
        "top_p": 0.9,
        "top_k": 50,
        "repetition_penalty": 1.1,
        "seed": None,
    }
    values.update(item.get("sampling") or {})
    return values


async def run_queue_benchmark(
    *,
    client: Any,
    api_url: str,
    requests: list[dict[str, Any]],
    burst_width: int,
) -> list[dict[str, Any]]:
    if burst_width < 1:
        raise ValueError("burst_width must be positive")
    queue = GenerationQueue(
        max_concurrent=1,
        max_queue_size=max(1, len(requests)),
        user_requests=max(1, len(requests) + 1),
        chat_requests=max(1, len(requests) + 1),
        window_seconds=1,
    )
    queue.start()
    records: list[dict[str, Any]] = []

    async def submit(item: dict[str, Any], index: int) -> None:
        record: dict[str, Any] = {
            "id": item.get("id", f"request-{index:04d}"),
            "kind": item.get("kind", "text"),
            "purpose": item.get("purpose", "performance"),
            "submitted_monotonic_ns": time.monotonic_ns(),
        }
        records.append(record)

        async def run() -> None:
            record["started_monotonic_ns"] = time.monotonic_ns()
            started = time.perf_counter()
            try:
                result = await client.generate(
                    api_url=api_url,
                    messages=item["messages"],
                    system_prompt=item.get("system_prompt"),
                    **_sampling(item),
                )
                record["response"] = result.get("response")
                record["model"] = result.get("model")
                record["api_elapsed_ms"] = result.get("elapsed_ms")
                record["usage"] = result.get("usage") or {}
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                record["client_elapsed_ms"] = round(
                    (time.perf_counter() - started) * 1000,
                    3,
                )
                record["completed_monotonic_ns"] = time.monotonic_ns()
                record["queue_wait_ms"] = round(
                    (
                        record["started_monotonic_ns"]
                        - record["submitted_monotonic_ns"]
                    )
                    / 1_000_000,
                    3,
                )
                record["wall_ms"] = round(
                    (
                        record["completed_monotonic_ns"]
                        - record["submitted_monotonic_ns"]
                    )
                    / 1_000_000,
                    3,
                )

        admission = await queue.submit(
            chat_id=1,
            user_id=index + 1,
            run=run,
        )
        record["queued"] = admission.queued
        record["queue_position"] = admission.position
        if not admission.allowed:
            record["error"] = admission.reason

    try:
        for offset in range(0, len(requests), burst_width):
            batch = requests[offset : offset + burst_width]
            await asyncio.gather(
                *(submit(item, offset + index) for index, item in enumerate(batch))
            )
            await queue.join()
    finally:
        await queue.shutdown()
    return records


def _load_workload(path: str) -> dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def _main_async(args: argparse.Namespace) -> int:
    workload = _load_workload(args.input)
    api_url = os.environ.get(
        "BUSINESSGPT_API_URL",
        "http://businessgpt-api:8000/generate",
    )
    api_key = os.environ.get("BUSINESSGPT_API_KEY", "")
    if not api_key:
        raise RuntimeError("BUSINESSGPT_API_KEY is required")
    client = BusinessGPTClient(api_key, frozenset({api_url}))
    try:
        requests = list(workload.get("requests") or [])
        warmup_count = min(args.warmup, len(requests))
        warmup = await run_queue_benchmark(
            client=client,
            api_url=api_url,
            requests=requests[:warmup_count],
            burst_width=1,
        )
        measured = await run_queue_benchmark(
            client=client,
            api_url=api_url,
            requests=requests,
            burst_width=args.burst_width,
        )
    finally:
        await client.close()
    json.dump(
        {
            "schema_version": 1,
            "burst_width": args.burst_width,
            "warmup": warmup,
            "records": measured,
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise the real BusinessGPT bot queue")
    parser.add_argument("--input", default="-", help="Workload JSON path or - for stdin")
    parser.add_argument("--burst-width", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=1)
    return asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
