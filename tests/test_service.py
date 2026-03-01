from app.models import resolve_model
from app.service import (
    InventoryAttempt,
    InventoryCheck,
    LaunchPlan,
    _looks_like_fit_failure,
    check_inventory,
    start_worker,
)
from app.state import RuntimeState
from app.user_config import UserConfig


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
