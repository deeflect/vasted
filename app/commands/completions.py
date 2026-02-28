from __future__ import annotations

import os

import click


@click.command(name="completions")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completions(shell: str) -> None:
    mode = {"bash": "bash_source", "zsh": "zsh_source", "fish": "fish_source"}[shell]
    click.echo(os.popen(f"_VASTED_COMPLETE={mode} vasted").read())
