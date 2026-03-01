from __future__ import annotations

import click

from app.commands.common import console
from app.commands.setup import display_client_base_url
from app.user_config import UserConfig, load_config


def format_config_summary(cfg: UserConfig) -> str:
    lines = [
        f"Deployment mode: {cfg.deployment_mode}",
        f"Bind host: {cfg.proxy_host}",
        f"Port: {cfg.proxy_port}",
    ]
    if cfg.public_host:
        lines.append(f"Public host: {cfg.public_host}")
    lines.extend(
        [
            f"Base URL: {display_client_base_url(cfg)}",
            f"Client profile: {cfg.client_profile}",
            f"Model: {cfg.model}",
            f"Quality: {cfg.quality_profile}",
            f"GPU mode: {cfg.gpu_mode}",
            f"GPU preset: {cfg.gpu_preset}",
            f"Llama jinja: {'enabled' if cfg.llama_server_jinja else 'disabled'}",
        ]
    )
    return "\n".join(lines)


def render_config_summary(cfg: UserConfig) -> None:
    console.print(format_config_summary(cfg))


@click.group(name="config")
def config() -> None:
    """Inspect saved configuration."""


@config.command("show")
def show_config() -> None:
    render_config_summary(load_config())
