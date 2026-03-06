import pytest

import app.vast as vast_module
from app.models import resolve_model
from app.vast import VastAPI, probe_worker_ready_sync


def test_offer_query_uses_vast_aliases_and_mib_units() -> None:
    api = VastAPI("test")
    query = api._offer_query("1xa100-80gb", min_disk_gb=40, min_cuda_max_good=12.8)
    assert query["gpu_name"] == {"in": ["A100 SXM4", "A100 PCIe"]}
    assert query["gpu_ram"] == {"gte": 80000}
    assert query["num_gpus"] == {"eq": 1}
    assert query["direct_port_count"] == {"gte": 1}
    assert query["disk_space"] == {"gte": 40}
    assert query["allocated_storage"] == 40.0
    assert query["cuda_max_good"] == {"gte": 12.8}
    assert query["verified"] == {"eq": True}

    relaxed = api._offer_query("1xh100", relaxed=True, min_disk_gb=64, min_cuda_max_good=12.8)
    assert relaxed["gpu_name"] == {"in": ["H100 SXM5", "H100 PCIe", "H100 NVL"]}
    assert relaxed["gpu_ram"] == {"gte": 80000}
    assert relaxed["disk_space"] == {"gte": 64}
    assert relaxed["allocated_storage"] == 64.0
    assert relaxed["cuda_max_good"] == {"gte": 12.8}
    assert "verified" not in relaxed

    api.client.close()


