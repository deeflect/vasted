from __future__ import annotations

import secrets

import click

from app.user_config import load_config, save_config


@click.command(name="rotate-token")
def rotate_token() -> None:
    cfg = load_config()
    cfg.bearer_token_plain = secrets.token_urlsafe(32)
    save_config(cfg)
    click.echo(cfg.bearer_token_plain)
