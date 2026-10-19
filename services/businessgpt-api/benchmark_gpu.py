#!/usr/bin/env python3
"""Measure isolated API process starts and text/vision requests on one NVIDIA GPU."""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import math
import os
import re
import secrets
import signal
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path


API_DIR = Path(__file__).resolve().parent


def fixture_image() -> bytes:
    """A 512px RGB fixture: red rectangle and blue circle on a white background."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('!I', len(data)) + kind + data + struct.pack(
            '!I', zlib.crc32(kind + data) & 0xFFFFFFFF
        )

    pixels = bytearray()
    for y in range(512):
        pixels.append(0)
        for x in range(512):
            color = (255, 255, 255)
            if 40 <= x < 200 and 120 <= y < 360:
                color = (220, 30, 30)
            elif (x - 360) ** 2 + (y - 240) ** 2 < 90 ** 2:
                color = (30, 70, 220)
            pixels.extend(color)
    return b'\x89PNG\r\n\x1a\n' + chunk(
        b'IHDR', struct.pack('!2I5B', 512, 512, 8, 2, 0, 0, 0)
    ) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def workloads(max_tokens: int) -> dict:
    settings = {
        'max_tokens': max_tokens, 'temperature': 0.7, 'top_p': 0.9,
        'top_k': 50, 'repetition_penalty': 1.2, 'seed': 42,
    }
    short = {'role': 'user', 'content': 'Завтра экзамен, а я опять весь вечер выбирал шрифт для конспекта.'}
    conversation = [
        {'role': 'user', 'name': f'student{i % 3}', 'content': text}
        for i, text in enumerate([
            'Давайте договоримся, когда готовиться к экзамену.',
            'Я могу вечером после семи, раньше пары.',
            'У меня конспект только за половину семестра.',
            'Остальное есть в общей папке, там ещё список вопросов.',
            'В списке сорок вопросов, а у нас два дня.',
            'Предлагаю разделить вопросы и потом обсудить вместе.',
            'Только без презентации на сто слайдов, времени нет.',
            'Я сделал таблицу, но забыл открыть доступ.',
            'Мы уже час обсуждаем подготовку вместо самой подготовки.',
            short['content'],
        ])
    ]
    image = 'data:image/png;base64,' + base64.b64encode(fixture_image()).decode('ascii')
    return {
        'text_short': {**settings, 'messages': [short]},
        'text_context_10': {**settings, 'messages': conversation},
        'vision_512': {**settings, 'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Какие фигуры и цвета видны на изображении? Ответь коротко.'},
            {'type': 'image_url', 'image_url': {'url': image}},
        ]}]},
    }


def query_gpu(fields: str) -> list[list[str]]:
    result = subprocess.run(
        ['nvidia-smi', f'--query-gpu={fields}', '--format=csv,noheader,nounits'],
        check=True, capture_output=True, text=True, timeout=3,
    )
    return [[value.strip() for value in row] for row in csv.reader(io.StringIO(result.stdout)) if row]


class MemorySampler:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.gpu_peak_mib: float | None = None
        self.container_peak_mib: float | None = None
        self.samples = 0
        self.errors = 0

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.gpu_peak_mib = max(self.gpu_peak_mib or 0, float(query_gpu('memory.used')[0][0]))
                self.samples += 1
                current = Path('/sys/fs/cgroup/memory.current')
                if current.is_file():
                    self.container_peak_mib = max(
                        self.container_peak_mib or 0, int(current.read_text()) / (1024 ** 2)
                    )
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                self.errors += 1
            self.stop_event.wait(0.5)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> dict:
        self.stop_event.set()
        self.thread.join(timeout=4)
        return {
            'gpu_peak_mib_sampled': self.gpu_peak_mib,
            'container_ram_peak_mib_sampled': self.container_peak_mib,
            'samples': self.samples, 'sampling_errors': self.errors,
        }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, key: str, timeout: float, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def remaining(deadline: float, maximum: float) -> float:
    value = min(deadline - time.monotonic(), maximum)
    if value <= 0:
        raise TimeoutError('benchmark time limit reached')
    return value


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_ready(proc, url: str, key: str, deadline: float) -> dict:
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f'API process exited with status {proc.returncode}')
        try:
            result = request_json(url + '/health/ready', key, remaining(deadline, 2))
            if not result.get('ok') or not result.get('vision_enabled'):
                raise RuntimeError('readiness must confirm both model and vision')
            if not result.get('model_sha256') or not result.get('projector_sha256'):
                raise RuntimeError('readiness is missing verified model hashes')
            return result
        except urllib.error.HTTPError as exc:
            if exc.code != 503:
                raise
        except (urllib.error.URLError, TimeoutError):
            remaining(deadline, 1)
        time.sleep(min(0.5, remaining(deadline, 0.5)))


def verify_gpu_offload(log: str) -> dict:
    matches = re.findall(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU', log)
    if not matches:
        raise RuntimeError('llama.cpp did not report GPU layer offload')
    loaded, total = map(int, matches[-1])
    if loaded != total or total == 0 or 'CUDA' not in log:
        raise RuntimeError(f'full CUDA offload was not confirmed: {loaded}/{total}')
    return {'offloaded_layers': loaded, 'total_layers': total}


def stop_process(proc) -> None:
    # uvicorn owns llama-server as a child; clean up the entire session on every path.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)


def summarize(records: list[dict], starts: list[dict], requests: int, rate: float, idle: float) -> dict:
    startup = statistics.median(item['startup_seconds'] for item in starts)
    result = {}
    for name in sorted({row['case'] for row in records if row['phase'] == 'warm'}):
        rows = [row for row in records if row['phase'] == 'warm' and row['case'] == name]
        latency = statistics.median(row['wall_seconds'] for row in rows)
        result[name] = {
            'wall_seconds_p50': latency,
            'wall_seconds_max': max(row['wall_seconds'] for row in rows),
            'monthly_isolated_request_compute_usd': round(requests * (startup + latency + idle) / 3600 * rate, 4),
        }
    return {
        'application_startup_seconds_p50': startup, 'cases': result,
        'estimate_assumptions': {
            'requests_per_month': requests, 'serverless_gpu_usd_per_hour': rate,
            'idle_seconds_per_isolated_request': idle,
            'scope': 'Each case separately assumes all monthly requests use that workload and a fresh API process. Platform startup, storage, CPU host, taxes and Pod idle billing are excluded. This is not a Serverless cold-start measurement or invoice.',
        },
    }


def run_benchmark(args, report: dict) -> None:
    cases = workloads(args.max_tokens)
    gpu = query_gpu('name,memory.total,driver_version')
    if len(gpu) != 1:
        raise RuntimeError('benchmark requires exactly one visible GPU')
    report['gpu'] = {'name': gpu[0][0], 'memory_total_mib': float(gpu[0][1]), 'driver': gpu[0][2]}
    deadline = time.monotonic() + args.time_limit
    for boot in range(args.cold_starts):
        port, llama_port = free_port(), free_port()
        while llama_port == port:
            llama_port = free_port()
        key = secrets.token_urlsafe(32)
        env = {**os.environ,
            'HOST': '127.0.0.1', 'LLAMA_HOST': '127.0.0.1', 'LLAMA_PORT': str(llama_port),
            # The pinned llama.cpp emits CUDA/offload details only at trace level.
            'LLAMA_ARG_LOG_VERBOSITY': '4',
            'BUSINESSGPT_API_KEY': key, 'BUSINESSGPT_ALLOW_UNVERIFIED_MODEL': 'false',
            'BUSINESSGPT_ALLOW_INSECURE_NO_AUTH': 'false', 'BUSINESSGPT_N_GPU_LAYERS': '99',
            'BUSINESSGPT_N_CTX': '4096', 'BUSINESSGPT_N_PARALLEL': '1',
            'BUSINESSGPT_N_THREADS': '4', 'BUSINESSGPT_CACHE_RAM_MIB': '0',
            'BUSINESSGPT_CTX_CHECKPOINTS': '1', 'BUSINESSGPT_GENERATION_TIMEOUT_SECONDS': '120',
        }
        url = f'http://127.0.0.1:{port}'
        with tempfile.TemporaryDirectory(prefix='businessgpt-gpu-') as temporary:
            log_path = Path(temporary) / 'api.log'
            sampler = MemorySampler()
            with log_path.open('w') as log:
                started = time.monotonic()
                remaining(deadline, 1)
                proc = subprocess.Popen(
                    [sys.executable, '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1',
                     '--port', str(port), '--workers', '1', '--no-access-log'],
                    cwd=API_DIR, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
                sampler.start()
                try:
                    ready = wait_ready(proc, url, key, min(deadline, started + args.startup_timeout))
                    startup = time.monotonic() - started
                    offload = verify_gpu_offload(log_path.read_text())
                    report['starts'].append({'boot': boot + 1, 'startup_seconds': startup, **ready, **offload})
                    print(f'Boot {boot + 1}/{args.cold_starts} ready in {startup:.2f}s', file=sys.stderr, flush=True)
                    sequence = [('first_request', 'text_short')]
                    if boot == args.cold_starts - 1:
                        sequence += [('warm', name) for _ in range(args.repeats) for name in cases]
                    for phase, name in sequence:
                        request_started = time.monotonic()
                        data = request_json(url + '/generate', key, remaining(deadline, 130), cases[name])
                        if not isinstance(data.get('response'), str) or not data['response'].strip():
                            raise RuntimeError(f'{name}: empty model response')
                        usage = data.get('usage', {})
                        if name == 'vision_512' and usage.get('image_count') != 1:
                            raise RuntimeError('vision request did not process an image')
                        report['records'].append({
                            'boot': boot + 1, 'phase': phase, 'case': name,
                            'wall_seconds': time.monotonic() - request_started,
                            'api_elapsed_ms': data.get('elapsed_ms'), 'usage': usage,
                            'response': data['response'],
                        })
                        print(f'{phase}: {name} completed', file=sys.stderr, flush=True)
                except BaseException:
                    report['failed_api_log_tail'] = '\n'.join(log_path.read_text().splitlines()[-40:])
                    raise
                finally:
                    try:
                        stop_process(proc)
                    finally:
                        report['memory'].append({'boot': boot + 1, **sampler.stop()})
    report['summary'] = summarize(report['records'], report['starts'], args.monthly_requests, args.gpu_hourly_rate, args.idle_seconds)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Show the workload without starting any processes or requiring a GPU')
    parser.add_argument('--cold-starts', type=int, default=3)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--max-tokens', type=int, default=128)
    parser.add_argument('--time-limit', type=float, default=600)
    parser.add_argument('--startup-timeout', type=float, default=180)
    parser.add_argument('--monthly-requests', type=int, default=442)
    parser.add_argument('--gpu-hourly-rate', type=float, default=0.69, help='Serverless scenario rate, not the price of the test Pod')
    parser.add_argument('--idle-seconds', type=float, default=5)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not (1 <= args.cold_starts <= 10 and 1 <= args.repeats <= 20 and 1 <= args.max_tokens <= 1024):
        parser.error('cold-starts: 1..10; repeats: 1..20; max-tokens: 1..1024')
    if any(not math.isfinite(value) or value <= 0 for value in (args.time_limit, args.startup_timeout, args.gpu_hourly_rate)):
        parser.error('timeouts and GPU rate must be finite and positive')
    if args.monthly_requests < 0 or not math.isfinite(args.idle_seconds) or args.idle_seconds < 0:
        parser.error('monthly requests and idle time must be non-negative')
    if args.output and args.output.exists():
        parser.error('output already exists; choose a new filename')
    report = {
        'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'python_version': sys.version.split()[0],
        'status': 'planned', 'cold_starts': args.cold_starts,
        'request_count': args.cold_starts + 3 * args.repeats,
        'max_tokens': args.max_tokens, 'time_limit_seconds': args.time_limit,
        'cases': list(workloads(args.max_tokens)), 'starts': [], 'records': [], 'memory': [],
    }
    status = 0
    if not args.plan:
        try:
            run_benchmark(args, report)
            report['status'] = 'completed'
        except (Exception, KeyboardInterrupt) as exc:
            report['status'] = 'failed'
            report['error'] = f'{type(exc).__name__}: {exc}'
            status = 1
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
            output.write(encoded + '\n')
    print(encoded, flush=True)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
