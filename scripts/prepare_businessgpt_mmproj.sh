#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LLAMA_CPP_REF="${LLAMA_CPP_REF:-a4107133a634250c8c9d888bc0bc8520dcfd6105}"
BASE_MODEL_ID="${BASE_MODEL_ID:-huihui-ai/Huihui-Qwen3.5-9B-abliterated}"
BASE_MODEL_REVISION="${BASE_MODEL_REVISION:-05b9e7c9b978ba29bdb8f50a49c30e4b91183339}"
LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$PWD/.cache/llama.cpp-$LLAMA_CPP_REF}"
PYTHON_BIN="${PYTHON_BIN:-/Users/developer/Documents/businessgpt/.venv/bin/python}"
OUTPUT_DIR="${OUTPUT_DIR:-$PWD/.artifacts/models}"
OUTPUT_PATH="${OUTPUT_PATH:-$OUTPUT_DIR/mmproj-businessgpt-v16-f16.gguf}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python environment not found: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -d "$LLAMA_CPP_DIR/.git" ]]; then
  mkdir -p "$(dirname "$LLAMA_CPP_DIR")"
  git clone --filter=blob:none --no-checkout https://github.com/ggml-org/llama.cpp "$LLAMA_CPP_DIR"
fi

git -C "$LLAMA_CPP_DIR" fetch --depth 1 origin "$LLAMA_CPP_REF"
git -C "$LLAMA_CPP_DIR" checkout --detach FETCH_HEAD

actual_ref="$(git -C "$LLAMA_CPP_DIR" rev-parse HEAD)"
if [[ "$actual_ref" != "$LLAMA_CPP_REF" ]]; then
  echo "llama.cpp revision mismatch: expected $LLAMA_CPP_REF, got $actual_ref" >&2
  exit 1
fi

actual_revision="$($PYTHON_BIN -c 'import sys; from huggingface_hub import HfApi; print(HfApi().model_info(sys.argv[1]).sha)' "$BASE_MODEL_ID")"
if [[ "$actual_revision" != "$BASE_MODEL_REVISION" ]]; then
  echo "Base model moved: expected $BASE_MODEL_REVISION, got $actual_revision" >&2
  echo "Review compatibility and update model-manifest.json before converting." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
"$PYTHON_BIN" "$LLAMA_CPP_DIR/convert_hf_to_gguf.py" \
  "$BASE_MODEL_ID" \
  --remote \
  --mmproj \
  --outtype f16 \
  --outfile "$OUTPUT_PATH"

shasum -a 256 "$OUTPUT_PATH"
ls -lh "$OUTPUT_PATH"
