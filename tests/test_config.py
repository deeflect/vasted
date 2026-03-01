import importlib
import json
from pathlib import Path

from click.testing import CliRunner

import app.persistence as persistence
from app.cli import cli
from app.client_config import write_or_merge_opencode_config
from app.commands.config import render_config_summary
from app.commands.setup import (
    _choose_custom_repo_file,
    apply_deployment_mode_defaults,
    display_client_base_url,
    ensure_bearer_token,
)
from app.user_config import UserConfig, load_config, save_config

setup_helpers = importlib.import_module("app.commands.setup")


def test_config_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    cfg = UserConfig(
        vast_api_key_plain="k",
        bearer_token_plain="b",
        model="qwen3-8b",
        quality_profile="balanced",
        gpu_mode="auto",
        gpu_preset="1xa100-80gb",
        max_requests_per_minute=123,
        model_profiles={"coding": {"model": "qwen2.5-coder-7b", "quality_profile": "balanced", "gpu_preset": "1xl40s"}},
    )
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded.vast_api_key_plain == "k"
    assert loaded.gpu_mode == "auto"
    assert loaded.max_requests_per_minute == 123
    assert loaded.model_profiles["coding"]["model"] == "qwen2.5-coder-7b"


def test_config_missing_file(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.model == "qwen3-8b"


def test_load_config_does_not_rewrite_when_schema_is_current(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "config.yaml"
    save_config(UserConfig(vast_api_key_plain="k", bearer_token_plain="b"), path)
    writes: list[Path] = []
    real_save = persistence.save_dataclass

    def fake_save(target: Path, obj: object) -> None:
        writes.append(target)
        real_save(target, obj)

    monkeypatch.setattr(persistence, "save_dataclass", fake_save)
    load_config(path)
    assert writes == []


def test_config_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- list\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.bearer_token_plain == ""


def test_user_config_defaults_include_local_deployment_mode() -> None:
    cfg = UserConfig()
    assert cfg.deployment_mode == "local_pc"
    assert cfg.public_host == ""


def test_finalize_defaults_for_local_pc_mode() -> None:
    cfg = UserConfig(proxy_host="0.0.0.0", proxy_port=9999, deployment_mode="manual")
    apply_deployment_mode_defaults(cfg, "local_pc")
    assert cfg.deployment_mode == "local_pc"
    assert cfg.proxy_host == "127.0.0.1"
    assert cfg.proxy_port == 4318


def test_setup_reuses_existing_bearer_token() -> None:
    cfg = UserConfig(bearer_token_plain="stable-token")
    token = ensure_bearer_token(cfg)
    assert token == "stable-token"
    assert cfg.bearer_token_plain == "stable-token"


def test_merge_opencode_config_preserves_unrelated_providers(tmp_path: Path) -> None:
    path = tmp_path / "opencode.json"
    path.write_text('{"provider":{"other":{"name":"Other"}}}', encoding="utf-8")
    write_or_merge_opencode_config(
        path,
        base_url="http://127.0.0.1:4318/v1",
        api_key="token",
        model="qwen3-coder-30b",
        context_length=65536,
        set_default_model=False,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "other" in data["provider"]
    assert "vasted" in data["provider"]
    assert "model" not in data


def test_display_client_base_url_uses_public_host_for_remote_mode() -> None:
    cfg = UserConfig(
        deployment_mode="vps_remote",
        public_host="gpu.example.com",
        proxy_host="0.0.0.0",
        proxy_port=4318,
    )
    assert display_client_base_url(cfg) == "http://gpu.example.com:4318/v1"


def test_config_show_reports_deployment_mode_and_base_url(capsys) -> None:
    cfg = UserConfig(
        deployment_mode="local_pc",
        proxy_host="127.0.0.1",
        proxy_port=4318,
        model="qwen3-coder-30b",
    )
    render_config_summary(cfg)
    out = capsys.readouterr().out
    assert "local_pc" in out
    assert "http://127.0.0.1:4318/v1" in out


def test_cli_help_lists_token_and_config_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "token" in result.output
    assert "config" in result.output


def test_choose_custom_repo_file_defaults_to_recommended(monkeypatch) -> None:
    selections = iter([0])

    def fake_choose(_title: str, _rows: list[list[str]], default_index: int = 1) -> int:
        assert default_index == 1
        return next(selections)

    monkeypatch.setattr(setup_helpers, "_choose", fake_choose)
    filename = _choose_custom_repo_file(
        "org/repo",
        [
            "model-Q3_K_M.gguf",
            "model-Q4_K_M.gguf",
            "model-Q5_K_M.gguf",
        ],
    )
    assert filename == "model-Q4_K_M.gguf"
