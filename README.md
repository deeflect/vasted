# vasted

`vasted` launches on-demand Vast.ai GPU workers for llama.cpp GGUF inference and keeps a stable local OpenAI-compatible `/v1` proxy endpoint.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Vast.ai account + API key
- (Optional) Telegram bot token for chat control

## Stack

- `httpx` for Vast API and upstream HTTP calls
- `starlette` + `uvicorn` for proxy server
- `click` + `rich` for CLI UX
- `pyyaml` for config/state persistence
- optional `python-telegram-bot`

## Architecture

- `app/commands/*` — CLI command handlers
- `app/cli.py` — command registration and entrypoint
- `app/service.py` — worker lifecycle/business logic (`start_worker`, `stop_worker`, status/usage, budget + idle guards)
- `app/persistence.py` — atomic YAML dataclass load/save helpers
- `app/state.py` and `app/user_config.py` — persisted runtime/config dataclasses
- `app/proxy.py` — OpenAI-compatible `/v1` forward proxy
- `bot.py` — Telegram control plane built on the service layer

## Quick start

```bash
uv sync
uv run vasted setup
uv run vasted serve
uv run vasted up
```

Use endpoint:

- `http://<proxy_host>:<proxy_port>/v1`
- `Authorization: Bearer <your_setup_token>`

## Commands

- `vasted setup [--non-interactive]` — setup wizard or env/flag-only config mode
- `vasted serve [--watchdog] [--log-file path]` — run local proxy with optional health watchdog and JSON logs
- `vasted up [--model ...] [--profile ...] [--max-price ...]` — pick offers, show top 3, start worker
- `vasted down [--force]` — destroy active worker (`bot.py` always uses force mode)
- `vasted status [--verbose]` — status and proxy endpoint (worker URL only in verbose)
- `vasted usage` — requests/tokens/cost and $/1M tokens
- `vasted rotate-token` — generate and persist new bearer token
- `vasted profile list|add|use|remove` — manage model/quality/GPU named profiles
- `vasted completions <bash|zsh|fish>` — print shell completion script

### Shell completion

```bash
# bash
eval "$(vasted completions bash)"

# zsh
eval "$(vasted completions zsh)"

# fish
vasted completions fish | source
```

To persist, add the command to your shell rc file.

## Model input formats

- Curated keys (e.g. `qwen2.5-7b`, `deepseek-coder-v2-lite`)
- Known Ollama aliases (e.g. `qwen2.5:7b`, `codestral:22b`)
- Direct HF GGUF ref (`org/repo:model.gguf`)
- Hugging Face resolve URL (`https://huggingface.co/.../resolve/main/model.gguf`)

## Maintenance

Run `uv lock --upgrade` monthly to refresh dependencies.
