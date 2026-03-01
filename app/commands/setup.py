from __future__ import annotations

import os
import secrets
import sys

import click
from rich.panel import Panel
from rich.prompt import Prompt

from app.client_config import OPENCODE_CONFIG_PATH, render_opencode_config, write_or_merge_opencode_config
from app.commands.common import console, print_client_config
from app.config import CURATED_MODELS, GPU_PRESETS, QUALITY_PROFILES
from app.models import choose_default_gguf_file, featured_model_keys, resolve_model, suggest_gpu_preset
from app.user_config import UserConfig, load_config, save_config
from app.vast import VastAPI, VastAuthError

DEPLOYMENT_MODE_ROWS: list[tuple[str, str, str]] = [
    ("local_pc", "Local PC", "Run the proxy on this machine for local OpenCode/OpenClaw use"),
    ("vps_remote", "VPS: another device", "Run on a VPS and use it from a different device"),
    ("vps_shared", "VPS: server + my device", "Run on a VPS and use it both there and from your device"),
    ("manual", "Manual / custom", "Change host, port, public host, and optional extras yourself"),
]
CLIENT_PROFILE_ROWS: list[tuple[str, str, str]] = [
    ("openclaw", "OpenClaw / chat-agent", "Default `--jinja` enabled"),
    ("opencode", "OpenCode / coding CLI", "Default `--jinja` disabled"),
    ("custom", "Custom", "Do not force a jinja default"),
]
CLIENT_PROFILE_JINJA_DEFAULTS: dict[str, bool | None] = {
    "openclaw": True,
    "opencode": False,
    "custom": None,
}


def _choose(title: str, rows: list[list[str]], default_index: int = 1) -> int:
    from rich.table import Table

    while True:
        table = Table(title=title)
        table.add_column("#")
        table.add_column("Option")
        table.add_column("Details")
        for row in rows:
            table.add_row(*row)
        console.print(table)
        raw = Prompt.ask("Choose by number", default=str(default_index))
        try:
            idx = int(raw)
            if 1 <= idx <= len(rows):
                return idx - 1
        except ValueError:
            pass
        console.print("[yellow]Invalid selection. Try again.[/yellow]")


def _ask_port(default: int) -> int:
    while True:
        raw = Prompt.ask("Proxy port", default=str(default))
        try:
            port = int(raw)
            if 1024 <= port <= 65535:
                return port
        except ValueError:
            pass
        console.print("[yellow]Port must be an integer between 1024 and 65535.[/yellow]")


def _display_base_url(host: str, port: int) -> str:
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{display_host}:{port}/v1"


def display_client_base_url(cfg: UserConfig) -> str:
    if cfg.deployment_mode in {"vps_remote", "vps_shared"}:
        if cfg.public_host:
            return f"http://{cfg.public_host}:{cfg.proxy_port}/v1"
        return f"http://<your-vps-host>:{cfg.proxy_port}/v1"
    return _display_base_url(cfg.proxy_host, cfg.proxy_port)


def ensure_bearer_token(cfg: UserConfig, override: str | None = None) -> str:
    if override:
        cfg.bearer_token_plain = override
        return cfg.bearer_token_plain
    if cfg.bearer_token_plain:
        return cfg.bearer_token_plain
    cfg.bearer_token_plain = secrets.token_urlsafe(32)
    return cfg.bearer_token_plain


def apply_deployment_mode_defaults(cfg: UserConfig, mode: str) -> None:
    cfg.deployment_mode = mode
    if mode == "local_pc":
        cfg.proxy_host = "127.0.0.1"
        cfg.proxy_port = 4318
        return
    if mode in {"vps_remote", "vps_shared"}:
        cfg.proxy_host = "0.0.0.0"
        cfg.proxy_port = 4318


def _pick_deployment_mode(default_mode: str) -> str:
    rows = [[str(i), label, detail] for i, (_, label, detail) in enumerate(DEPLOYMENT_MODE_ROWS, start=1)]
    keys = [key for key, _, _ in DEPLOYMENT_MODE_ROWS]
    default_index = keys.index(default_mode) + 1 if default_mode in keys else 1
    idx = _choose("Choose deployment mode", rows, default_index=default_index)
    return keys[idx]


