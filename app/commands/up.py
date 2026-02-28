from __future__ import annotations

import click
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from app.commands.common import console, error_panel
from app.config import GPU_PRESETS
from app.service import require_config, start_worker
from app.vast import VastAPI, VastAPIError, VastAuthError


@click.command()
@click.option("--model", "model_override", default=None)
@click.option("--profile", "profile_name", default=None)
@click.option("--max-price", type=float, default=None)
@click.option("--force", is_flag=True, default=False)
def up(model_override: str | None, profile_name: str | None, max_price: float | None, force: bool) -> None:
    try:
        cfg = require_config()
        if profile_name:
            p = cfg.model_profiles.get(profile_name)
            if not p:
                raise click.ClickException(f"Unknown profile: {profile_name}")
            cfg.model = p["model"]
            cfg.quality_profile = p["quality_profile"]
            cfg.gpu_preset = p["gpu_preset"]

        offers = VastAPI(cfg.vast_api_key_plain, cfg.vast_base_url).search_offers(
            cfg.gpu_preset, instance_type=cfg.instance_type
        )
        if not offers:
            raise click.ClickException("No matching Vast offers found")

        top = offers[:3]
        table = Table(title="Top 3 offers")
        table.add_column("#")
        table.add_column("Offer ID")
        table.add_column("GPU")
        table.add_column("$/hr")
        for i, offer in enumerate(top, start=1):
            table.add_row(
                str(i),
                str(offer.get("id") or offer.get("ask_id") or offer.get("ask_contract_id") or "?"),
                str(offer.get("gpu_name") or GPU_PRESETS[cfg.gpu_preset].search),
                f"${float(offer.get('dph_total') or offer.get('dph') or 0.0):.4f}",
            )
        console.print(table)

        best_price = float(top[0].get("dph_total") or top[0].get("dph") or 0.0)
        budget = max_price if max_price is not None else cfg.max_hourly_cost
        if (
            budget > 0
            and best_price > budget
            and not Confirm.ask(f"Best offer is ${best_price:.4f}/hr above max ${budget:.4f}. Continue?", default=False)
        ):
            raise click.ClickException("Cancelled")

        result = start_worker(model_override=model_override, force=force)
        panel = Table(show_header=False)
        panel.add_row("Instance", str(result.instance_id))
        panel.add_row("Model", result.model)
        panel.add_row("Price", f"${result.price_per_hour:.4f}/hr")
        panel.add_row("Proxy endpoint", f"http://{cfg.proxy_host}:{cfg.proxy_port}/v1")
        console.print(Panel(panel, title="[green]Worker ready[/green]", border_style="green"))
    except (RuntimeError, TimeoutError, VastAuthError, VastAPIError, ValueError) as exc:
        error_panel("Failed to start worker", str(exc))
        raise click.ClickException(str(exc)) from exc
