# Setup Deployment Modes Design

**Goal:** Replace the current `express` / `advanced` setup flow with a mode-first wizard that matches the user's deployment intent, minimizes typing, and keeps the client-facing endpoint and bearer token stable.

## Problem

The current setup wizard is still organized around internal implementation labels:

- Users must choose `express` or `advanced`.
- The default flow still asks for text input in places where it should be selection-based.
- Networking defaults are mixed into the main path even when most users should keep a fixed local endpoint.
- The bearer token is generated, but the UX does not clearly frame it as the stable client credential that should remain unchanged after setup.

This creates unnecessary friction, especially for OpenCode / OpenClaw users who want a stable endpoint and do not want to keep reconfiguring their clients.

## User Modes

The setup wizard should begin with a single mode-selection screen:

- `Local PC`
- `VPS: use from another device`
- `VPS: use from server and my device`
- `Manual / custom`

These modes map to real deployment intent, which is what the user actually knows. The wizard should no longer lead with `express` / `advanced`.

## Default Behavior

For the first three modes, setup should be auto-first:

- Generate a bearer token only if one does not already exist.
- Preserve the same bearer token on future setup runs unless the user explicitly rotates it elsewhere.
- Keep the default port stable at `4318`.
- Keep the default local endpoint stable so OpenCode / OpenClaw config does not need to be changed repeatedly.
- Use selection-based prompts wherever possible.
- Only require freeform typing when the user chooses `Custom GGUF` or enters the Vast API key.

Per-mode defaults:

- `Local PC`
  - Bind to `127.0.0.1`
  - Use port `4318`
  - Present `http://127.0.0.1:4318/v1` as the client endpoint
- `VPS: use from another device`
  - Bind to `0.0.0.0`
  - Use port `4318`
  - Print the remote URL using a configurable public host/IP/domain if available, otherwise fall back to clear guidance that the user should substitute their VPS host
- `VPS: use from server and my device`
  - Bind to `0.0.0.0`
  - Use port `4318`
  - Print both the server-local URL and the remote URL guidance
- `Manual / custom`
  - Expose the current low-level prompts for host, port, token override, and similar knobs

## Model and Prompting UX

The setup wizard should remain interactive, but reduce typing:

- Use numbered selection menus for deployment mode, model, quality, GPU mode, and optional client integration.
- Keep `Custom GGUF` as a first-class option in the model picker.
- If `Custom GGUF` is selected, prompt once for the pasted model reference and validate it.
- Avoid text-choice prompts like `Setup mode [express/advanced]`.

The wizard should feel like guided selection, not a form.

## Stable Bearer Token

The bearer token should be treated as a long-lived client credential:

- Create it automatically when none exists.
- Persist it in the existing config/keyring flow.
- Do not replace it during normal setup reruns.
- Show it clearly at the end of setup every time.
- Reserve token changes for explicit token commands, not general setup.

This keeps OpenCode / OpenClaw configuration stable.

## Safe Client Auto-Config

For `Local PC`, setup should optionally offer local client configuration:

- `Configure OpenCode on this machine`
- `Configure OpenClaw on this machine` when its config path is known
- `Skip`

The update strategy must be merge-only:

- If the config file does not exist, create the smallest valid config.
- If it exists, parse it and only add or update the `vasted` provider block.
- Never remove unrelated providers, models, or user preferences.
- Only set the default model if the user explicitly agrees.
- If parsing fails or the format is unknown, do not overwrite the file; print a config snippet instead.

For remote VPS modes, setup should not auto-edit local client config by default because the final endpoint may be a non-local host.

## Command Model

The command surface should be clarified so setup is not overloaded:

- `vasted setup`
  - Mode-first guided setup
  - Preserves the existing bearer token unless missing
- `vasted setup --manual`
  - Explicit low-level path for advanced overrides
- `vasted token show`
  - Print the current bearer token
- `vasted token rotate`
  - Generate a new bearer token only when explicitly requested
  - Reprint the client config afterward
- `vasted config show`
  - Print deployment mode, bind host, port, base URL, and selected model

This cleanly separates one-time guided setup from explicit credential and networking maintenance.

## Launch Hand-Off

After setup completes, the wizard should:

- Print the exact client config the user needs
- Show the stable bearer token
- Offer `Launch now`

If the user declines, the wizard should print the exact next command.

## Testing Focus

The implementation should be validated with tests that cover:

- Mode-to-network defaults
- Stable token preservation on repeated setup runs
- New token generation only when none exists
- Manual mode preserving override behavior
- Safe merge behavior for OpenCode config updates
- Fallback-to-snippet behavior when client config parsing fails
- Command routing for `token show`, `token rotate`, and `config show`
