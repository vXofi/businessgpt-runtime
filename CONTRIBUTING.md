# Contributing

## Public Repository Rules

- Use `.example` domains and RFC 5737 documentation addresses.
- Keep Telegram IDs, credentials, endpoints, and the exact runtime prompt in
  private environment files.
- Use obvious synthetic identifiers in tests.
- Do not add runtime databases, feedback exports, media, models, archives, or
  benchmark outputs.
- Keep host-specific operations and unrelated service inventory outside Git.
- Start feature branches from this repository's current `main`. Transfer only
  reviewed runtime files from other checkouts; never merge deployment history.
- Do not use Gitleaks allowlists to suppress a real credential.

## One-Time Setup

```bash
brew install gitleaks pre-commit
pre-commit install --hook-type pre-commit --hook-type pre-push
```

The commit hook scans the current tree. The push hook scans all reachable
history. Both run the repository-specific public-boundary checker.

## Before Opening A Pull Request

```bash
pre-commit run --all-files
PYTHONPATH=services/businessgpt-api \
  python -m unittest discover -s services/businessgpt-api/tests -v
PYTHONPATH=services/businessgpt-bot \
  python -m unittest discover -s services/businessgpt-bot/tests -v
python -m unittest discover -s scripts/tests -v
```

`main` accepts changes only through pull requests with all required checks
passing.
