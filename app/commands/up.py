from __future__ import annotations

import click
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from app.commands.common import console, error_panel, print_client_config
from app.config import GPU_PRESETS
from app.proxy import ensure_proxy_running
from app.service import InventoryCheck, check_inventory, prepare_launch, require_config, start_worker
from app.vast import VastAPIError, VastAuthError


def _display_base_url(host: str, port: int) -> str:
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{display_host}:{port}/v1"


def _inventory_message(inventory: InventoryCheck, minimum_gpu_preset: str) -> str:
    raw_capacity = [attempt for attempt in inventory.attempts if attempt.relaxed_count > 0]
    if raw_capacity:
        details = ", ".join(
            f"{GPU_PRESETS[attempt.gpu_preset].name}: {attempt.relaxed_count} raw / {attempt.strict_count} safe"
            for attempt in raw_capacity
        )
        return (
            "Raw Vast capacity exists, but none of it meets the current reliability/network floor. "
            f"Current matches: {details}."
        )
    attempted = (
        ", ".join(GPU_PRESETS[attempt.gpu_preset].name for attempt in inventory.attempts)
        or GPU_PRESETS[minimum_gpu_preset].name
    )
    return f"No safe inventory matched for {attempted}."


def _print_inventory_summary(inventory: InventoryCheck, minimum_gpu_preset: str) -> None:
    table = Table(title="Vast Preflight")
    table.add_column("GPU")
    table.add_column("Safe")
    table.add_column("Raw")
    table.add_column("Best $/hr")
    for attempt in inventory.attempts:
        best = "-" if attempt.best_price is None else f"${attempt.best_price:.4f}"
        table.add_row(
            GPU_PRESETS[attempt.gpu_preset].name,
            str(attempt.strict_count),
            str(attempt.relaxed_count),
            best,
        )
    console.print(table)
    if inventory.selected_gpu_preset and inventory.selected_gpu_preset != minimum_gpu_preset:
        console.print(
            "[yellow]"
            f"No safe {GPU_PRESETS[minimum_gpu_preset].name} offers right now. "
            f"Escalating to {GPU_PRESETS[inventory.selected_gpu_preset].name}."
            "[/yellow]"
        )
    elif not inventory.offers:
        console.print(f"[yellow]{_inventory_message(inventory, minimum_gpu_preset)}[/yellow]")


@click.command()
@click.option("--model", "model_override", default=None)
@click.option("--quality", "quality_override", default=None)
@click.option("--gpu-mode", "gpu_mode_override", default=None)
@click.option("--gpu-preset", "gpu_preset_override", default=None)
@click.option("--profile", "profile_name", default=None)
@click.option("--max-price", type=float, default=None)
@click.option("--force", is_flag=True, default=False)
@click.option("--serve/--no-serve", "auto_serve", default=True)
def up(
    model_override: str | None,
    quality_override: str | None,
    gpu_mode_override: str | None,
    gpu_preset_override: str | None,
    profile_name: str | None,
    max_price: float | None,
    force: bool,
    auto_serve: bool,
) -> None:
    try:
        cfg = require_config()
        if profile_name:
            p = cfg.model_profiles.get(profile_name)
            if not p:
                raise click.ClickException(f"Unknown profile: {profile_name}")
            model_override = p["model"]
            quality_override = p["quality_profile"]
            gpu_preset_override = p["gpu_preset"]
            gpu_mode_override = "manual"

        plan = prepare_launch(
            model_override=model_override,
            quality_override=quality_override,
            gpu_mode_override=gpu_mode_override,
            gpu_preset_override=gpu_preset_override,
        )

        inventory = check_inventory(plan, limit=10)
        preview_offers = inventory.offers
        preview_preset = inventory.selected_gpu_preset or plan.selected_gpu_preset

        summary = Table(show_header=False)
        summary.add_row("Model", plan.model_spec.name)
        summary.add_row("Quality", f"{plan.quality_profile} ({plan.target_context // 1024}k)")
        summary.add_row("GPU mode", plan.gpu_mode)
        summary.add_row("Minimum safe GPU", GPU_PRESETS[plan.selected_gpu_preset].name)
        summary.add_row("Sizing", f"{plan.model_size_gb:.1f} GB model, ~{plan.required_vram_gb:.1f} GB VRAM")
        console.print(Panel(summary, title="Launch Plan", border_style="cyan"))
        _print_inventory_summary(inventory, plan.selected_gpu_preset)

        if preview_offers:
            top = preview_offers[:3]
            table = Table(title=f"Top 3 offers ({GPU_PRESETS[preview_preset].name})")
            table.add_column("#")
            table.add_column("Offer ID")
            table.add_column("GPU")
            table.add_column("$/hr")
            for i, offer in enumerate(top, start=1):
                table.add_row(
                    str(i),
                    str(offer.get("id") or offer.get("ask_id") or offer.get("ask_contract_id") or "?"),
                    str(offer.get("gpu_name") or GPU_PRESETS[preview_preset].search),
                    f"${float(offer.get('dph_total') or offer.get('dph') or 0.0):.4f}",
                )
            console.print(table)
            best_price = float(top[0].get("dph_total") or top[0].get("dph") or 0.0)
            budget = max_price if max_price is not None else cfg.max_hourly_cost
            if (
                budget > 0
                and best_price > budget
                and not Confirm.ask(
                    f"Best offer is ${best_price:.4f}/hr above max ${budget:.4f}. Continue?", default=False
                )
            ):
                raise click.ClickException("Cancelled")
        else:
            message = _inventory_message(inventory, plan.selected_gpu_preset)
            error_panel("No safe inventory", message)
            raise click.ClickException(message)

        if auto_serve:
            with console.status("Ensuring local proxy is running..."):
                started_proxy = ensure_proxy_running(cfg.proxy_host, cfg.proxy_port)
            if started_proxy:
                console.print(f"[green]Proxy started at {_display_base_url(cfg.proxy_host, cfg.proxy_port)}[/green]")
            else:
                console.print(
                    f"[dim]Proxy already running at {_display_base_url(cfg.proxy_host, cfg.proxy_port)}[/dim]"
                )

        console.print(
            "[cyan]Launching worker. First boot can take a few minutes while Vast pulls the image and model.[/cyan]"
        )
        with console.status("Preparing worker...") as status:
            result = start_worker(force=force, progress=status.update, launch_plan=plan, inventory_check=inventory)
        panel = Table(show_header=False)
        panel.add_row("Instance", str(result.instance_id))
        panel.add_row("Model", result.model)
        panel.add_row("Quality", f"{result.quality_profile} ({result.target_context // 1024}k)")
        panel.add_row("GPU", GPU_PRESETS[result.gpu_preset].name)
        panel.add_row("Price", f"${result.price_per_hour:.4f}/hr")
        panel.add_row("Proxy endpoint", _display_base_url(cfg.proxy_host, cfg.proxy_port))
        console.print(Panel(panel, title="[green]Worker ready[/green]", border_style="green"))
        print_client_config(_display_base_url(cfg.proxy_host, cfg.proxy_port), cfg.bearer_token_plain, result.model)
    except (RuntimeError, TimeoutError, VastAuthError, VastAPIError, ValueError) as exc:
        error_panel("Failed to start worker", str(exc))
        raise click.ClickException(str(exc)) from exc