def _normalize_client_profile(raw: str | None, default: str = "openclaw") -> str:
    if not raw:
        return default
    value = raw.strip().lower()
    if value not in CLIENT_PROFILE_JINJA_DEFAULTS:
        raise click.ClickException(f"Unknown client profile: {raw}")
    return value


def _pick_client_profile(default_profile: str) -> str:
    rows = [[str(i), label, detail] for i, (_, label, detail) in enumerate(CLIENT_PROFILE_ROWS, start=1)]
    keys = [key for key, _, _ in CLIENT_PROFILE_ROWS]
    default_index = keys.index(default_profile) + 1 if default_profile in keys else 1
    idx = _choose("Choose client profile", rows, default_index=default_index)
    return keys[idx]


def _choose_yes_no(title: str, yes_detail: str, no_detail: str, default_yes: bool = True) -> bool:
    rows = [
        ["1", "Yes", yes_detail],
        ["2", "No", no_detail],
    ]
    idx = _choose(title, rows, default_index=1 if default_yes else 2)
    return idx == 0


def _maybe_run_up() -> None:
    if _choose_yes_no("Launch now?", "Run `uv run vasted up` now", "Skip launch for now", default_yes=True):
        os.execv(sys.executable, [sys.executable, "-m", "app.cli", "up"])
    console.print("[cyan]Next: `uv run vasted up`[/cyan]")


def _choose_bind_host(current: str) -> str:
    rows = [
        ["1", "Local only", "Bind to 127.0.0.1 (same machine only)"],
        ["2", "LAN / all interfaces", "Bind to 0.0.0.0 (other devices on your network can connect)"],
    ]
    default_index = 2 if current == "0.0.0.0" else 1
    idx = _choose("Choose proxy bind mode", rows, default_index=default_index)
    return "0.0.0.0" if idx == 1 else "127.0.0.1"


def _pick_model(default_model: str) -> str:
    model_keys = [key for key in featured_model_keys() if key in CURATED_MODELS]
    model_rows = []
    for i, key in enumerate(model_keys, start=1):
        model = CURATED_MODELS[key]
        model_rows.append([str(i), key, f"{model.kind}; {model.description}"])
    model_rows.append(
        [
            str(len(model_rows) + 1),
            "Custom GGUF",
            "Paste a Hugging Face repo, org/repo:file.gguf, or a full HF URL",
        ]
    )
    default_idx = model_keys.index(default_model) + 1 if default_model in model_keys else 1
    idx = _choose("Choose default model", model_rows, default_index=default_idx)
    if idx == len(model_keys):
        while True:
            custom = Prompt.ask("Custom model", default=default_model if "/" in default_model else "")
            try:
                spec = resolve_model(custom, _choose_custom_repo_file)
                return f"{spec.hf_repo}:{spec.filename}"
            except ValueError as exc:
                console.print(f"[yellow]{exc}[/yellow]")
    return model_keys[idx]


def _choose_custom_repo_file(repo: str, filenames: list[str]) -> str:
    recommended = choose_default_gguf_file(filenames)
    if len(filenames) == 1:
        return recommended

    rows = [
        ["1", "Use recommended", f"{recommended} (best default for most setups)"],
        ["2", "Choose a different quant", "Show all available GGUF files"],
    ]
    choice = _choose(f"Choose GGUF for {repo}", rows, default_index=1)
    if choice == 0:
        return recommended

    file_rows = [[str(index), name, "GGUF file"] for index, name in enumerate(filenames, start=1)]
    default_index = filenames.index(recommended) + 1
    idx = _choose(f"Choose GGUF file for {repo}", file_rows, default_index=default_index)
    return filenames[idx]


def _pick_quality(default_quality: str) -> str:
    quality_keys = list(QUALITY_PROFILES.keys())
    rows = [
        [
            str(i + 1),
            key,
            f"{QUALITY_PROFILES[key].context_length // 1024}k context; {QUALITY_PROFILES[key].use_case}",
        ]
        for i, key in enumerate(quality_keys)
    ]
    default_idx = quality_keys.index(default_quality) + 1 if default_quality in quality_keys else 2
    idx = _choose("Choose quality", rows, default_index=default_idx)
    return quality_keys[idx]


