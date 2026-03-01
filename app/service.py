from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from app.config import GPU_PRESETS
from app.models import ModelSpec, resolve_model
from app.sizing import LaunchSizing, iter_candidate_gpu_keys, plan_launch_sizing
from app.state import RuntimeState, clear_state, load_state, save_state
from app.usage import UsageSummary, reset_usage_for_new_session, summarize_usage
from app.user_config import UserConfig, config_exists, load_config
from app.vast import BillingInfo, VastAPI, VastOffer, recommended_min_cuda_max_good


@dataclass(slots=True)
class UpResult:
    instance_id: int
    worker_url: str
    model: str
    price_per_hour: float
    gpu_preset: str
    quality_profile: str
    target_context: int


@dataclass(slots=True)
class LaunchPlan:
    model_spec: ModelSpec
    quality_profile: str
    gpu_mode: str
    requested_gpu_preset: str
    selected_gpu_preset: str
    target_context: int
    model_size_gb: float
    required_vram_gb: float
    rationale: str


@dataclass(slots=True)
class DownResult:
    usage: UsageSummary
    billing: BillingInfo


@dataclass(slots=True)
class InventoryAttempt:
    gpu_preset: str
    strict_count: int
    relaxed_count: int
    best_price: float | None


@dataclass(slots=True)
class InventoryCheck:
    selected_gpu_preset: str | None
    offers: list[VastOffer]
    attempts: list[InventoryAttempt]


@dataclass(slots=True)
class LaunchAttemptFailure(RuntimeError):
    message: str
    fit_issue: bool = False

    def __str__(self) -> str:
        return self.message


def require_config() -> UserConfig:
    if not config_exists():
        raise RuntimeError("Run `vasted setup` first")
    cfg = load_config()
    if not cfg.vast_api_key_plain or not cfg.bearer_token_plain:
        raise RuntimeError("Incomplete config. Run `vasted setup` first")
    return cfg


def _api(cfg: UserConfig) -> VastAPI:
    return VastAPI(cfg.vast_api_key_plain, base_url=cfg.vast_base_url)


def _candidate_presets_for_plan(plan: LaunchPlan) -> list[str]:
    if plan.gpu_mode == "manual":
        return [plan.selected_gpu_preset]
    return list(iter_candidate_gpu_keys(plan.selected_gpu_preset))


def _inventory_attempt_for_preset(
    api: VastAPI,
    cfg: UserConfig,
    plan: LaunchPlan,
    preset_key: str,
    limit: int = 50,
) -> tuple[list[VastOffer], InventoryAttempt]:
    min_disk_gb = api.estimate_disk_gb(plan.model_spec)
    min_cuda_max_good = recommended_min_cuda_max_good(cfg.llama_cpp_image)
    strict_offers = api.search_offers(
        preset_key,
        instance_type=cfg.instance_type,
        limit=limit,
        min_disk_gb=min_disk_gb,
        min_cuda_max_good=min_cuda_max_good,
    )
    relaxed_offers = strict_offers or api.search_offers(
        preset_key,
        instance_type=cfg.instance_type,
        limit=limit,
        relaxed=True,
        min_disk_gb=min_disk_gb,
        min_cuda_max_good=min_cuda_max_good,
    )
    best_offer = strict_offers[0] if strict_offers else (relaxed_offers[0] if relaxed_offers else None)
    best_price = None
    if best_offer is not None:
        best_price = float(best_offer.get("dph_total") or best_offer.get("dph") or 0.0)
    attempt = InventoryAttempt(
        gpu_preset=preset_key,
        strict_count=len(strict_offers),
        relaxed_count=len(relaxed_offers),
        best_price=best_price,
    )
    return strict_offers, attempt


def _offer_id(offer: VastOffer) -> int:
    offer_id_raw = offer.get("id") or offer.get("ask_id") or offer.get("ask_contract_id")
    if offer_id_raw is None:
        raise RuntimeError(f"Malformed Vast offer (missing id): {offer}")
    return int(offer_id_raw)


def _offer_price(offer: VastOffer) -> float:
    return float(offer.get("dph_total") or offer.get("dph") or 0.0)


def _fit_failure_markers() -> tuple[str, ...]:
    return (
        "out of memory",
        "not enough device memory",
        "not enough free device memory",
        "insufficient memory",
        "failed to allocate",
        "failed to reserve",
        "ggml_gallocr_reserve_n: failed",
        "cuda_error_out_of_memory",
    )


