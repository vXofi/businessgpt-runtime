from __future__ import annotations

import unittest

from backend_provenance import extract_backend_provenance


class BackendProvenanceTests(unittest.TestCase):
    def test_extracts_finish_seed_cache_and_timings(self) -> None:
        result = extract_backend_provenance(
            {
                "choices": [{"finish_reason": "stop"}],
                "usage": {"prompt_tokens_details": {"cached_tokens": 12}},
                "timings": {
                    "prompt_n": 20,
                    "prompt_ms": 100.5,
                    "predicted_n": 8,
                    "predicted_ms": 200.25,
                },
            },
            requested_seed=41,
        )
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["seed"], 41)
        self.assertEqual(result["cached_prompt_tokens"], 12)
        self.assertEqual(result["llama_prompt_tokens"], 20)
        self.assertEqual(result["llama_predicted_ms"], 200.25)

    def test_handles_sparse_backend_response(self) -> None:
        self.assertEqual(
            extract_backend_provenance({}, requested_seed=None),
            {
                "finish_reason": None,
                "seed": None,
                "cached_prompt_tokens": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
