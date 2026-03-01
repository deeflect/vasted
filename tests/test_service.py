import pytest

from app.models import resolve_model
from app.service import (
    InventoryAttempt,
    InventoryCheck,
    LaunchPlan,
    _looks_like_fit_failure,
    check_inventory,
    start_worker,
    stop_worker,
)
from app.state import RuntimeState
from app.usage import UsageSummary
from app.user_config import UserConfig
from app.vast import BillingInfo


def _usage_sample(total_cost: float = 1.23) -> UsageSummary:
    return UsageSummary(
        requests=1,
        input_tokens=0,
        output_tokens=0,
        prompt_ms_total=0.0,
        predicted_ms_total=0.0,
        avg_output_tokens_per_second=0.0,
        duration_seconds=1.0,
        total_cost=total_cost,
        input_cost=0.0,
        output_cost=0.0,
        overhead_cost=total_cost,
        blended_dollars_per_million_tokens=0.0,
        input_dollars_per_million_tokens=0.0,
        output_dollars_per_million_tokens=0.0,
    )


class _FakeAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, int, float | None]] = []

    def estimate_disk_gb(self, model_spec) -> int:
        return 40

    def search_offers(
        self,
        gpu_preset: str,
        instance_type: str = "any",
        limit: int = 50,
        relaxed: bool = False,
        min_disk_gb: int = 1,
        min_cuda_max_good: float | None = None,
    ) -> list[dict[str, object]]:
        self.calls.append((gpu_preset, relaxed, min_disk_gb, min_cuda_max_good))
        if gpu_preset == "1xa100-80gb" and relaxed:
            return [{"id": 1, "dph_total": 0.9}]
        if gpu_preset == "1xh100" and not relaxed:
            return [{"id": 2, "dph_total": 1.6}]
        return []


def test_check_inventory_escalates_after_recording_raw_capacity(monkeypatch) -> None:
    from app import service

    fake_api = _FakeAPI()
    monkeypatch.setattr(
        service,
        "require_config",
        lambda: UserConfig(vast_api_key_plain="vast", bearer_token_plain="token"),
    )
    monkeypatch.setattr(service, "_api", lambda cfg: fake_api)
    plan = LaunchPlan(
        model_spec=resolve_model("qwen3-coder-30b"),
        quality_profile="balanced",
        gpu_mode="auto",
        requested_gpu_preset="1xa100-80gb",
        selected_gpu_preset="1xa100-80gb",
        target_context=65536,
        model_size_gb=19.0,
        required_vram_gb=80.0,
        rationale="test",
    )

    inventory = check_inventory(plan, limit=5)

    assert inventory.selected_gpu_preset == "1xh100"
    assert len(inventory.offers) == 1
    assert [attempt.gpu_preset for attempt in inventory.attempts] == ["1xa100-80gb", "1xh100"]
    assert inventory.attempts[0].strict_count == 0
    assert inventory.attempts[0].relaxed_count == 1
    assert inventory.attempts[1].strict_count == 1
    assert inventory.attempts[1].best_price == 1.6
    assert fake_api.calls == [
        ("1xa100-80gb", False, 40, 12.8),
        ("1xa100-80gb", True, 40, 12.8),
        ("1xh100", False, 40, 12.8),
    ]


def test_looks_like_fit_failure_matches_memory_errors() -> None:
    assert _looks_like_fit_failure("CUDA out of memory")
    assert _looks_like_fit_failure("ggml_gallocr_reserve_n: failed")
    assert not _looks_like_fit_failure("provided PTX was compiled with an unsupported toolchain")


