from __future__ import annotations

import click
from rich.panel import Panel

from app.commands.common import console, error_panel
from app.service import get_status, require_config
from app.vast import VastAPI, VastAPIError, VastAuthError


@click.command()
@click.option("--instance-id", type=int, default=None, help="Fetch logs for a specific Vast instance")
@click.option("--tail", type=int, default=80, show_default=True, help="Show only the last N lines (0 = all)")
def logs(instance_id: int | None, tail: int) -> None:
    try:
        if tail < 0:
            raise click.ClickException("--tail must be 0 or greater")

        cfg = require_config()
        state = get_status()
        target_instance = instance_id or state.instance_id
        if not target_instance:
            raise click.ClickException("No active worker. Pass --instance-id to fetch logs for a specific instance.")

        api = VastAPI(cfg.vast_api_key_plain, base_url=cfg.vast_base_url)
        with console.status(f"Fetching logs for instance {target_instance}..."):
            log_text = api.get_instance_logs(target_instance)

        lines = log_text.splitlines()
        if tail > 0:
            lines = lines[-tail:]

        title = f"Vast Logs #{target_instance}"
        if not lines:
            console.print(Panel.fit("No log lines returned", title=title, border_style="yellow"))
            return

        shown = "all lines" if tail == 0 else f"last {len(lines)} lines"
        console.print(Panel.fit(shown, title=title, border_style="cyan"))
        for line in lines:
            console.print(line, markup=False, highlight=False)
    except VastAPIError as exc:
        if "log object not ready yet" in str(exc):
            console.print(
                Panel.fit(
                    "Vast has not published the log file yet. Retry in a few seconds.",
                    title="[yellow]Logs Not Ready[/yellow]",
                    border_style="yellow",
                )
            )
            return
        error_panel("Failed to fetch logs", str(exc))
        raise click.ClickException(str(exc)) from exc
    except (RuntimeError, VastAuthError) as exc:
        error_panel("Failed to fetch logs", str(exc))
        raise click.ClickException(str(exc)) from exc
