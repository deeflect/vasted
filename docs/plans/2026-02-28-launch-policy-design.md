# Vasted Launch Policy Design

Date: 2026-02-28

## Goals

- Optimize for coding and agentic workloads such as `opencode` and `openclaw`.
- Keep a small manual curated model list, but make custom GGUF input a first-class path.
- Remove optimistic GPU guessing and replace it with deterministic sizing.
- Make `vasted up` feel like a single command by auto-starting the proxy.

## Model Catalog

- Curated models stay manual and versioned in-repo.
- Featured defaults emphasize coding-first models.
- `Custom GGUF` remains available in setup and uses the same validation path as curated models.

## Context Policy

- `fast`: 32k target context
- `balanced`: 64k target context (default)
- `max`: 128k target context

## Sizing Policy

- Size from the exact GGUF file when possible.
- Compute conservative VRAM needs from model size, target context, and runtime reserve.
- Choose the cheapest safe GPU preset in `auto` mode.
- Reject undersized manual GPU selections before instance creation.
- Allow scaling to larger presets, including multi-GPU tiers, for bigger models and contexts.

## UX

- Setup uses interactive selections for model, quality, GPU mode, bind mode, and optional manual GPU preset.
- `vasted up` shows a launch plan, starts the proxy automatically, and updates the user while provisioning.
- Startup state is saved immediately after instance creation so interrupted launches remain visible and recoverable.

## Inventory and Readiness Follow-Up

- Vast offer searches must use provider GPU names that actually exist on Vast, not marketing shorthand.
- Vast `gpu_ram` filters must be expressed in MiB, not GiB, so `80 GB` means `81920` and cannot silently match `40 GB` cards.
- `vasted up` should run a preflight inventory check that explains whether inventory exists for the minimum safe preset, whether it had to escalate to larger presets, and which filter tier is blocking the result.
- Readiness updates should surface the current Vast `actual_status` plus a compact final line from `status_msg`, so "loading", "pulling image", and "server not listening yet" are distinguishable.
- Timeout errors should include the most recent readiness phase and status detail instead of a generic "timed out" message.
