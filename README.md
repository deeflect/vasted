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
uv run vasted serve
uv run vasted up
```

`setup` supports two interactive modes:
- **Express (recommended):** 2 questions only (Vast API key + model), then auto-configures GPU/quality/proxy/token.
- **Advanced:** full manual control over proxy, GPU preset, quality, and Telegram.

### Express setup flow (2 questions)

```text
? Vast.ai API key: ********
? Model [qwen3-8b]: qwen3-coder-30b
```

### Example copy-paste output

```text
✅ Config saved to ~/.config/vasted/config.yaml

Next commands:
  vasted serve
  vasted up

OpenAI endpoint:
  http://127.0.0.1:8080/v1
Authorization:
  Bearer <your-token>
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