def test_request_instance_logs_parses_result_url(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")

    class _Resp:
        def json(self):
            return {"result_url": "https://example.com/log.txt"}

    monkeypatch.setattr(api, "_request", lambda method, path, **kwargs: _Resp())

    assert api.request_instance_logs(123) == "https://example.com/log.txt"

    api.client.close()


def test_get_instance_logs_fetches_result_url(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")
    monkeypatch.setattr(api, "request_instance_logs", lambda instance_id: "https://example.com/log.txt")

    class _Resp:
        status_code = 200
        text = "line1\nline2\n"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(vast_module.httpx, "get", lambda *args, **kwargs: _Resp())

    assert api.get_instance_logs(123) == "line1\nline2\n"

    api.client.close()


def test_get_billing_parses_nested_cost_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")
    monkeypatch.setattr(api, "get_instance_status", lambda instance_id: {"billing": {"total_charged": "1.2345"}})

    billing = api.get_billing(123, estimated_cost=2.5)

    assert billing.estimated_cost == pytest.approx(2.5)
    assert billing.billed_cost == pytest.approx(1.2345)
    api.client.close()


def test_get_account_balance_parses_nested_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")

    class _Resp:
        def json(self):
            return {"data": {"account": {"balance": "42.25"}}}

    monkeypatch.setattr(api, "_request", lambda method, path, **kwargs: _Resp())

    assert api.get_account_balance() == pytest.approx(42.25)
    api.client.close()


def test_probe_worker_ready_sync_checks_multiple_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Resp:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    def fake_get(url: str, headers: dict[str, str], timeout):
        calls.append(url)
        if url.endswith("/health"):
            return _Resp(503)
        return _Resp(200)

    monkeypatch.setattr(vast_module.httpx, "get", fake_get)

    assert probe_worker_ready_sync("http://worker:8000", headers={"Authorization": "Bearer token"})
    assert calls == ["http://worker:8000/health", "http://worker:8000/v1/models"]


def test_wait_for_ready_timeout_reports_phase_and_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")
    monkeypatch.setattr(
        api,
        "get_instance_status",
        lambda instance_id: {"actual_status": "loading", "status_msg": "Pulling model\nDownload complete"},
    )
    monkeypatch.setattr(api, "refresh_worker_url", lambda instance_id: None)
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(vast_module.time, "time", lambda: next(ticks))
    monkeypatch.setattr(vast_module.time, "sleep", lambda _: None)

    progress: list[str] = []
    with pytest.raises(TimeoutError) as exc:
        api.wait_for_ready(123, timeout=1, progress=progress.append)

    assert progress == ["waiting for Vast provisioning (loading): downloads complete; waiting for server"]
    message = str(exc.value)
    assert "phase=waiting for Vast provisioning" in message
    assert "last status=loading" in message
    assert "downloads complete; waiting for server" in message

    api.client.close()


def test_wait_for_ready_fails_fast_on_exited_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")
    monkeypatch.setattr(
        api,
        "get_instance_status",
        lambda instance_id: {"actual_status": "exited", "status_msg": "insufficient free disk"},
    )
    monkeypatch.setattr(api, "refresh_worker_url", lambda instance_id: None)

    with pytest.raises(vast_module.VastAPIError) as exc:
        api.wait_for_ready(123, timeout=1)

    assert "stopped before becoming ready (exited)" in str(exc.value)
    assert "insufficient free disk" in str(exc.value)

    api.client.close()


def test_wait_for_ready_checks_instance_status_once_per_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")
    calls: list[int] = []

    def fake_status(instance_id: int):
        calls.append(instance_id)
        return {"actual_status": "loading", "status_msg": "Pulling model"}

    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(api, "get_instance_status", fake_status)
    monkeypatch.setattr(vast_module.time, "time", lambda: next(ticks))
    monkeypatch.setattr(vast_module.time, "sleep", lambda _: None)

    with pytest.raises(TimeoutError):
        api.wait_for_ready(123, timeout=1)

    assert calls == [123]

    api.client.close()


def test_build_onstart_prefers_staged_download_and_local_model() -> None:
    api = VastAPI("test")
    script = api._build_onstart(resolve_model("qwen3-coder-30b"), "balanced", api_token="token")
    model_url = (
        "MODEL_URL=https://huggingface.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF/resolve/main/"
        "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
    )
    local_model = " -m ${CACHE_ROOT}/unsloth_Qwen3-Coder-30B-A3B-Instruct-GGUF_Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
    remote_repo = "--hf-repo unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF"
    cached_path = (
        "CACHED_PATH=${CACHE_ROOT}/unsloth_Qwen3-Coder-30B-A3B-Instruct-GGUF_Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"
    )

    assert 'python3 -m pip install --no-cache-dir -U "huggingface_hub[hf_xet]"' in script
    assert 'hf download "$HF_REPO" "$HF_FILE"' in script
    assert "HF_XET_HIGH_PERFORMANCE=1" in script
    assert 'HF_XET_NUM_CONCURRENT_RANGE_GETS="${HF_XET_NUM_CONCURRENT_RANGE_GETS:-64}"' in script
    assert "command -v curl" in script
    assert "ensure_parallel_downloader()" in script
    assert "apt-get install --no-install-recommends -y aria2c" in script
    assert "pick_cache_parent()" in script
    assert "for candidate in /workspace /data /mnt /var/lib /tmp /root /;" in script
    assert "MIN_FREE_GB=" in script
    assert "--jinja" in script
    assert cached_path in script
    assert model_url in script
    assert local_model in script
    assert remote_repo in script

    api.client.close()


def test_build_onstart_can_disable_jinja() -> None:
    api = VastAPI("test")
    script = api._build_onstart(resolve_model("qwen3-coder-30b"), "balanced", api_token="token", enable_jinja=False)
    assert "--jinja" not in script
    api.client.close()


def test_create_instance_uses_args_runtype(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")
    captured: dict[str, object] = {}

    class _Resp:
        def json(self):
            return {"new_contract": 123}

    def fake_request(method: str, path: str, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = kwargs.get("json")
        return _Resp()

    monkeypatch.setattr(api, "_request", fake_request)

    instance_id = api.create_instance(99, resolve_model("qwen3-coder-30b"), "balanced", "1xa100-80gb", api_token="t")

    assert instance_id == 123
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["runtype"] == "args"
    assert payload["onstart"] == "/bin/bash"
    assert payload["args"][0] == "-lc"
    assert "exec /app/llama-server" in payload["args"][1]
    assert "--jinja" in payload["args"][1]
    assert payload["env"] == {"-p 8000:8000": "1"}

    api.client.close()


def test_create_instance_can_disable_jinja(monkeypatch: pytest.MonkeyPatch) -> None:
    api = VastAPI("test")
    captured: dict[str, object] = {}

    class _Resp:
        def json(self):
            return {"new_contract": 123}

    def fake_request(method: str, path: str, **kwargs):
        captured["json"] = kwargs.get("json")
        return _Resp()

    monkeypatch.setattr(api, "_request", fake_request)

    api.create_instance(
        99,
        resolve_model("qwen3-coder-30b"),
        "balanced",
        "1xa100-80gb",
        api_token="t",
        enable_jinja=False,
    )

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert "--jinja" not in payload["args"][1]

    api.client.close()