def _pick_gpu_mode(default_mode: str) -> str:
    rows = [
        ["1", "Auto (Recommended)", "Pick the cheapest safe GPU for the selected model + context"],
        ["2", "Manual", "Use your chosen GPU preset, but validate it before launch"],
    ]
    default_idx = 2 if default_mode == "manual" else 1
    idx = _choose("Choose GPU mode", rows, default_index=default_idx)
    return "manual" if idx == 1 else "auto"


def _pick_gpu_preset(default_preset: str) -> str:
    gpu_keys = list(GPU_PRESETS.keys())
    rows = [
        [
            str(i + 1),
            GPU_PRESETS[key].name,
            f"{GPU_PRESETS[key].total_vram_gb} GB total VRAM; {GPU_PRESETS[key].typical_price}",
        ]
        for i, key in enumerate(gpu_keys)
    ]
    default_idx = gpu_keys.index(default_preset) + 1 if default_preset in gpu_keys else 1
    idx = _choose("Choose GPU preset", rows, default_index=default_idx)
    return gpu_keys[idx]


def _validate_api_key_loop(cfg_api_key: str, base_url: str) -> str:
    key = cfg_api_key
    while True:
        prompt = "Vast API key"
        if key:
            prompt = "Vast API key (already set; press Enter to keep)"
        key = Prompt.ask(prompt, default=key, password=True, show_default=False)
        try:
            with console.status("Validating Vast API key..."):
                user = VastAPI(key, base_url).validate_api_key()
            identity = user.get("username") or user.get("email") or "unknown"
            console.print(f"[green]✓ Valid API key[/green] ({identity})")
            return key
        except VastAuthError:
            console.print("[yellow]Invalid API key. Please try again.[/yellow]")


