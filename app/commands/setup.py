from __future__ import annotations

import os
import secrets

import click
import httpx
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from app.commands.common import console, print_client_config
from app.config import CURATED_MODELS, GPU_PRESETS, QUALITY_PROFILES
from app.models import resolve_model, suggest_gpu_preset
from app.user_config import load_config, save_config
from app.vast import VastAPI, VastAuthError


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


def _detect_public_ip() -> str | None:
    try:
        r = httpx.get("https://api.ipify.org", timeout=8.0)
        r.raise_for_status()
        return r.text.strip()
    except Exception:
        return None


def _pick_model(default_model: str) -> str:
    model_keys = list(CURATED_MODELS.keys())
    model_rows = [[str(i + 1), k, CURATED_MODELS[k].description] for i, k in enumerate(model_keys)]
    model_rows.append([str(len(model_rows) + 1), "Custom", "org/repo:file.gguf or HF URL"])
    default_idx = model_keys.index(default_model) + 1 if default_model in model_keys else 1
    idx = _choose("Choose default model", model_rows, default_index=default_idx)
    if idx == len(model_keys):
        while True:
            custom = Prompt.ask("Custom model")
            try:
                resolve_model(custom)
                return custom
            except ValueError as exc:
                console.print(f"[yellow]{exc}[/yellow]")
    return model_keys[idx]


def _validate_api_key_loop(cfg_api_key: str, base_url: str) -> str:
    key = cfg_api_key
    while True:
        key = Prompt.ask("Vast API key", default=key, password=True)
        try:
            with console.status("Validating Vast API key..."):
                user = VastAPI(key, base_url).validate_api_key()
            console.print(f"[green]✓ Valid API key[/green] ({user.get('username') or user.get('email') or 'unknown'})")
            return key
        except VastAuthError:
            console.print("[yellow]Invalid API key. Please try again.[/yellow]")


@click.command()
@click.option("--vast-api-key", default=None)
@click.option("--bearer-token", default=None)
@click.option("--model", "model_opt", default=None)
@click.option("--quality", default=None)
@click.option("--gpu-preset", default=None)
@click.option("--proxy-host", default=None)
@click.option("--proxy-port", type=int, default=None)
@click.option("--non-interactive", is_flag=True, default=False)
def setup(
    vast_api_key: str | None,
    bearer_token: str | None,
    model_opt: str | None,
    quality: str | None,
    gpu_preset: str | None,
    proxy_host: str | None,
    proxy_port: int | None,
    non_interactive: bool,
) -> None:
    cfg = load_config()

    if non_interactive:
        cfg.vast_api_key_plain = vast_api_key or os.getenv("VASTED_API_KEY", cfg.vast_api_key_plain) or ""
        cfg.bearer_token_plain = (
            bearer_token or os.getenv("VASTED_BEARER_TOKEN", cfg.bearer_token_plain) or secrets.token_urlsafe(32)
        )
        cfg.model = model_opt or os.getenv("VASTED_MODEL", cfg.model) or cfg.model
        cfg.quality_profile = quality or os.getenv("VASTED_QUALITY", cfg.quality_profile) or cfg.quality_profile
        cfg.gpu_preset = gpu_preset or os.getenv("VASTED_GPU_PRESET", cfg.gpu_preset) or cfg.gpu_preset
        cfg.proxy_host = proxy_host or os.getenv("VASTED_PROXY_HOST", cfg.proxy_host) or cfg.proxy_host
        cfg.proxy_port = proxy_port or int(os.getenv("VASTED_PROXY_PORT", str(cfg.proxy_port)))
        if not (1024 <= int(cfg.proxy_port) <= 65535):
            raise click.ClickException("Port must be 1024-65535")
        save_config(cfg)
        console.print("[green]Saved configuration (non-interactive).[/green]")
        print_client_config(f"http://{cfg.proxy_host}:{cfg.proxy_port}/v1", cfg.bearer_token_plain, cfg.model)
        return

    console.print(Panel.fit("[bold cyan]Vasted Setup Wizard[/bold cyan]", border_style="cyan"))

    mode = Prompt.ask("Setup mode", choices=["express", "advanced"], default="express")
    cfg.vast_api_key_plain = _validate_api_key_loop(cfg.vast_api_key_plain, cfg.vast_base_url)

    if mode == "express":
        cfg.model = _pick_model("qwen3-8b")
        model_spec = resolve_model(cfg.model)
        cfg.gpu_preset = suggest_gpu_preset(model_spec)
        cfg.quality_profile = "balanced"
        cfg.proxy_host = "127.0.0.1"
        cfg.proxy_port = 4318
        cfg.bearer_token_plain = secrets.token_urlsafe(32)
        save_config(cfg)
        console.print("[green]Express setup complete[/green]")
        console.print(f"Auto-picked GPU preset: [cyan]{GPU_PRESETS[cfg.gpu_preset].name}[/cyan]")
        print_client_config(f"http://{cfg.proxy_host}:{cfg.proxy_port}/v1", cfg.bearer_token_plain, cfg.model)
        return

    detected = _detect_public_ip() or cfg.proxy_host
    cfg.proxy_host = Prompt.ask("Proxy host", default=proxy_host or detected)
    cfg.proxy_port = _ask_port(proxy_port or int(cfg.proxy_port))
    cfg.bearer_token_plain = Prompt.ask("Proxy bearer token", default=secrets.token_urlsafe(32), password=True)

    cfg.model = _pick_model(cfg.model)

    gpu_keys = list(GPU_PRESETS.keys())
    cfg.gpu_preset = gpu_keys[
        _choose(
            "Choose GPU preset",
            [[str(i + 1), GPU_PRESETS[k].name, GPU_PRESETS[k].typical_price] for i, k in enumerate(gpu_keys)],
        )
    ]

    quality_keys = list(QUALITY_PROFILES.keys())
    cfg.quality_profile = quality_keys[
        _choose(
            "Choose quality",
            [[str(i + 1), k, QUALITY_PROFILES[k].use_case] for i, k in enumerate(quality_keys)],
            2,
        )
    ]

    if Confirm.ask("Configure Telegram bot?", default=bool(cfg.telegram_chat_id)):
        cfg.telegram_token_plain = (
            Prompt.ask("Telegram token", default=cfg.telegram_token_plain or "", password=True) or None
        )
        cfg.telegram_chat_id = Prompt.ask("Telegram chat/user ID", default=cfg.telegram_chat_id or "") or None

    save_config(cfg)
    console.print("[green]Advanced setup complete[/green]")
    print_client_config(f"http://{cfg.proxy_host}:{cfg.proxy_port}/v1", cfg.bearer_token_plain, cfg.model)
