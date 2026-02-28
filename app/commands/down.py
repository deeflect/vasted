from __future__ import annotations

import click
from rich.panel import Panel
from rich.table import Table

from app.commands.common import console, error_panel, format_duration
from app.service import stop_worker
from app.vast import VastAPIError, VastAuthError


@click.command()
@click.option("--force", is_flag=True, default=False)
def down(force: bool) -> None:
    try:
        result = stop_worker(force=force)
        u = result.usage
        t = Table(show_header=False)
        t.add_row("Requests", f"{u.requests:,}")
        t.add_row("Input tokens", f"{u.input_tokens:,}")
        t.add_row("Output tokens", f"{u.output_tokens:,}")
        t.add_row("Session duration", format_duration(u.duration_seconds))
        t.add_row("Estimated cost", f"${result.billing.estimated_cost:.4f}")
        t.add_row(
            "Billed cost",
            f"${result.billing.billed_cost:.4f}" if result.billing.billed_cost is not None else "unavailable",
        )
        console.print(Panel(t, title="[green]Worker destroyed[/green]", border_style="green"))
    except (RuntimeError, VastAuthError, VastAPIError) as exc:
        error_panel("Failed to destroy instance", str(exc))
        raise click.ClickException(str(exc)) from exc
