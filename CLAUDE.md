# Vasted

Personal GPU launcher for on-demand LLM inference on Vast.ai.

## Quick Start
```bash
uv sync
uv run vasted setup
uv run vasted serve
uv run vasted up
```

## Stack
- Python 3.12+, uv only
- rich (TUI/output), pyyaml (config), httpx (proxy/HTTP), click (CLI)
- vastai SDK for Vast.ai API
- Optional: python-telegram-bot

## Architecture
- `app/` — core modules (config, state, models, vast, proxy, usage)
- `vasted` — CLI entry point (thin wrapper over app/)
- `bot.py` — optional Telegram adapter

## Key Rules
1. CLI calls core functions directly — never bot handlers
2. Proxy is THE stable endpoint; worker URL is internal only
3. Config (user prefs) and state (active worker) are separate files
4. Bearer token is fixed by user at setup; worker auth is hidden
5. llama.cpp with GGUF models only (v1)
6. Use template_hash_id for Vast templates, don't force image

## Spec
Full spec: `docs/spec.md`