def test_start_worker_escalates_after_fit_probe_failure(monkeypatch) -> None:
    from app import service

    class _ProbeAPI:
        def __init__(self) -> None:
            self.created: list[int] = []
            self.destroyed: list[int] = []

        def create_instance(self, offer_id, *args, **kwargs) -> int:
            self.created.append(int(offer_id))
            return int(offer_id)

        def wait_for_ready(self, instance_id, **kwargs):
            if int(instance_id) == 1:
                raise TimeoutError("probe timed out")
            return "http://worker"

        def get_instance_logs(self, instance_id, attempts=3, delay_s=1.0) -> str:
            if int(instance_id) == 1:
                return "CUDA out of memory"
            return ""

        def destroy_instance(self, instance_id) -> None:
            self.destroyed.append(int(instance_id))

    fake_api = _ProbeAPI()
    monkeypatch.setattr(
        service,
        "require_config",
        lambda: UserConfig(vast_api_key_plain="vast", bearer_token_plain="token"),
    )
    monkeypatch.setattr(service, "_api", lambda cfg: fake_api)
    monkeypatch.setattr(service, "load_state", lambda: RuntimeState())
    monkeypatch.setattr(service, "save_state", lambda state: None)
    monkeypatch.setattr(service, "clear_state", lambda: None)
    monkeypatch.setattr(service, "reset_usage_for_new_session", lambda price: None)
    monkeypatch.setattr(
        service,
        "_inventory_attempt_for_preset",
        lambda api, cfg, plan, preset_key, limit=50: (
            ([{"id": 2, "dph_total": 1.6}] if preset_key == "1xh100" else []),
            InventoryAttempt(
                gpu_preset=preset_key,
                strict_count=1 if preset_key == "1xh100" else 0,
                relaxed_count=0,
                best_price=1.6 if preset_key == "1xh100" else None,
            ),
        ),
    )

    plan = LaunchPlan(
        model_spec=resolve_model(
            "mradermacher/Huihui-Qwen3-Coder-Next-abliterated-GGUF:Huihui-Qwen3-Coder-Next-abliterated.Q4_K_M.gguf"
        ),
        quality_profile="balanced",
        gpu_mode="auto",
        requested_gpu_preset="1xa100-80gb",
        selected_gpu_preset="1xa100-80gb",
        target_context=65536,
        model_size_gb=45.0,
        required_vram_gb=66.0,
        rationale="test",
    )
    inventory = InventoryCheck(
        selected_gpu_preset="1xa100-80gb",
        offers=[{"id": 1, "dph_total": 0.9}],
        attempts=[InventoryAttempt(gpu_preset="1xa100-80gb", strict_count=1, relaxed_count=1, best_price=0.9)],
    )

    result = start_worker(launch_plan=plan, inventory_check=inventory)

    assert result.gpu_preset == "1xh100"
    assert result.instance_id == 2
    assert fake_api.created == [1, 2]
    assert fake_api.destroyed == [1]


def test_stop_worker_uses_balance_delta_when_billed_cost_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import service

    class _FakeClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _FakeAPI:
        def __init__(self) -> None:
            self.client = _FakeClient()
            self.destroyed: list[int] = []
            self._balances = iter([10.0, 9.25])

        def get_account_balance(self) -> float:
            return float(next(self._balances))

        def get_billing(self, instance_id: int, estimated_cost: float) -> BillingInfo:
            return BillingInfo(estimated_cost=estimated_cost, billed_cost=None)

        def destroy_instance(self, instance_id: int) -> None:
            self.destroyed.append(instance_id)

    fake_api = _FakeAPI()
    usage = _usage_sample(total_cost=1.23)
    cleared: list[bool] = []

    monkeypatch.setattr(
        service,
        "require_config",
        lambda: UserConfig(vast_api_key_plain="vast", bearer_token_plain="token"),
    )
    monkeypatch.setattr(service, "load_state", lambda: RuntimeState(instance_id=321))
    monkeypatch.setattr(service, "summarize_usage", lambda: usage)
    monkeypatch.setattr(service, "_api", lambda cfg: fake_api)
    monkeypatch.setattr(service, "clear_state", lambda: cleared.append(True))

    result = stop_worker()

    assert result.billing.estimated_cost == pytest.approx(1.23)
    assert result.billing.billed_cost == pytest.approx(0.75)
    assert result.had_active_instance
    assert result.remote_destroyed
    assert fake_api.destroyed == [321]
    assert fake_api.client.closed
    assert cleared == [True]


