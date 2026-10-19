# Runtime Updates

These are portable application features, not a record of any particular host,
endpoint, account or production rollout. Credentials and prompts remain private.

## Runpod Transport

The bot supports either an allowlisted direct `/generate` URL or an allowlisted
Runpod endpoint base URL. Configure the URL in both settings:

```dotenv
BUSINESSGPT_API_URL=https://api.runpod.ai/v2/ENDPOINT_ID
BUSINESSGPT_BOT_ALLOWED_API_URLS=https://api.runpod.ai/v2/ENDPOINT_ID
BUSINESSGPT_RUNPOD_API_KEY=
BUSINESSGPT_RUNPOD_TIMEOUT_SECONDS=600
```

Replace the endpoint placeholder and fill the key only in a private env file.
Use an endpoint-scoped key where available, not an account administration key.
Direct API profiles continue to use `BUSINESSGPT_API_KEY`; credentials are never
interchanged or forwarded across redirects.

Each answer enters the existing local FIFO before submission. Remote jobs are
polled until completion or the configured deadline. Cancellation and timeouts
attempt to cancel outstanding remote work; cancellation failures are logged.
Waiting for an available worker is included in that deadline. Longer deadlines
do not guarantee capacity or reduce cold-start delay. Images are capped at 6 MiB.

The worker preserves model hashes, sampling provenance and default-prompt
metadata for existing encrypted feedback and settings behavior. Operators must
set `BUSINESSGPT_SYSTEM_PROMPT` in the worker's private environment; no prompt is
baked into the public image.

Build tooling:

```bash
bash scripts/build_businessgpt_gpu_benchmark.sh --check
bash scripts/build_businessgpt_runpod.sh
```

The helpers expect hash-verified model and projector files in
`.artifacts/models`, or an explicit `MODEL_DIR`. They build locally; they do not
publish images, create endpoints, start workers or incur cloud GPU rental.
`Dockerfile.cuda` has separate benchmark and serverless targets. The SDK uses
its own virtual environment so its dependencies cannot replace the API's pins.

## Image Generation

The image provider must implement the supported HTTPS SSE protocol at
`/v1/images/generations/stream`. This is separate from vision input and inference.

```dotenv
BUSINESSGPT_IMAGE_GENERATION_ENABLED=false
BUSINESSGPT_IMAGE_API_URL=https://images.example/v1/images/generations/stream
BUSINESSGPT_IMAGE_API_KEY=
BUSINESSGPT_BOT_IMAGE_GENERATION_TIMEOUT_SECONDS=120
BUSINESSGPT_BOT_IMAGE_GENERATION_QUEUE_SIZE=4
```

The server policy is disabled by default. Enabling requires a provider key;
Telegram settings cannot override a disabled server policy. On the generic
container deployment, the transactional toggle is:

```bash
scripts/set_businessgpt_image_generation_vm.sh disable
scripts/set_businessgpt_image_generation_vm.sh enable
```

These are deployment commands, not local tests. The helper expects an existing
configured container and its owner-controlled private env file. Disabling
removes the image key from the recreated process environment.

Users invoke `/image <description>` or reply to text with `/image`. Group admins
set image parameters through `/settings`, including custom numeric values or
JSON. Dimensions are 256-512 in 64-pixel increments, steps 2-16, and guidance
0-5. Requests use a separate bounded queue and return PNGs as Telegram spoilers.
Generated images and prompts are not stored in the bot's database, context or
feedback samples; Telegram and the image provider still receive them.

## Media And Diagnostics

Addressed video/GIF messages and replies use Telegram's generated thumbnail.
The bot does not download or decode full videos. Missing previews produce a
bounded error instead of triggering CPU-heavy frame extraction.

Polling, dispatch, trigger, queue and API boundaries carry correlated identifiers.
Duplicate-message observations and queue timing help distinguish repeated updates
from delayed work. Logs exclude message bodies, prompts, credentials and media.
Keep logging bounded and treat identifiers as operational metadata.

Structured readiness records database initialization, handler startup and a
successful polling request. A process-start log alone is not a readiness check.

## CPU Memory And Benchmarking

`BUSINESSGPT_CACHE_RAM_MIB=0` disables the separate llama.cpp host prompt cache;
the active slot can still reuse its KV prefix. `BUSINESSGPT_CTX_CHECKPOINTS=1`
bounds checkpoint retention. Both values are reported in authenticated readiness
and generation metadata. This does not cap all process memory.

The model manifest supports verified Q4 and Q5 artifacts. The conversion helper
requires an explicit `SOURCE_MODEL`; `QUANT` selects the matching hash and output
filename. GPU benchmark outputs and workloads stay outside Git and may contain
generated text. Running a benchmark on a rented GPU is a separate paid action;
the benchmark container remains idle afterward until the operator stops it.

## Verification

Run the three suites documented in the root README. Runpod worker unit tests use
fake upstreams; SDK integration tests are skipped unless the pinned SDK is
installed in a separate environment. No unit test is evidence of live Telegram
delivery, GPU performance, provider availability or a production rollout.
