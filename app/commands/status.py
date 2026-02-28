from __future__ import annotations

import time

import click
from rich.panel import Panel
from rich.table import Table

from app.commands.common import console, format_duration
from app.service import get_status
from app.user_config import load_config


@click.command()
@click.option("--verbose", is_flag=True, default=False)
def status(verbose: bool) -> None:
    cfg = load_config()
    s = get_status()
    if not s.instance_id:
        console.print(Panel.fit("No active worker", border_style="yellow"))
        return
    t = Table(show_header=False)
    t.add_row("Model", str(s.model_name or "unknown"))
    t.add_row("Instance ID", str(s.instance_id))
    if verbose:
        t.add_row("Worker URL", str(s.worker_url or "unknown"))
    t.add_row("Price/hr", f"${s.price_per_hour:.4f}")
    t.add_row("Uptime", format_duration(time.time() - (s.started_at or time.time())))
    t.add_row("Proxy endpoint", f"http://{cfg.proxy_host}:{cfg.proxy_port}/v1")
    console.print(Panel(t, title="Vasted Status", border_style="cyan"))
