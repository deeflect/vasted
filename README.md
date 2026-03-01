# `vasted`

[![CI](https://github.com/borb/vasted/actions/workflows/ci.yml/badge.svg)](https://github.com/borb/vasted/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

`vasted` launches on-demand Vast.ai GPU workers for `llama.cpp` GGUF inference and exposes a stable local OpenAI-compatible `/v1` endpoint.

## Why Use It

- Stable local endpoint while worker hosts rotate.
- Guided setup for local machine and VPS modes.
- OpenAI-compatible proxy for editor/agent tooling.
- Usage and cost tracking with request/token metrics.
- Optional Telegram bot commands (`/up`, `/down`, `/status`, `/usage`).

## Requirements

- Python `3.12+`
- [`uv`](https://docs.astral.sh/uv/)
- Vast.ai account with API key
- Optional: Telegram bot token

## Install

Install directly from GitHub right now:

```bash
uv tool install "git+https://github.com/borb/vasted.git"
```

Upgrade later:

```bash
uv tool upgrade vasted
```

For contributors/developers:

```bash
# as project tool from local checkout
uv tool install .

# development environment
uv sync --extra dev
```

Optional Telegram support:

```bash
uv sync --extra telegram
```

## Quick Start

```bash
uv run vasted setup
uv run vasted up
uv run vasted status
```

After setup, point your client at:

- `Base URL`: `http://<proxy_host>:<proxy_port>/v1`
- `API key`: `Bearer <setup token>`

## Core Commands

- `vasted setup [--non-interactive] [--manual] [--client openclaw|opencode|custom]`
- `vasted up [--model ...] [--profile ...] [--max-price ...] [--jinja|--no-jinja] [--non-interactive] [--yes]`
- `vasted down [--force]`
- `vasted status [--verbose]`
- `vasted usage`
- `vasted logs [--instance-id ...] [--tail N]`
- `vasted serve [--watchdog] [--log-file path]`
- `vasted token show|rotate`
- `vasted config show`
- `vasted profile list|add|use|remove`
- `vasted completions <bash|zsh|fish>`

## Telegram Bot (Optional)

After setting `telegram_token` and `telegram_chat_id` in setup:

```bash
uv run python bot.py
```

## Automation / Agent Mode

Use non-interactive flags to avoid prompts in CI/agents:

```bash
uv run vasted setup --non-interactive --vast-api-key "$VASTED_API_KEY"
uv run vasted up --non-interactive --yes --model qwen3-coder-30b --quality balanced --gpu-mode auto
```

Agent-specific usage guidance is in `AGENTS.md`.

## OpenClaw vs OpenCode

- OpenClaw-style assistant/chat agents often require `llama.cpp --jinja`.
- Setup can pick a client preset that controls default jinja behavior:
  - `vasted setup --client openclaw` (jinja default on)
  - `vasted setup --client opencode` (jinja default off)
  - `vasted setup --client custom` (manual/default behavior)
- `vasted` now enables jinja by default and supports per-run override:
  - force on: `vasted up --jinja`
  - force off: `vasted up --no-jinja`
- The proxy normalizes strict chat-role payloads for llama-server templates:
  - maps `developer` -> `system`
  - maps legacy `function` -> `tool`
  - flattens structured `content` blocks to text for compatibility
- Persisted default can be configured in setup:
  - `vasted setup --non-interactive --client openclaw`
  - `vasted setup --non-interactive --client opencode`
  - `vasted setup --non-interactive --llama-jinja`
  - `vasted setup --non-interactive --no-llama-jinja`

## PyPI (Prepared)

Publishing workflow and release docs are prepped. Once the package is published, users will be able to install with:

```bash
uv tool install vasted
```

Release process documentation: `RELEASING.md`.

## Model Inputs

Supported model input formats:

- Curated keys (`qwen3-coder-30b`, `gemma-3-12b`, ...)
- Known Ollama aliases (`qwen3:8b`, `codestral`, ...)
- HF GGUF ref (`org/repo:model.gguf`)
- HF resolve URL (`https://huggingface.co/.../resolve/main/model.gguf`)

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run mypy app tests
uv run pytest -q
```

`CI` runs lint, typing, and tests on pushes and pull requests.

## Project Layout

- `app/commands/*`: CLI commands
- `app/service.py`: worker lifecycle and policy logic
- `app/proxy.py`: OpenAI-compatible reverse proxy
- `app/vast.py`: Vast API client and startup script builder
- `app/usage.py`: request/token/cost accounting
- `app/state.py`, `app/user_config.py`: persisted state/config
- `bot.py`: Telegram control plane

## Security Notes

- Keep your Vast API key and bearer token private.
- Prefer binding proxy to localhost unless remote access is required.
- Review `SECURITY.md` for vulnerability reporting.

## Contributing

See `CONTRIBUTING.md` for workflow and standards. Please run lint, mypy, and tests before opening a PR.

## License

MIT. See `LICENSE`.

## Project Docs

- `docs/spec.md`
- `CHANGELOG.md`
- `SECURITY.md`
- `RELEASING.md`