def test_stop_worker_keeps_direct_billed_cost_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import service

    class _FakeClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _FakeAPI:
        def __init__(self) -> None:
            self.client = _FakeClient()
            self.destroyed: list[int] = []
            self._balances = iter([10.0, 8.0])

        def get_account_balance(self) -> float:
            return float(next(self._balances))

        def get_billing(self, instance_id: int, estimated_cost: float) -> BillingInfo:
            return BillingInfo(estimated_cost=estimated_cost, billed_cost=0.42)

        def destroy_instance(self, instance_id: int) -> None:
            self.destroyed.append(instance_id)

    fake_api = _FakeAPI()
    usage = _usage_sample(total_cost=1.23)

    monkeypatch.setattr(
        service,
        "require_config",
        lambda: UserConfig(vast_api_key_plain="vast", bearer_token_plain="token"),
    )
    monkeypatch.setattr(service, "load_state", lambda: RuntimeState(instance_id=321))
    monkeypatch.setattr(service, "summarize_usage", lambda: usage)
    monkeypatch.setattr(service, "_api", lambda cfg: fake_api)
    monkeypatch.setattr(service, "clear_state", lambda: None)

    result = stop_worker()

    assert result.billing.billed_cost == pytest.approx(0.42)
    assert result.had_active_instance
    assert result.remote_destroyed
    assert fake_api.destroyed == [321]
    assert fake_api.client.closed


def test_stop_worker_no_active_instance_clears_state_without_remote_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import service

    cleared: list[bool] = []
    usage = _usage_sample(total_cost=0.0)

    monkeypatch.setattr(service, "load_state", lambda: RuntimeState())
    monkeypatch.setattr(service, "summarize_usage", lambda: usage)
    monkeypatch.setattr(service, "clear_state", lambda: cleared.append(True))
    monkeypatch.setattr(service, "require_config", lambda: (_ for _ in ()).throw(AssertionError("should not load cfg")))

    result = stop_worker()

    assert not result.had_active_instance
    assert not result.remote_destroyed
    assert result.billing.billed_cost is None
    assert cleared == [True]


def test_stop_worker_treats_not_found_destroy_as_already_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import service

    class _FakeClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _FakeAPI:
        def __init__(self) -> None:
            self.client = _FakeClient()
            self.destroyed: list[int] = []
            self._balances = iter([5.0, 5.0])

        def get_account_balance(self) -> float:
            return float(next(self._balances))

        def get_billing(self, instance_id: int, estimated_cost: float) -> BillingInfo:
            return BillingInfo(estimated_cost=estimated_cost, billed_cost=None)

        def destroy_instance(self, instance_id: int) -> None:
            self.destroyed.append(instance_id)
            raise RuntimeError("DELETE /instances/321 failed with 404 not found")

    fake_api = _FakeAPI()
    usage = _usage_sample(total_cost=0.8)

    monkeypatch.setattr(
        service,
        "require_config",
        lambda: UserConfig(vast_api_key_plain="vast", bearer_token_plain="token"),
    )
    monkeypatch.setattr(service, "load_state", lambda: RuntimeState(instance_id=321))
    monkeypatch.setattr(service, "summarize_usage", lambda: usage)
    monkeypatch.setattr(service, "_api", lambda cfg: fake_api)
    monkeypatch.setattr(service, "clear_state", lambda: None)

    result = stop_worker(force=False)

    assert result.had_active_instance
    assert result.remote_destroyed
    assert fake_api.destroyed == [321]
    assert fake_api.client.closed


def test_stop_worker_force_without_config_clears_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import service

    cleared: list[bool] = []
    usage = _usage_sample(total_cost=0.5)

    monkeypatch.setattr(service, "load_state", lambda: RuntimeState(instance_id=999))
    monkeypatch.setattr(service, "summarize_usage", lambda: usage)
    monkeypatch.setattr(service, "require_config", lambda: (_ for _ in ()).throw(RuntimeError("missing config")))
    monkeypatch.setattr(service, "clear_state", lambda: cleared.append(True))

    result = stop_worker(force=True)

    assert result.had_active_instance
    assert not result.remote_destroyed
    assert result.billing.billed_cost is None
    assert cleared == [True]
