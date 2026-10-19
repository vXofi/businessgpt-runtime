#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Build a local linux/amd64 CUDA image; no push or cloud deployment.' \
    'Usage: bash scripts/build_businessgpt_gpu_benchmark.sh [--check]' \
    '--check verifies the two local model hashes without requiring Docker.' \
    'Optional env: MODEL_DIR, IMAGE_TAG, PYTHON_BIN, BUILD_JOBS, BUILD_TARGET (benchmark/serverless).'
  exit 0
fi
if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != "--check" ) ]]; then
  echo 'Expected --check, --help, or no arguments.' >&2
  exit 2
fi

MODEL_DIR="${MODEL_DIR:-$PWD/.artifacts/models}"
IMAGE_TAG="${IMAGE_TAG:-businessgpt-api:cuda-benchmark-v1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BUILD_JOBS="${BUILD_JOBS:-2}"
BUILD_TARGET="${BUILD_TARGET:-benchmark}"
case "$BUILD_TARGET" in
  benchmark|serverless) ;;
  *) echo 'BUILD_TARGET must be benchmark or serverless.' >&2; exit 2 ;;
esac

PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" - "$MODEL_DIR" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, 'services/businessgpt-api')
from model_verification import verify_model_files
root = Path(sys.argv[1]).resolve()
hashes = verify_model_files(
    manifest_path='services/businessgpt-api/model-manifest.json',
    model_path=str(root / 'model-Q5_K_M-no-mtp.gguf'),
    projector_path=str(root / 'mmproj-businessgpt-v16-f16.gguf'),
    allow_unverified=False,
)
print('Q5 + projector verified:', *hashes)
PY

if [[ "${1:-}" == "--check" ]]; then
  exit 0
fi
docker info >/dev/null
docker buildx build --platform linux/amd64 --load \
  --target "$BUILD_TARGET" \
  --build-context "model_artifacts=$MODEL_DIR" \
  --build-arg "BUILD_JOBS=$BUILD_JOBS" \
  -f services/businessgpt-api/Dockerfile.cuda \
  -t "$IMAGE_TAG" services/businessgpt-api
printf 'Built %s. Publishing and paid Pod deployment are separate steps.\n' "$IMAGE_TAG"
