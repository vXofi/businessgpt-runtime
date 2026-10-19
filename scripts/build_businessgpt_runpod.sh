#!/usr/bin/env bash
set -euo pipefail

export BUILD_TARGET=serverless
export IMAGE_TAG="${IMAGE_TAG:-businessgpt-api:runpod-v1}"
exec bash "$(dirname "$0")/build_businessgpt_gpu_benchmark.sh" "$@"
