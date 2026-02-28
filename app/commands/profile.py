from __future__ import annotations

import click
from rich.table import Table

from app.commands.common import console
from app.models import resolve_model
from app.user_config import load_config, save_config


@click.group(name="profile")
def profile() -> None:
    """Manage model profiles."""


@profile.command("list")
def list_profiles() -> None:
    cfg = load_config()
    table = Table(title="Profiles")
    table.add_column("Name")
    table.add_column("Model")
    table.add_column("Quality")
    table.add_column("GPU")
    for name, p in cfg.model_profiles.items():
        table.add_row(name, p.get("model", ""), p.get("quality_profile", ""), p.get("gpu_preset", ""))
    console.print(table)


@profile.command("add")
@click.argument("name")
@click.option("--model", required=True)
@click.option("--quality", "quality_profile", required=True)
@click.option("--gpu-preset", required=True)
def add_profile(name: str, model: str, quality_profile: str, gpu_preset: str) -> None:
    _ = resolve_model(model)
    cfg = load_config()
    cfg.model_profiles[name] = {"model": model, "quality_profile": quality_profile, "gpu_preset": gpu_preset}
    save_config(cfg)
    console.print(f"[green]Saved profile {name}[/green]")


@profile.command("use")
@click.argument("name")
def use_profile(name: str) -> None:
    cfg = load_config()
    p = cfg.model_profiles.get(name)
    if not p:
        raise click.ClickException(f"Unknown profile: {name}")
    cfg.model = p["model"]
    cfg.quality_profile = p["quality_profile"]
    cfg.gpu_preset = p["gpu_preset"]
    save_config(cfg)
    console.print(f"[green]Using profile {name}[/green]")


@profile.command("remove")
@click.argument("name")
def remove_profile(name: str) -> None:
    cfg = load_config()
    if name not in cfg.model_profiles:
        raise click.ClickException(f"Unknown profile: {name}")
    del cfg.model_profiles[name]
    save_config(cfg)
    console.print(f"[green]Removed profile {name}[/green]")
