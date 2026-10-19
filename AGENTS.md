# Runtime Development

This is the public runtime repository. Read README.md and CONTRIBUTING.md before
changing deployment behavior or publishing code.

- Base changes on the current public main branch. Do not fetch, merge, or
  cherry-pick private deployment history into this checkout.
- Import only explicitly selected runtime files and tests. Preserve upstream
  CI, security checks, portable builds, and external prompt configuration.
- This is the sole source of BusinessGPT application code and image builds.
  Infrastructure consumes immutable images; do not maintain another source
  copy, synchronization job, or dependency on a private infrastructure checkout.
- Keep host addresses, user identities, exact prompts, deployment inventories,
  live state and credentials outside Git. Use synthetic values in tests.
- Never contact live Telegram, inference, or image endpoints during unit tests.
- Run the API, bot and helper suites plus the public-boundary checker before
  committing. Check the entire reachable history before publication.
- Do not push, deploy, or change cloud resources without user authorization.

Verification commands are in README.md. SDK tests use their own environment;
do not install the Runpod lockfile over the API runtime dependencies.
