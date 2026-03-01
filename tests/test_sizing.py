from app.models import resolve_model
from app.sizing import (
    _estimate_kv_cache_gb,
    _fetch_model_payload,
    _head_file_size_gb,
    fetch_model_file_size_gb,
    plan_launch_sizing,
    quality_context,
)


def test_quality_context_targets() -> None:
    assert quality_context("fast") == 32768
    assert quality_context("balanced") == 65536
    assert quality_context("max") == 131072


def test_launch_sizing_uses_curated_floor_for_large_coder() -> None:
    sizing = plan_launch_sizing(resolve_model("qwen3-coder-30b"), "balanced")
    assert sizing.minimum_gpu_preset == "1xa100-80gb"
    assert sizing.target_context == 65536


def test_fetch_model_file_size_falls_back_to_head(monkeypatch) -> None:
    _fetch_model_payload.cache_clear()
    _head_file_size_gb.cache_clear()

    class FakeResponse:
        def __init__(self, payload=None, headers=None) -> None:
            self._payload = payload or {}
            self.headers = headers or {}

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    def fake_get(_url: str, timeout: float):
        return FakeResponse(
            {
                "siblings": [
                    {
                        "rfilename": "model.gguf",
                    }
                ]
            }
        )

    def fake_head(_url: str, timeout: float, follow_redirects: bool):
        assert follow_redirects is True
        return FakeResponse(headers={"content-length": str(8 * 1024**3)})

    monkeypatch.setattr("app.sizing.httpx.get", fake_get)
    monkeypatch.setattr("app.sizing.httpx.head", fake_head)

    size = fetch_model_file_size_gb(resolve_model("org/repo:model.gguf"))
    assert size == 8.0


def test_fetch_model_file_size_caches_model_metadata(monkeypatch) -> None:
    _fetch_model_payload.cache_clear()
    _head_file_size_gb.cache_clear()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "siblings": [
                    {
                        "rfilename": "model.gguf",
                        "size": 5 * 1024**3,
                    }
                ]
            }

    calls: list[str] = []

    def fake_get(url: str, timeout: float):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr("app.sizing.httpx.get", fake_get)

    spec = resolve_model("org/repo:model.gguf")
    assert fetch_model_file_size_gb(spec) == 5.0
    assert fetch_model_file_size_gb(spec) == 5.0
    assert len(calls) == 1


def test_estimate_kv_cache_uses_architecture_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.sizing._fetch_model_config",
        lambda _repo: {
            "num_hidden_layers": 48,
            "num_key_value_heads": 2,
            "head_dim": 256,
        },
    )

    kv_gb = _estimate_kv_cache_gb(resolve_model("org/repo:model.gguf"), 65536)
    assert kv_gb == 6.0
