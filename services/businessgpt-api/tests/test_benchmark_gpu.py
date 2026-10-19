from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import signal
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location('benchmark_gpu', Path(__file__).parents[1] / 'benchmark_gpu.py')
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class BenchmarkGpuTests(unittest.TestCase):
    def test_plan_needs_neither_gpu_nor_api_process(self):
        output = io.StringIO()
        with patch.object(module, 'run_benchmark') as run, contextlib.redirect_stdout(output):
            self.assertEqual(module.main(['--plan']), 0)
        run.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report['request_count'], 12)
        self.assertEqual(report['cold_starts'], 3)
        self.assertIn('vision_512', report['cases'])

    def test_rejects_nonfinite_cost_and_unbounded_generation_settings(self):
        for flags in (['--gpu-hourly-rate', 'nan'], ['--idle-seconds', 'inf'], ['--repeats', '1000']):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                module.main(['--plan', *flags])

    def test_cpu_fallback_and_partial_offload_are_errors(self):
        for log in ('no usable GPU found', 'CUDA: offloaded 0/33 layers to GPU', 'CUDA: offloaded 20/33 layers to GPU'):
            with self.subTest(log=log), self.assertRaises(RuntimeError):
                module.verify_gpu_offload(log)
        self.assertEqual(module.verify_gpu_offload('CUDA0: ready\nload_tensors: offloaded 33/33 layers to GPU')['offloaded_layers'], 33)

    def test_readiness_auth_failure_is_not_retried(self):
        proc = Mock()
        proc.poll.return_value = None
        failure = urllib.error.HTTPError('http://127.0.0.1/health/ready', 401, 'unauthorized', {}, None)
        with patch.object(module, 'request_json', side_effect=failure) as request:
            with self.assertRaises(urllib.error.HTTPError):
                module.wait_ready(proc, 'http://127.0.0.1', 'test-key', module.time.monotonic() + 5)
        self.assertEqual(request.call_count, 1)

    def test_process_cleanup_covers_llama_child_even_if_uvicorn_exited(self):
        proc = Mock(pid=1234)
        proc.wait.return_value = 0
        with patch.object(module.os, 'killpg') as kill:
            module.stop_process(proc)
        self.assertEqual(kill.call_args_list[0].args, (1234, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (1234, signal.SIGKILL))

    def test_failed_generation_cleans_up_process_and_sampler(self):
        args = SimpleNamespace(time_limit=10, cold_starts=1, startup_timeout=5, max_tokens=128, repeats=1)
        report = {'starts': [], 'records': [], 'memory': []}
        proc = Mock(pid=1234)
        sampler = Mock()
        sampler.stop.return_value = {'samples': 1}
        with patch.dict(module.os.environ, {'LLAMA_ARG_LOG_VERBOSITY': '0'}), \
             patch.object(module, 'query_gpu', return_value=[['A5000', '24564', '570']]), \
             patch.object(module, 'free_port', side_effect=[8000, 8081]), \
             patch.object(module.subprocess, 'Popen', return_value=proc) as spawn, \
             patch.object(module, 'MemorySampler', return_value=sampler), \
             patch.object(module, 'wait_ready', return_value={'ok': True}), \
             patch.object(module, 'verify_gpu_offload', return_value={}), \
             patch.object(module, 'request_json', side_effect=TimeoutError('test deadline')), \
             patch.object(module, 'stop_process') as stop, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(TimeoutError):
                module.run_benchmark(args, report)
        stop.assert_called_once_with(proc)
        sampler.stop.assert_called_once()
        environment = spawn.call_args.kwargs['env']
        self.assertEqual(environment['BUSINESSGPT_ALLOW_UNVERIFIED_MODEL'], 'false')
        self.assertEqual(environment['BUSINESSGPT_N_GPU_LAYERS'], '99')
        self.assertEqual(environment['LLAMA_ARG_LOG_VERBOSITY'], '4')
        self.assertNotIn(environment['BUSINESSGPT_API_KEY'], json.dumps(report))
        self.assertEqual(report['memory'][0]['samples'], 1)

    def test_estimate_includes_startup_and_idle_not_just_token_generation(self):
        report = module.summarize(
            records=[{'phase': 'warm', 'case': 'text', 'wall_seconds': 10}],
            starts=[{'startup_seconds': 20}], requests=442, rate=0.69, idle=5,
        )
        cost = report['cases']['text']['monthly_isolated_request_compute_usd']
        self.assertAlmostEqual(cost, 442 * 35 / 3600 * 0.69, places=4)
        self.assertIn('not a Serverless cold-start measurement', report['estimate_assumptions']['scope'])

    def test_success_records_three_starts_and_twelve_generations(self):
        args = SimpleNamespace(time_limit=10, cold_starts=3, startup_timeout=5,
                               max_tokens=128, repeats=3, monthly_requests=442,
                               gpu_hourly_rate=0.69, idle_seconds=5)
        report = {'starts': [], 'records': [], 'memory': []}
        sampler = Mock()
        sampler.stop.return_value = {'gpu_peak_mib_sampled': 9000}

        def generate(url, key, timeout, payload):
            vision = isinstance(payload['messages'][0]['content'], list)
            return {'response': 'test response', 'usage': {'image_count': int(vision)}}

        with patch.object(module, 'query_gpu', return_value=[['A5000', '24564', '570']]), \
             patch.object(module, 'free_port', side_effect=[8000, 8081] * 3), \
             patch.object(module.subprocess, 'Popen'), \
             patch.object(module, 'MemorySampler', return_value=sampler), \
             patch.object(module, 'wait_ready', return_value={'ok': True}), \
             patch.object(module, 'verify_gpu_offload', return_value={}), \
             patch.object(module, 'request_json', side_effect=generate), \
             patch.object(module, 'stop_process') as stop, \
             contextlib.redirect_stderr(io.StringIO()):
            module.run_benchmark(args, report)
        self.assertEqual(len(report['starts']), 3)
        self.assertEqual(len(report['records']), 12)
        self.assertEqual(sum(row['usage']['image_count'] for row in report['records']), 3)
        self.assertEqual(stop.call_count, 3)
        self.assertEqual(set(report['summary']['cases']), {'text_short', 'text_context_10', 'vision_512'})

    def test_global_deadline_cannot_be_extended_by_new_requests(self):
        with patch.object(module.time, 'monotonic', return_value=100):
            self.assertEqual(module.remaining(105, 130), 5)
            with self.assertRaises(TimeoutError):
                module.remaining(100, 130)


if __name__ == '__main__':
    unittest.main()