def _finalize_gpu_defaults(model_value: str, quality_value: str, fallback_preset: str) -> str:
    try:
        return suggest_gpu_preset(resolve_model(model_value), quality_value)
    except Exception:
        return fallback_preset


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_raw(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed if trimmed else None


def _resolve_llama_jinja(
    *,
    explicit: bool | None,
    env_raw: str | None,
    client_profile: str,
    fallback: bool,
) -> bool:
    if explicit is not None:
        return explicit
    if env_raw is not None:
        value = env_raw.lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        return fallback
    profile_default = CLIENT_PROFILE_JINJA_DEFAULTS.get(client_profile)
    if profile_default is None:
        return fallback
    return profile_default


def _manual_public_host(current: str) -> str:
    while True:
        host = Prompt.ask("Public host or IP for clients (leave blank to skip)", default=current or "")
        host = host.strip()
        if host:
            return host
        if _choose_yes_no("Skip public host?", "Keep it blank for now", "Enter a host or IP", default_yes=True):
            return ""


def _maybe_configure_opencode(cfg: UserConfig) -> None:
    rows = [
        ["1", "Skip", "Do not change local OpenCode config"],
        ["2", "Add provider", "Merge a `vasted` provider into OpenCode config"],
        ["3", "Add provider + default model", "Merge provider and set OpenCode default to this model"],
    ]
    choice = _choose("OpenCode integration", rows, default_index=1)
    if choice == 0:
        return

    context_length = QUALITY_PROFILES[cfg.quality_profile].context_length
    set_default_model = choice == 2
    try:
        write_or_merge_opencode_config(
            OPENCODE_CONFIG_PATH,
            base_url=display_client_base_url(cfg),
            api_key=cfg.bearer_token_plain,
            model=cfg.model,
            context_length=context_length,
            set_default_model=set_default_model,
        )
        console.print(f"[green]Updated OpenCode config[/green] at [cyan]{OPENCODE_CONFIG_PATH}[/cyan]")
    except (OSError, ValueError) as exc:
        console.print(f"[yellow]Could not safely update OpenCode config: {exc}[/yellow]")
        console.print("[bold]OpenCode snippet:[/bold]")
        console.print(
            render_opencode_config(
                base_url=display_client_base_url(cfg),
                api_key=cfg.bearer_token_plain,
                model=cfg.model,
                context_length=context_length,
                set_default_model=set_default_model,
            )
        )


def _print_setup_completion(cfg: UserConfig) -> None:
    console.print("[green]Setup complete[/green]")
    if cfg.gpu_mode == "auto":
        console.print(f"Auto GPU floor: [cyan]{GPU_PRESETS[cfg.gpu_preset].name}[/cyan]")
    console.print(f"Client profile: [cyan]{cfg.client_profile}[/cyan]")
    console.print(f"llama.cpp jinja mode: [cyan]{'enabled' if cfg.llama_server_jinja else 'disabled'}[/cyan]")

    print_client_config(display_client_base_url(cfg), cfg.bearer_token_plain, cfg.model)

    if cfg.deployment_mode == "vps_remote" and not cfg.public_host:
        console.print("[yellow]Replace <your-vps-host> with your VPS IP or domain in your client.[/yellow]")
    if cfg.deployment_mode == "vps_shared":
        console.print(f"Server-local URL: [cyan]{_display_base_url('127.0.0.1', cfg.proxy_port)}[/cyan]")
        if not cfg.public_host:
            console.print("[yellow]Replace <your-vps-host> with your VPS IP or domain for remote devices.[/yellow]")
    if cfg.proxy_host == "0.0.0.0":
        console.print("[yellow]0.0.0.0 is the bind address. Clients should use your real host or IP.[/yellow]")


@click.command()
@click.option("--vast-api-key", default=None)
@click.option("--bearer-token", default=None)
@click.option("--model", "model_opt", default=None)
@click.option("--quality", default=None)
@click.option("--gpu-mode", default=None)
@click.option("--gpu-preset", default=None)
@click.option("--deployment-mode", default=None)
@click.option("--proxy-host", default=None)
@click.option("--proxy-port", type=int, default=None)
@click.option("--public-host", default=None)
@click.option(
    "--client",
    "client_profile",
    type=click.Choice([row[0] for row in CLIENT_PROFILE_ROWS], case_sensitive=False),
    default=None,
    help="Client preset (openclaw/opencode/custom) that sets default jinja behavior.",
)
@click.option("--llama-jinja", "llama_jinja", flag_value=True, default=None)
@click.option("--no-llama-jinja", "llama_jinja", flag_value=False)
@click.option(
    "--manual", is_flag=True, default=False
)
@click.option("--non-interactive", is_flag=True, default=False)
def setup(
    vast_api_key: str | None,
    bearer_token: str | None,
    model_opt: str | None,
    quality: str | None,
    gpu_mode: str | None,
    gpu_preset: str | None,
    deployment_mode: str | None,
    proxy_host: str | None,
    proxy_port: int | None,
    public_host: str | None,
    client_profile: str | None,
    llama_jinja: bool | None,
    manual: bool,
    non_interactive: bool,
) -> None:
    cfg = load_config()

    if non_interactive:
        cfg.vast_api_key_plain = vast_api_key or os.getenv("VASTED_API_KEY", cfg.vast_api_key_plain) or ""
        if not cfg.vast_api_key_plain:
            raise click.ClickException("Vast API key is required. Set --vast-api-key or VASTED_API_KEY.")
        cfg.bearer_token_plain = (
            bearer_token or os.getenv("VASTED_BEARER_TOKEN", cfg.bearer_token_plain) or secrets.token_urlsafe(32)
        )
        cfg.model = model_opt or os.getenv("VASTED_MODEL", cfg.model) or cfg.model
        cfg.quality_profile = quality or os.getenv("VASTED_QUALITY", cfg.quality_profile) or cfg.quality_profile
        cfg.gpu_mode = gpu_mode or os.getenv("VASTED_GPU_MODE", cfg.gpu_mode) or cfg.gpu_mode
        cfg.gpu_preset = gpu_preset or os.getenv("VASTED_GPU_PRESET", cfg.gpu_preset) or cfg.gpu_preset
        mode_value = deployment_mode or os.getenv("VASTED_DEPLOYMENT_MODE", cfg.deployment_mode) or cfg.deployment_mode
        if manual:
            mode_value = "manual"
        if mode_value not in {row[0] for row in DEPLOYMENT_MODE_ROWS}:
            raise click.ClickException(f"Unknown deployment mode: {mode_value}")
        if mode_value != "manual":
            apply_deployment_mode_defaults(cfg, mode_value)
        else:
            cfg.deployment_mode = "manual"
        cfg.proxy_host = proxy_host or os.getenv("VASTED_PROXY_HOST", cfg.proxy_host) or cfg.proxy_host
        cfg.proxy_port = proxy_port or int(os.getenv("VASTED_PROXY_PORT", str(cfg.proxy_port)))
        cfg.public_host = public_host or os.getenv("VASTED_PUBLIC_HOST", cfg.public_host) or cfg.public_host
        cfg.client_profile = _normalize_client_profile(
            client_profile or _env_raw("VASTED_CLIENT") or cfg.client_profile,
            default=cfg.client_profile,
        )
        cfg.llama_server_jinja = _resolve_llama_jinja(
            explicit=llama_jinja,
            env_raw=_env_raw("VASTED_LLAMA_JINJA"),
            client_profile=cfg.client_profile,
            fallback=cfg.llama_server_jinja,
        )
        if not (1024 <= int(cfg.proxy_port) <= 65535):
            raise click.ClickException("Port must be 1024-65535")
        if cfg.gpu_mode not in {"auto", "manual"}:
            raise click.ClickException("GPU mode must be auto or manual")
        if cfg.gpu_mode == "auto":
            cfg.gpu_preset = _finalize_gpu_defaults(cfg.model, cfg.quality_profile, cfg.gpu_preset)
        ensure_bearer_token(cfg, override=bearer_token or os.getenv("VASTED_BEARER_TOKEN"))
        save_config(cfg)
        console.print("[green]Saved configuration (non-interactive).[/green]")
        _print_setup_completion(cfg)
        return

    console.print(Panel.fit("[bold cyan]Vasted Setup Wizard[/bold cyan]", border_style="cyan"))

    mode = "manual" if manual else _pick_deployment_mode(cfg.deployment_mode)
    cfg.vast_api_key_plain = _validate_api_key_loop(cfg.vast_api_key_plain, cfg.vast_base_url)
    cfg.model = _pick_model(cfg.model if cfg.model in CURATED_MODELS else "qwen3-coder-30b")
    cfg.quality_profile = _pick_quality(cfg.quality_profile)
    cfg.gpu_mode = _pick_gpu_mode(cfg.gpu_mode)
    if cfg.gpu_mode == "manual":
        cfg.gpu_preset = _pick_gpu_preset(cfg.gpu_preset)
    else:
        cfg.gpu_preset = _finalize_gpu_defaults(cfg.model, cfg.quality_profile, cfg.gpu_preset)
    cfg.client_profile = (
        _normalize_client_profile(client_profile, default=cfg.client_profile)
        if client_profile is not None
        else _pick_client_profile(cfg.client_profile)
    )
    if llama_jinja is not None:
        cfg.llama_server_jinja = llama_jinja
    elif cfg.client_profile == "custom":
        cfg.llama_server_jinja = (
            Prompt.ask(
                "Enable llama.cpp --jinja chat template mode?",
                choices=["yes", "no"],
                default="yes" if cfg.llama_server_jinja else "no",
            )
            == "yes"
        )
    else:
        cfg.llama_server_jinja = bool(CLIENT_PROFILE_JINJA_DEFAULTS[cfg.client_profile])

    if mode == "manual":
        cfg.deployment_mode = "manual"
        cfg.proxy_host = proxy_host or _choose_bind_host(cfg.proxy_host)
        cfg.proxy_port = _ask_port(proxy_port or int(cfg.proxy_port))
        if cfg.proxy_host == "0.0.0.0":
            cfg.public_host = public_host if public_host is not None else _manual_public_host(cfg.public_host)
        else:
            cfg.public_host = ""
    else:
        apply_deployment_mode_defaults(cfg, mode)
        if mode == "local_pc":
            cfg.public_host = ""
        elif public_host is not None:
            cfg.public_host = public_host

    ensure_bearer_token(cfg)

    if mode == "manual" and Prompt.ask("Configure Telegram bot?", choices=["no", "yes"], default="no") == "yes":
        cfg.telegram_token_plain = (
            Prompt.ask("Telegram token", default=cfg.telegram_token_plain or "", password=True) or None
        )
        cfg.telegram_chat_id = Prompt.ask("Telegram chat/user ID", default=cfg.telegram_chat_id or "") or None

    save_config(cfg)
    _print_setup_completion(cfg)
    if cfg.deployment_mode == "local_pc":
        _maybe_configure_opencode(cfg)
    _maybe_run_up()
