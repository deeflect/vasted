# Contributing to `vasted`

Thanks for contributing.

## Development Setup

```bash
uv sync --extra dev
```

## Before Opening a PR

Run the full local checks:

```bash
uv run ruff check .
uv run mypy app tests
uv run pytest -q
```

## Pull Request Guidelines

- Keep PRs focused and small when possible.
- Add or update tests when behavior changes.
- Update docs (`README.md`, command help text) when UX changes.
- Use clear commit messages explaining intent.

## Code Style

- Python 3.12+.
- `ruff` enforces lint/format expectations.
- Prefer explicit typing on new/modified interfaces.
- Keep command output user-oriented and actionable.

## Reporting Bugs

Use GitHub Issues with:

- Exact command(s) run
- Relevant config (redact secrets)
- Expected vs actual behavior
- Logs or traceback

## Security Issues

Do not open public issues for sensitive vulnerabilities. See `SECURITY.md`.
