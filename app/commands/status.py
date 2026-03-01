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
    is_ready = bool(s.worker_url)
    t = Table(show_header=False)
    t.add_row("State", "ready" if is_ready else "starting")
    t.add_row("Model", str(s.model_name or "unknown"))
    t.add_row("Instance ID", str(s.instance_id))
    if verbose:
        t.add_row("Worker URL", str(s.worker_url or "not assigned yet"))
    t.add_row("Price/hr", f"${s.price_per_hour:.4f}")
    t.add_row("Uptime", format_duration(time.time() - (s.started_at or time.time())))
    t.add_row("Proxy endpoint", f"http://{cfg.proxy_host}:{cfg.proxy_port}/v1")
    title = "Vasted Status"
    border_style = "green" if is_ready else "yellow"
    console.print(Panel(t, title=title, border_style=border_style))
    if not is_ready:
        console.print("[dim]Use `vasted logs` to inspect remote startup progress.[/dim]")
