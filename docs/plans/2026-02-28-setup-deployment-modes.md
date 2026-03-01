# Setup Deployment Modes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current setup wizard with a deployment-mode-first flow, preserve a stable bearer token by default, and add explicit token/config commands plus safe local client auto-configuration.

**Architecture:** Keep the current Click-based CLI and `UserConfig` persistence model, but add a small layer of deployment-mode metadata and setup-specific helpers so the default path is mode-driven instead of knob-driven. Implement the user-facing flow in `app/commands/setup.py`, keep reusable output logic in command helpers, and isolate client config merge logic in a dedicated helper rather than mixing file mutation into the wizard body.

**Tech Stack:** Python 3.12, Click, Rich, dataclass-based config persistence, pytest

---

### Task 1: Add deployment-mode config fields and defaults

**Files:**
- Modify: `app/user_config.py`
- Modify: `app/defaults.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_user_config_defaults_include_local_deployment_mode():
    cfg = UserConfig()
    assert cfg.deployment_mode == "local_pc"
    assert cfg.public_host == ""
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_user_config_defaults_include_local_deployment_mode -v`

Expected: FAIL because `UserConfig` does not yet have `deployment_mode` and `public_host`.

**Step 3: Write minimal implementation**

Add new persisted fields to `UserConfig`:

```python
deployment_mode: str = "local_pc"
public_host: str = ""
```

Keep defaults aligned with the existing stable endpoint assumptions:

- default mode: `local_pc`
- default bind host: existing localhost default
- default port: existing stable port

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_user_config_defaults_include_local_deployment_mode -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add app/user_config.py app/defaults.py tests/test_config.py
git commit -m "feat: add deployment mode config defaults"
```

### Task 2: Refactor setup into deployment-mode-first prompts

**Files:**
- Modify: `app/commands/setup.py`
- Modify: `app/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_finalize_defaults_for_local_pc_mode():
    cfg = UserConfig()
    apply_deployment_mode_defaults(cfg, "local_pc")
    assert cfg.proxy_host == "127.0.0.1"
    assert cfg.proxy_port == 4318
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_finalize_defaults_for_local_pc_mode -v`

Expected: FAIL because `apply_deployment_mode_defaults` does not exist.

**Step 3: Write minimal implementation**

In `app/commands/setup.py`:

- Replace the `express` / `advanced` prompt with a numbered deployment-mode picker.
- Add a helper like:

```python
def apply_deployment_mode_defaults(cfg: UserConfig, mode: str) -> None:
    if mode == "local_pc":
        cfg.proxy_host = "127.0.0.1"
        cfg.proxy_port = 4318
    elif mode in {"vps_remote", "vps_shared"}:
        cfg.proxy_host = "0.0.0.0"
        cfg.proxy_port = 4318
```

- Keep `Manual / custom` as the only branch that asks for host/port overrides.
- Keep selection-based menus for quality, GPU mode, and model choice.

In `app/config.py`, add any static labels or details needed for the setup menu if extracting mode metadata reduces duplication.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_finalize_defaults_for_local_pc_mode -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add app/commands/setup.py app/config.py tests/test_config.py
git commit -m "feat: make setup mode-first and auto-defaulted"
```

### Task 3: Preserve bearer tokens by default and split explicit token commands

**Files:**
- Modify: `app/commands/setup.py`
- Modify: `app/commands/rotate_token.py`
- Create: `app/commands/token.py`
- Modify: `app/commands/__init__.py`
- Modify: `app/cli.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_setup_reuses_existing_bearer_token():
    cfg = UserConfig()
    cfg.bearer_token_plain = "stable-token"
    token = ensure_bearer_token(cfg)
    assert token == "stable-token"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_setup_reuses_existing_bearer_token -v`

Expected: FAIL because `ensure_bearer_token` does not exist.

**Step 3: Write minimal implementation**

In `app/commands/setup.py`:

```python
def ensure_bearer_token(cfg: UserConfig) -> str:
    if cfg.bearer_token_plain:
        return cfg.bearer_token_plain
    cfg.bearer_token_plain = secrets.token_urlsafe(32)
    return cfg.bearer_token_plain
```

Then:

- Stop regenerating the token in the default setup path when one already exists.
- Keep manual override only in `--manual`.

In `app/commands/token.py`:

- Add a Click group `token`.
- Add `show` command that prints the current token.
- Reuse or wrap the existing rotate logic as `token rotate`.

In `app/cli.py` and `app/commands/__init__.py`:

