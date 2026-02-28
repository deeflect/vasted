from __future__ import annotations

import click
from rich.table import Table

from app.commands.common import console, format_duration
from app.service import get_usage


@click.command()
def usage() -> None:
    u = get_usage()
    t = Table(title="Usage")
    t.add_column("Metric")
    t.add_column("Value")
    t.add_row("Requests", f"{u.requests:,}")
    t.add_row("Input tokens", f"{u.input_tokens:,}")
    t.add_row("Output tokens", f"{u.output_tokens:,}")
    t.add_row("Duration", format_duration(u.duration_seconds))
    t.add_row("Total cost", f"${u.total_cost:.4f}")
    t.add_row("$/1M tokens", f"${u.dollars_per_million_tokens:.2f}")
    console.print(t)
