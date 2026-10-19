#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

QUANT="${QUANT:-Q5_K_M}"
case "$QUANT" in
  Q5_K_M)
    default_hash="b92a2f414c7cd835a4c21db80d54f6cb996ab163e9b1acdc678cb1f6a02ba74b"
    ;;
  Q4_K_M)
    default_hash="2cfa9995a830a7f515cd090e066c53a6163094fdb6280d93d272290c6e9b9170"
    ;;
  *)
    echo "QUANT must be Q5_K_M or Q4_K_M" >&2
    exit 2
    ;;
esac

SOURCE_MODEL="${SOURCE_MODEL:?Set SOURCE_MODEL to the local source GGUF}"
EXPECTED_SOURCE_SHA256="${EXPECTED_SOURCE_SHA256:-$default_hash}"
OUTPUT_DIR="${OUTPUT_DIR:-$PWD/.artifacts/models}"
OUTPUT_NAME="${OUTPUT_NAME:-model-${QUANT}-no-mtp.gguf}"

[[ -f "$SOURCE_MODEL" ]] || { echo "Missing source model: $SOURCE_MODEL" >&2; exit 1; }
docker image inspect businessgpt-api:llama-server >/dev/null

actual_source_sha256="$(shasum -a 256 "$SOURCE_MODEL" | awk '{print $1}')"
if [[ "$actual_source_sha256" != "$EXPECTED_SOURCE_SHA256" ]]; then
  echo "Source GGUF hash mismatch: $actual_source_sha256" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
docker run --rm \
  --entrypoint /opt/llama.cpp/build/bin/llama-quantize \
  -v "$(dirname "$SOURCE_MODEL"):/input:ro" \
  -v "$OUTPUT_DIR:/output" \
  businessgpt-api:llama-server \
  --override-kv qwen35.block_count=int:32 \
  --override-kv qwen35.nextn_predict_layers=int:0 \
  "/input/$(basename "$SOURCE_MODEL")" \
  "/output/$OUTPUT_NAME" \
  COPY 8

shasum -a 256 "$OUTPUT_DIR/$OUTPUT_NAME"
ls -lh "$OUTPUT_DIR/$OUTPUT_NAME"
