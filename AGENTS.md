# AGENTS.md

Guidance for automation agents operating this repository.

## Goal

Run `vasted` commands in fully unattended mode without hanging on interactive prompts.

## Non-Interactive Command Patterns

Use these command forms for automation:

```bash
# 1) Configure without wizard prompts
uv run vasted setup --non-interactive \
  --vast-api-key "$VASTED_API_KEY" \
  --bearer-token "$VASTED_BEARER_TOKEN" \
  --client openclaw \
  --deployment-mode local_pc \
  --model qwen3-coder-30b \
  --quality balanced \
  --gpu-mode auto
```

```bash
# 2) Launch worker without confirmation prompts
uv run vasted up --non-interactive --yes --jinja --model qwen3-coder-30b --quality balanced --gpu-mode auto --no-serve
```

```bash
# 3) Poll status/usage
uv run vasted status --verbose
uv run vasted usage
```

```bash
# 4) Teardown safely for automation
uv run vasted down --force
```

## Important Behavior

- `vasted setup --non-interactive` never opens the interactive setup wizard.
- `vasted up --non-interactive` will fail if a confirmation is needed (for example budget overage).
- Add `--yes` to `vasted up` to auto-confirm and avoid failures in unattended jobs.
- `--client openclaw|opencode|custom` sets persisted defaults for jinja template behavior.
- For OpenClaw/chat-agent use cases, keep `--jinja` enabled (default).
- For raw completion-style workloads, pass `--no-jinja` if needed.
- Prefer `--no-serve` in headless environments where proxy process management is external.

## Required Environment Variables

- `VASTED_API_KEY`: Vast.ai API key (required for non-interactive setup if `--vast-api-key` is not passed).
- `VASTED_BEARER_TOKEN`: Optional stable proxy token. If omitted, one is generated.
- `VASTED_CLIENT`: Optional persisted client preset (`openclaw`, `opencode`, `custom`).
- `VASTED_LLAMA_JINJA`: Optional persisted jinja default (`true/false`).

## Validation Commands

Before opening PRs:

```bash
uv run ruff check .
uv run mypy app tests bot.py
uv run pytest -q
```