- Register the new `token` group.
- Keep backward compatibility for `rotate-token` only if you want a deprecation bridge; otherwise remove it cleanly and update imports.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_setup_reuses_existing_bearer_token -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add app/commands/setup.py app/commands/rotate_token.py app/commands/token.py app/commands/__init__.py app/cli.py tests/test_config.py
git commit -m "feat: preserve setup tokens and add token commands"
```

### Task 4: Add safe OpenCode client auto-config helpers

**Files:**
- Create: `app/client_config.py`
- Modify: `app/commands/setup.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_merge_opencode_config_preserves_unrelated_providers(tmp_path):
    path = tmp_path / "opencode.json"
    path.write_text('{"provider":{"other":{"name":"Other"}}}')
    write_or_merge_opencode_config(
        path,
        base_url="http://127.0.0.1:4318/v1",
        api_key="token",
        model="qwen3-coder-30b",
        set_default_model=False,
    )
    data = json.loads(path.read_text())
    assert "other" in data["provider"]
    assert "vasted" in data["provider"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_merge_opencode_config_preserves_unrelated_providers -v`

Expected: FAIL because `write_or_merge_opencode_config` does not exist.

**Step 3: Write minimal implementation**

In `app/client_config.py`, add a focused helper:

```python
def write_or_merge_opencode_config(path: Path, base_url: str, api_key: str, model: str, set_default_model: bool) -> None:
    ...
```

Behavior:

- Create minimal JSON if the file is absent.
- Merge only the `provider.vasted` block if the file exists.
- Only set the top-level default model when `set_default_model` is `True`.
- Raise a clear exception on invalid JSON instead of overwriting unknown content.

In `app/commands/setup.py`:

- Offer OpenCode auto-config only for `local_pc`.
- Catch merge errors and fall back to printing a snippet instead of overwriting files.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_merge_opencode_config_preserves_unrelated_providers -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add app/client_config.py app/commands/setup.py tests/test_config.py
git commit -m "feat: add safe opencode auto-config"
```

### Task 5: Improve setup completion output and launch hand-off

**Files:**
- Modify: `app/commands/setup.py`
- Modify: `app/commands/common.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_display_base_url_uses_public_host_for_remote_mode():
    cfg = UserConfig()
    cfg.deployment_mode = "vps_remote"
    cfg.public_host = "gpu.example.com"
    cfg.proxy_host = "0.0.0.0"
    cfg.proxy_port = 4318
    assert display_client_base_url(cfg) == "http://gpu.example.com:4318/v1"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_display_base_url_uses_public_host_for_remote_mode -v`

Expected: FAIL because `display_client_base_url` does not exist.

**Step 3: Write minimal implementation**

Add a reusable helper in `app/commands/setup.py` or `app/commands/common.py`:

```python
def display_client_base_url(cfg: UserConfig) -> str:
    if cfg.deployment_mode in {"vps_remote", "vps_shared"} and cfg.public_host:
        return f"http://{cfg.public_host}:{cfg.proxy_port}/v1"
    return _display_base_url(cfg.proxy_host, cfg.proxy_port)
```

Then:

- Always print the stable bearer token at the end of setup.
- Offer `Launch now`.
- If declined, print the exact next command.
- For `vps_shared`, print both local-on-server and remote access guidance.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_display_base_url_uses_public_host_for_remote_mode -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add app/commands/setup.py app/commands/common.py tests/test_config.py
git commit -m "feat: improve setup output and launch hand-off"
```

### Task 6: Add config inspection command and full regression coverage

**Files:**
- Create: `app/commands/config.py`
- Modify: `app/commands/__init__.py`
- Modify: `app/cli.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_config_show_reports_deployment_mode_and_base_url(capsys):
    cfg = UserConfig()
    cfg.deployment_mode = "local_pc"
    cfg.proxy_host = "127.0.0.1"
    cfg.proxy_port = 4318
    render_config_summary(cfg)
    out = capsys.readouterr().out
    assert "local_pc" in out
    assert "http://127.0.0.1:4318/v1" in out
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_config_show_reports_deployment_mode_and_base_url -v`

Expected: FAIL because `render_config_summary` and the `config` command do not exist.

**Step 3: Write minimal implementation**

Create `app/commands/config.py` with:

- a Click group `config`
- a `show` subcommand
- a helper that prints deployment mode, bind host, port, public host (if any), selected model, and the effective client base URL

Wire it into the CLI registration in `app/cli.py` and exports in `app/commands/__init__.py`.

Then run a broader regression slice covering setup and config output behavior.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`

Expected: PASS for the new and existing config/setup-focused tests.

**Step 5: Commit**

```bash
git add app/commands/config.py app/commands/__init__.py app/cli.py tests/test_config.py
git commit -m "feat: add config show command"
```

### Task 7: Final validation

**Files:**
- Modify: `README.md`
- Test: `tests/test_config.py`
- Test: `tests/test_usage.py`

**Step 1: Write the failing doc/test expectation**

```python
def test_cli_help_lists_token_and_config_commands(runner):
    result = runner.invoke(cli, ["--help"])
    assert "token" in result.output
    assert "config" in result.output
```

**Step 2: Run test to verify it fails (if not already covered)**

Run: `uv run pytest tests/test_config.py::test_cli_help_lists_token_and_config_commands -v`

Expected: FAIL until the new commands are registered.

**Step 3: Write minimal implementation**

- Update `README.md` setup examples to describe the new deployment-mode-first setup flow.
- Add or refine CLI help tests as needed.
- Make sure the docs no longer mention `express` / `advanced` as the primary user-facing model.

**Step 4: Run full relevant verification**

Run: `uv run pytest tests/test_config.py tests/test_usage.py -v`

Expected: PASS.

Run: `uv run ruff check`

Expected: PASS.

**Step 5: Commit**

```bash
git add README.md tests/test_config.py tests/test_usage.py
git commit -m "docs: update setup flow and command help"
```
