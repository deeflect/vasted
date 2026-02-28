from __future__ import annotations

import time
from dataclasses import dataclass

from app.models import ModelSpec, resolve_model
from app.state import RuntimeState, clear_state, load_state, save_state
from app.usage import UsageSummary, reset_usage_for_new_session, summarize_usage
from app.user_config import UserConfig, config_exists, load_config
from app.vast import BillingInfo, VastAPI


@dataclass(slots=True)
class UpResult:
    instance_id: int
    worker_url: str
    model: str
    price_per_hour: float


@dataclass(slots=True)
class DownResult:
    usage: UsageSummary
    billing: BillingInfo


def require_config() -> UserConfig:
    if not config_exists():
        raise RuntimeError("Run `vasted setup` first")
    cfg = load_config()
    if not cfg.vast_api_key_plain or not cfg.bearer_token_plain:
        raise RuntimeError("Incomplete config. Run `vasted setup` first")
    return cfg


def _api(cfg: UserConfig) -> VastAPI:
    return VastAPI(cfg.vast_api_key_plain, base_url=cfg.vast_base_url)


def start_worker(model_override: str | None = None, force: bool = False) -> UpResult:
    cfg = require_config()
    state = load_state()
    if state.instance_id and not force:
        raise RuntimeError("Worker already active. Run `vasted down` first, or pass --force")
    if state.instance_id and force:
        stop_worker(force=True)

    model_spec: ModelSpec = resolve_model(model_override or cfg.model)
    api = _api(cfg)
    offers = api.search_offers(cfg.gpu_preset, instance_type=cfg.instance_type)
    if not offers:
        raise RuntimeError("No matching Vast offers found")

    best = offers[0]
    offer_id_raw = best.get("id") or best.get("ask_id") or best.get("ask_contract_id")
    if offer_id_raw is None:
        raise RuntimeError(f"Malformed Vast offer (missing id): {best}")
    offer_id = int(offer_id_raw)
    price = float(best.get("dph_total") or best.get("dph") or 0.0)

    instance_id = api.create_instance(
        offer_id,
        model_spec,
        cfg.quality_profile,
        cfg.gpu_preset,
        image=cfg.llama_cpp_image,
        api_token=cfg.bearer_token_plain,
    )
    try:
        worker_url = api.wait_for_ready(instance_id, api_token=cfg.bearer_token_plain)
    except TimeoutError as exc:
        api.destroy_instance(instance_id)
        clear_state()
        raise TimeoutError(
            f"Worker readiness timed out; instance {instance_id} was automatically destroyed and state cleared"
        ) from exc

    now = time.time()
    save_state(
        RuntimeState(
            instance_id=instance_id,
            worker_url=worker_url,
            model_name=model_spec.name,
            started_at=now,
            price_per_hour=price,
            session_start=now,
            last_request_at=now,
        )
    )
    reset_usage_for_new_session(price)
    return UpResult(instance_id=instance_id, worker_url=worker_url, model=model_spec.name, price_per_hour=price)


def stop_worker(force: bool = False) -> DownResult:
    cfg = require_config()
    state = load_state()
    usage = summarize_usage()
    billing = BillingInfo(estimated_cost=usage.total_cost, billed_cost=None)
    if state.instance_id:
        api = _api(cfg)
        billing = api.get_billing(state.instance_id, usage.total_cost)
        if force:
            try:
                api.destroy_instance(state.instance_id)
            except Exception:
                pass
        else:
            api.destroy_instance(state.instance_id)
    clear_state()
    return DownResult(usage=usage, billing=billing)


def get_status() -> RuntimeState:
    return load_state()


def get_usage() -> UsageSummary:
    return summarize_usage()


def touch_last_request() -> None:
    s = load_state()
    s.last_request_at = time.time()
    save_state(s)


def maybe_auto_shutdown_idle() -> bool:
    cfg = load_config()
    s = load_state()
    if not s.instance_id:
        return False
    if not s.last_request_at:
        return False
    idle_s = max(0, cfg.idle_timeout_minutes) * 60
    if idle_s <= 0:
        return False
    if (time.time() - s.last_request_at) >= idle_s:
        stop_worker(force=True)
        return True
    return False


def check_budget_and_maybe_shutdown() -> tuple[bool, str | None]:
    cfg = load_config()
    u = summarize_usage()
    s = load_state()
    if not s.instance_id:
        return False, None

    if cfg.max_session_cost > 0:
        if u.total_cost >= cfg.max_session_cost:
            stop_worker(force=True)
            return True, f"Session cost limit reached (${cfg.max_session_cost:.2f}); worker auto-stopped"
        if u.total_cost >= cfg.max_session_cost * 0.8:
            return False, f"Warning: session cost at 80% (${u.total_cost:.2f}/${cfg.max_session_cost:.2f})"

    if cfg.max_hourly_cost > 0 and s.price_per_hour >= cfg.max_hourly_cost:
        stop_worker(force=True)
        return True, f"Hourly cost limit reached (${cfg.max_hourly_cost:.2f}); worker auto-stopped"
    if cfg.max_hourly_cost > 0 and s.price_per_hour >= cfg.max_hourly_cost * 0.8:
        return False, f"Warning: hourly price at 80% (${s.price_per_hour:.2f}/${cfg.max_hourly_cost:.2f})"

    return False, None
