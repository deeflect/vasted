from __future__ import annotations

import secrets

import click

from app.commands.common import console, print_client_config
from app.commands.setup import display_client_base_url
from app.user_config import UserConfig, load_config, save_config


def rotate_bearer_token() -> UserConfig:
    cfg = load_config()
    cfg.bearer_token_plain = secrets.token_urlsafe(32)
    save_config(cfg)
    return cfg


@click.group(name="token")
def token() -> None:
    """Inspect or rotate the proxy bearer token."""


@token.command("show")
def show_token() -> None:
    cfg = load_config()
    click.echo(cfg.bearer_token_plain)


@token.command("rotate")
def rotate_token_command() -> None:
    cfg = rotate_bearer_token()
    console.print("[green]Rotated bearer token[/green]")
    print_client_config(display_client_base_url(cfg), cfg.bearer_token_plain, cfg.model)
