# vasted

[![CI](https://github.com/deeflect/vasted/actions/workflows/ci.yml/badge.svg)](https://github.com/deeflect/vasted/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

`vasted` is a CLI that launches on-demand Vast.ai GPU workers for `llama.cpp` GGUF inference and exposes a stable OpenAI-compatible `/v1` endpoint.

Built by [deeflect.com](https://deeflect.com) · Follow on X: [x.com/deeflectcom](https://x.com/deeflectcom)

## Demo

GIF placeholder (add after publish):

```md
![vasted demo](docs/assets/demo.gif)
```

## Why `vasted`

- Stable client endpoint while worker URLs rotate.
- Setup wizard for local machine and VPS deployments.
- Non-interactive automation mode for agents/CI.
- OpenAI-compatible proxy for tools that expect `/v1` APIs.
- Session usage and cost tracking.
- Optional Telegram bot control commands.

## Requirements

- Python `3.12+`
- [`uv`](https://docs.astral.sh/uv/)
- Vast.ai account + API key
- Optional: Telegram bot token (`telegram` extra)

## Install

### Use from source (recommended while iterating)

```bash
git clone https://github.com/deeflect/vasted.git
cd vasted
uv sync --extra dev
```

Run CLI commands from the repo:

```bash
uv run vasted --help
```

### Install as a tool

```bash
uv tool install "git+https://github.com/deeflect/vasted.git"
```

Upgrade later:

```bash
uv tool upgrade vasted
```

## Quick Start

```bash
uv run vasted setup
uv run vasted up
uv run vasted status --verbose
```

Client connection values after setup:

- Base URL: `http://<host>:<port>/v1`
- Auth header: `Authorization: Bearer <token>`

When `proxy_host` is `0.0.0.0`, use your real machine/VPS IP or domain in clients.

## Automation / Unattended Mode

Use non-interactive commands to avoid prompts:

```bash
uv run vasted setup --non-interactive \
  --vast-api-key "$VASTED_API_KEY" \
  --bearer-token "$VASTED_BEARER_TOKEN" \
  --client openclaw \
  --deployment-mode local_pc \
  --model qwen3-coder-30b \
  --quality balanced \
  --gpu-mode auto

uv run vasted up --non-interactive --yes --jinja --model qwen3-coder-30b --quality balanced --gpu-mode auto --no-serve
uv run vasted status --verbose
uv run vasted usage
uv run vasted down --force
```

Environment variables accepted by `setup --non-interactive`:

- `VASTED_API_KEY`
- `VASTED_BEARER_TOKEN`
- `VASTED_CLIENT` (`openclaw`, `opencode`, `custom`)
- `VASTED_LLAMA_JINJA` (`true`/`false`)
- `VASTED_MODEL`, `VASTED_QUALITY`, `VASTED_GPU_MODE`, `VASTED_GPU_PRESET`
- `VASTED_DEPLOYMENT_MODE`, `VASTED_PROXY_HOST`, `VASTED_PROXY_PORT`, `VASTED_PUBLIC_HOST`

## Client Profiles and Jinja Behavior

`setup` supports client presets that define default `llama.cpp --jinja` behavior:

- `--client openclaw`: jinja on by default
- `--client opencode`: jinja off by default
- `--client custom`: keep/manual behavior

Per launch override is still available:

```bash
uv run vasted up --jinja
uv run vasted up --no-jinja
```

## Command Reference

```bash
vasted setup [--non-interactive] [--manual] [--client openclaw|opencode|custom]
vasted up [--model ...] [--quality ...] [--gpu-mode auto|manual] [--gpu-preset ...] [--profile ...] [--max-price ...] [--jinja|--no-jinja] [--yes] [--non-interactive] [--serve|--no-serve]
vasted down [--force]
vasted status [--verbose]
vasted logs [--instance-id N] [--tail N]
vasted usage
vasted token show [--full]
vasted token rotate
vasted rotate-token
vasted config show
vasted profile list|add|use|remove
vasted completions <bash|zsh|fish>
```

## Telegram Bot (Optional)

Install telegram extra and run:

```bash
uv sync --extra telegram
uv run python bot.py
```

## Development

```bash
uv run ruff check .
uv run mypy app tests bot.py
uv run pytest -q
```

## Project Layout

- `app/commands/*`: CLI command handlers
- `app/service.py`: worker lifecycle + launch policy
- `app/proxy.py`: OpenAI-compatible reverse proxy
- `app/vast.py`: Vast API integration + startup script generation
- `app/usage.py`: token/time/cost accounting
- `app/user_config.py`: persistent config + keyring integration
- `app/state.py`: runtime state persistence
- `bot.py`: optional Telegram control plane

## Security

- Keep Vast API keys and bearer tokens private.
- Prefer localhost binds unless remote access is required.
- See [SECURITY.md](./SECURITY.md) for disclosure policy.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) and run the validation commands before opening a PR.

## License

MIT — see [LICENSE](./LICENSE).
