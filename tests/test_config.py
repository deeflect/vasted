from pathlib import Path

from app.user_config import UserConfig, load_config, save_config


def test_config_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    cfg = UserConfig(
        vast_api_key_plain="k",
        bearer_token_plain="b",
        model="qwen2.5-7b",
        quality_profile="balanced",
        gpu_preset="1xa100-80gb",
        max_requests_per_minute=123,
        model_profiles={"coding": {"model": "qwen2.5-coder-7b", "quality_profile": "balanced", "gpu_preset": "1xl40s"}},
    )
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded.vast_api_key_plain == "k"
    assert loaded.max_requests_per_minute == 123
    assert loaded.model_profiles["coding"]["model"] == "qwen2.5-coder-7b"


def test_config_missing_file(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.model == "qwen2.5-7b"


def test_config_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- list\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.bearer_token_plain == ""
