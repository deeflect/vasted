from __future__ import annotations

import click

from app import __version__
from app.commands import completions, down, profile, rotate_token, serve, setup, status, up, usage


@click.group()
@click.version_option(__version__)
def cli() -> None:
    """vasted - personal Vast.ai launcher + local OpenAI-compatible proxy"""


cli.add_command(setup)
cli.add_command(serve)
cli.add_command(up)
cli.add_command(down)
cli.add_command(status)
cli.add_command(usage)
cli.add_command(profile)
cli.add_command(rotate_token)
cli.add_command(completions)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
