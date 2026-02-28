# Vasted — Product Spec

Personal-use launcher for on-demand LLM inference on Vast.ai.

## Core Experience

One user: configure once → get one stable OpenAI-compatible endpoint → start GPU when needed → stop when done → track cost.

## Commands

| Command | What |
|---------|------|
| `vasted setup` | Interactive TUI wizard, saves config |
| `vasted serve` | Runs stable local proxy at `/v1` |
| `vasted up` | Searches Vast offers, launches worker, waits for ready |
| `vasted down` | Destroys worker, finalizes cost tracking |
| `vasted status` | Worker state, uptime, price/hr, model, endpoint |
| `vasted usage` | Requests, tokens, session cost, $/1M tokens |
| `vasted bot start` | Optional Telegram adapter |

## Architecture

### Modules
- `app/config.py` — constants, bootstrap
- `app/user_config.py` — user config load/save (YAML)
- `app/state.py` — runtime state (separate from config)
- `app/models.py` — model resolution (curated, Ollama alias, HF GGUF)
- `app/vast.py` — Vast search/create/destroy/readiness
- `app/proxy.py` — stable /v1/* proxy, bearer auth, usage accounting
- `app/usage.py` — request/token/cost tracking
- `vasted` CLI — thin layer over core
- `bot.py` — optional Telegram adapter over same core

### Key Decisions
- **Backend:** llama.cpp (GGUF only)
- **Default quality:** balanced = 64k context
- **Default preset:** 1x A100 80GB
- **Auth:** fixed user-defined bearer token at proxy; worker token hidden
- **Packaging:** uv only (pyproject.toml, uv.lock)
- **Config vs State:** separate files, never mixed

## Usage Patterns

1. **Same machine** — proxy at 127.0.0.1:4318
2. **Another device** — proxy at host IP, auto-detect public host when needed

## Run Locations
- Local machine
- Personal VPS

## Model Input
- Curated models (built-in list)
- Known Ollama alias → mapped to HF GGUF
- Direct HF GGUF repo/link
- Unknown Ollama alias → prompt for HF GGUF repo

## Vast Behavior
- Use raw Docker image launch flow (no Vast templates)
- Image: `ghcr.io/ggml-org/llama.cpp:server-cuda`
- Run `/app/llama-server` via onstart with explicit `--hf-repo` and `--hf-file`
- Use `runtype: "ssh_direc ssh_proxy"` and map `-p 8000:8000`

## Anti-Patterns (Don't Do)
1. CLI must not reuse bot handlers
2. Don't use Vast templates for llama.cpp startup
3. Don't expose raw Vast worker URL to clients
4. Don't use rotating worker tokens for client config
5. Don't mix user config and transient worker state

## Telegram (Optional)
- Same commands as CLI
- No config ownership
- No special logic — just adapter over core

## Docs
- Vast.ai: docs.vast.ai, search offers API, create instance API, instance status API
- llama.cpp: github.com/ggml-org/llama.cpp
- HF GGUF: huggingface.co/docs/hub/gguf-llamacpp
- uv: docs.astral.sh/uv/
