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

Choose one install path:

```bash
# Option A: install as a uv tool
uv tool install .
```

```bash
# Option B: local dev environment
uv sync
```

Then run setup + start:

```bash
uv run vasted setup
uv run vasted up
```

`setup` is deployment-mode driven:
- **Local PC (default):** stable `127.0.0.1:4318` endpoint for OpenCode/OpenClaw on the same machine
- **VPS: another device:** stable `:4318` endpoint for a remote client
- **VPS: server + my device:** one proxy used locally on the VPS and remotely
- **Manual / custom:** explicit host/port/public-host/Telegram overrides

The guided path is selection-first. You mostly choose from menus, and only type when you enter:
- your Vast API key
- a `Custom GGUF` model ref

### Example copy-paste output

```text
Setup complete
Auto GPU floor: 1x A100 80GB

━━━ Add to your client config ━━━
Base URL: http://127.0.0.1:4318/v1
API Key:  <stable-token>
Model:    qwen3-coder-30b
```

Use endpoint:

- `http://<proxy_host>:<proxy_port>/v1`
- `Authorization: Bearer <your_setup_token>`

## Commands

- `vasted setup [--non-interactive]` — setup wizard or env/flag-only config mode
- `vasted setup --manual` — jump straight to low-level host/port/public-host/Telegram overrides
- `vasted serve [--watchdog] [--log-file path]` — run local proxy with optional health watchdog and JSON logs
- `vasted up [--model ...] [--profile ...] [--max-price ...]` — pick offers, show top 3, start worker
- `vasted down [--force]` — destroy active worker (`bot.py` always uses force mode)
- `vasted status [--verbose]` — status and proxy endpoint (worker URL only in verbose)
- `vasted logs [--instance-id ...] [--tail N]` — fetch exported Vast instance logs for startup/debugging
- `vasted usage` — requests/tokens/cost and $/1M tokens
- `vasted token show` — print the current stable bearer token
- `vasted token rotate` — explicitly rotate the bearer token and reprint client config
- `vasted config show` — print deployment mode, endpoint, and current model settings
- `vasted rotate-token` — legacy compatibility alias for token rotation
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

## Curated models

| Key | Hugging Face repo | File | Recommended context | Notes |
|---|---|---|---:|---|
| `qwen3-8b` | `bartowski/Qwen_Qwen3-8B-GGUF` | `Qwen_Qwen3-8B-Q4_K_M.gguf` | 32768 | Fast general-purpose model, great for chat and reasoning. |
| `gemma-3-12b` | `bartowski/google_gemma-3-12b-it-GGUF` | `google_gemma-3-12b-it-Q4_K_M.gguf` | 32768 | Strong instruction following and reasoning. |
| `qwen3-coder-30b` | `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | `Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf` | 65536 | Best open-source coding model, MoE architecture. |
| `deepseek-coder-v2-lite` | `bartowski/DeepSeek-Coder-V2-Lite-Instruct-GGUF` | `DeepSeek-Coder-V2-Lite-Instruct-Q4_K_M.gguf` | 65536 | Lightweight coding model, low VRAM. |
| `qwen2.5-7b` | `bartowski/Qwen2.5-7B-Instruct-GGUF` | `Qwen2.5-7B-Instruct-Q4_K_M.gguf` | 32768 | Proven speed/quality tradeoff. |
| `qwen2.5-coder-7b` | `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF` | `qwen2.5-coder-7b-instruct-q4_k_m.gguf` | 65536 | Code-focused, balanced cost/quality. |
| `codestral-22b` | `bartowski/Codestral-22B-v0.1-GGUF` | `Codestral-22B-v0.1-Q4_K_M.gguf` | 32768 | High-quality coding, needs larger GPU. |

## Model input formats

- Curated keys (e.g. `qwen3-8b`, `gemma-3-12b`, `qwen3-coder-30b`, `deepseek-coder-v2-lite`, `codestral-22b`)
- Known Ollama aliases (e.g. `qwen3:8b`, `gemma3:12b`, `deepseek-coder:lite`, `codestral`)
- Direct HF GGUF ref (`org/repo:model.gguf`)
- Hugging Face resolve URL (`https://huggingface.co/.../resolve/main/model.gguf`)

## Install as a tool

```bash
uv tool install .
vasted --help
```

## Maintenance

Run `uv lock --upgrade` monthly to refresh dependencies.
