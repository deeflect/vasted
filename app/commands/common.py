from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

console = Console()


def format_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def error_panel(title: str, message: str) -> None:
    console.print(Panel.fit(message, title=f"[red]{title}[/red]", border_style="red"))


def print_client_config(base_url: str, api_key: str, model: str) -> None:
    console.print("\n[bold]━━━ Add to your client config ━━━[/bold]")
    console.print(f"Base URL: {base_url}")
    console.print(f"API Key:  {api_key}")
    console.print(f"Model:    {model}")