def _looks_like_fit_failure(*parts: str) -> bool:
    haystack = "\n".join(part for part in parts if part).lower()
    return any(marker in haystack for marker in _fit_failure_markers())


def _should_probe_fit(plan: LaunchPlan, gpu_preset: str) -> bool:
    if plan.model_spec.source_key is None:
        return True
    total_vram = float(GPU_PRESETS[gpu_preset].total_vram_gb)
    free_margin = total_vram - plan.required_vram_gb
    if free_margin <= 0:
        return True
    return (free_margin / total_vram) < 0.2


def check_inventory(plan: LaunchPlan, limit: int = 50) -> InventoryCheck:
    cfg = require_config()
    api = _api(cfg)
    attempts: list[InventoryAttempt] = []
    selected_gpu_preset: str | None = None
    selected_offers: list[VastOffer] = []
    for preset_key in _candidate_presets_for_plan(plan):
        strict_offers, attempt = _inventory_attempt_for_preset(api, cfg, plan, preset_key, limit=limit)
        attempts.append(attempt)
        if strict_offers and selected_gpu_preset is None:
            selected_gpu_preset = preset_key
            selected_offers = strict_offers
            break
    return InventoryCheck(selected_gpu_preset=selected_gpu_preset, offers=selected_offers, attempts=attempts)


def prepare_launch(
    model_override: str | None = None,
    quality_override: str | None = None,
    gpu_mode_override: str | None = None,
    gpu_preset_override: str | None = None,
) -> LaunchPlan:
    cfg = require_config()
    model_spec: ModelSpec = resolve_model(model_override or cfg.model)
    quality_profile = quality_override or cfg.quality_profile
    sizing: LaunchSizing = plan_launch_sizing(model_spec, quality_profile)
    gpu_mode = (gpu_mode_override or cfg.gpu_mode or "auto").strip().lower()
    if gpu_mode not in {"auto", "manual"}:
        raise RuntimeError(f"Unsupported GPU mode: {gpu_mode}")
    requested_gpu_preset = gpu_preset_override or cfg.gpu_preset
    if requested_gpu_preset not in GPU_PRESETS:
        raise RuntimeError(f"Unknown GPU preset: {requested_gpu_preset}")
    selected_gpu_preset = sizing.minimum_gpu_preset
    if gpu_mode == "manual":
        if GPU_PRESETS[requested_gpu_preset].total_vram_gb < GPU_PRESETS[sizing.minimum_gpu_preset].total_vram_gb:
            raise RuntimeError(
                f"Selected GPU preset {requested_gpu_preset} is too small for {quality_profile} "
                f"({sizing.target_context // 1024}k). Minimum required preset is {sizing.minimum_gpu_preset}."
            )
        selected_gpu_preset = requested_gpu_preset
    return LaunchPlan(
        model_spec=model_spec,
        quality_profile=quality_profile,
        gpu_mode=gpu_mode,
        requested_gpu_preset=requested_gpu_preset,
        selected_gpu_preset=selected_gpu_preset,
        target_context=sizing.target_context,
        model_size_gb=sizing.model_size_gb,
        required_vram_gb=sizing.required_vram_gb,
        rationale=sizing.rationale,
    )


def _load_worker_once(
    api: VastAPI,
    cfg: UserConfig,
    plan: LaunchPlan,
    gpu_preset: str,
    offer: VastOffer,
    progress: Callable[[str], None] | None = None,
    probe: bool = False,
) -> UpResult:
    price = _offer_price(offer)
    if progress:
        action = "Running fit probe" if probe else "Launching worker"
        progress(f"{action} on {GPU_PRESETS[gpu_preset].name}...")
    instance_id = api.create_instance(
        _offer_id(offer),
        plan.model_spec,
        plan.quality_profile,
        gpu_preset,
        image=cfg.llama_cpp_image,
        api_token=cfg.bearer_token_plain,
    )
    now = time.time()
    save_state(
        RuntimeState(
            instance_id=instance_id,
            worker_url=None,
            model_name=plan.model_spec.name,
            started_at=now,
            price_per_hour=price,
            session_start=now,
            last_request_at=now,
        )
    )
    try:
        worker_url = api.wait_for_ready(instance_id, api_token=cfg.bearer_token_plain, progress=progress)
    except Exception as exc:
        log_text = ""
        try:
            log_text = api.get_instance_logs(instance_id, attempts=3, delay_s=1.0)
        except Exception:
            pass
        try:
            api.destroy_instance(instance_id)
        except Exception:
            pass
        clear_state()
        detail = str(exc)
        if log_text:
            last_lines = "\n".join(log_text.splitlines()[-8:])
            detail = f"{detail}\n{last_lines}"
        raise LaunchAttemptFailure(detail, fit_issue=_looks_like_fit_failure(str(exc), log_text)) from exc

    save_state(
        RuntimeState(
            instance_id=instance_id,
            worker_url=worker_url,
            model_name=plan.model_spec.name,
            started_at=now,
            price_per_hour=price,
            session_start=now,
            last_request_at=now,
        )
    )
    reset_usage_for_new_session(price)
    return UpResult(
        instance_id=instance_id,
        worker_url=worker_url,
        model=plan.model_spec.name,
        price_per_hour=price,
        gpu_preset=gpu_preset,
        quality_profile=plan.quality_profile,
        target_context=plan.target_context,
    )


