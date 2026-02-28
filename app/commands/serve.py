from __future__ import annotations

import click

from app.commands.common import console, error_panel
from app.proxy import run_proxy
from app.service import require_config


@click.command()
@click.option("--watchdog", is_flag=True, default=False)
@click.option("--log-file", default=None)
def serve(watchdog: bool, log_file: str | None) -> None:
    try:
        cfg = require_config()
    except RuntimeError as exc:
        error_panel("Missing config", str(exc))
        raise click.ClickException(str(exc)) from exc
    console.print(f"[cyan]Proxy listening on http://{cfg.proxy_host}:{cfg.proxy_port}/v1[/cyan]")
    run_proxy(cfg.proxy_host, cfg.proxy_port, watchdog=watchdog, log_file=log_file)
