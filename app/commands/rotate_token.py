from __future__ import annotations

import click

from app.commands.token import rotate_bearer_token


@click.command(name="rotate-token")
def rotate_token() -> None:
    cfg = rotate_bearer_token()
    click.echo(cfg.bearer_token_plain)