def start_worker(
    model_override: str | None = None,
    quality_override: str | None = None,
    gpu_mode_override: str | None = None,
    gpu_preset_override: str | None = None,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
    launch_plan: LaunchPlan | None = None,
    inventory_check: InventoryCheck | None = None,
) -> UpResult:
    cfg = require_config()
    state = load_state()
    if state.instance_id and not force:
        raise RuntimeError("Worker already active. Run `vasted down` first, or pass --force")
    if state.instance_id and force:
        stop_worker(force=True)

    plan = launch_plan or prepare_launch(
        model_override=model_override,
        quality_override=quality_override,
        gpu_mode_override=gpu_mode_override,
        gpu_preset_override=gpu_preset_override,
    )
    api = _api(cfg)
    inventory = inventory_check
    if inventory is None:
        if progress:
            progress(f"Searching Vast offers for {GPU_PRESETS[plan.selected_gpu_preset].name}...")
        inventory = check_inventory(plan)
    first_gpu_preset = inventory.selected_gpu_preset or plan.selected_gpu_preset
    if not inventory.offers:
        raw_capacity = [attempt for attempt in inventory.attempts if attempt.relaxed_count > 0]
        if raw_capacity:
            details = ", ".join(
                f"{GPU_PRESETS[attempt.gpu_preset].name}: {attempt.relaxed_count} raw / {attempt.strict_count} safe"
                for attempt in raw_capacity
            )
            raise RuntimeError(
                "Safe Vast offers were filtered out by reliability/network constraints. "
                f"Raw capacity exists for {details}."
            )
        raise RuntimeError(
            f"No matching Vast offers found for {first_gpu_preset} or any larger preset."
            if plan.gpu_mode == "auto"
            else f"No matching Vast offers found for {first_gpu_preset}"
        )

    candidate_presets = (
        [first_gpu_preset] if plan.gpu_mode == "manual" else list(iter_candidate_gpu_keys(first_gpu_preset))
    )
    first_offers = inventory.offers
    last_failure: LaunchAttemptFailure | None = None
    for index, gpu_preset in enumerate(candidate_presets):
        current_plan = replace(plan, selected_gpu_preset=gpu_preset)
        offers = first_offers if index == 0 else _inventory_attempt_for_preset(api, cfg, current_plan, gpu_preset)[0]
        if not offers:
            continue
        probe = _should_probe_fit(current_plan, gpu_preset)
        fit_failure_for_tier = False
        for offer_index, offer in enumerate(offers[:3]):
            if progress and offer_index > 0:
                progress(f"Retrying another {GPU_PRESETS[gpu_preset].name} host...")
            try:
                return _load_worker_once(api, cfg, current_plan, gpu_preset, offer, progress=progress, probe=probe)
            except LaunchAttemptFailure as exc:
                last_failure = exc
                if exc.fit_issue:
                    fit_failure_for_tier = True
                    break
                if plan.gpu_mode == "manual" or offer_index == min(len(offers), 3) - 1:
                    raise RuntimeError(str(exc)) from exc
        if fit_failure_for_tier:
            if plan.gpu_mode == "manual":
                raise RuntimeError(str(last_failure)) from last_failure
            if progress:
                progress(f"Fit probe failed on {GPU_PRESETS[gpu_preset].name}; escalating to the next GPU tier...")
            continue

    if last_failure is not None:
        raise RuntimeError(str(last_failure)) from last_failure
    raise RuntimeError("Unable to find a working Vast offer for the selected launch plan.")


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
