from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from rich.console import Console

from app.config import DEFAULT_CONFIG_PATH
from app.defaults import (
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_GPU_MODE,
    DEFAULT_IDLE_TIMEOUT_MINUTES,
    DEFAULT_LLAMA_CPP_IMAGE,
    DEFAULT_PROXY_HOST,
    DEFAULT_PROXY_PORT,
    DEFAULT_VAST_BASE_URL,
    SCHEMA_VERSION,
)
from app.persistence import load_dataclass, save_dataclass

console = Console()

keyring: Any | None
try:
    import keyring as _keyring  # type: ignore

    keyring = _keyring
except Exception:  # pragma: no cover
    keyring = None


@dataclass(slots=True)
class UserConfig:
    schema_version: int = SCHEMA_VERSION
    vast_api_key_ref: str = ""
    bearer_token_ref: str = ""
    telegram_token_ref: str | None = None
    vast_api_key_plain: str = ""
    bearer_token_plain: str = ""
    telegram_token_plain: str | None = None
    model: str = "qwen3-8b"
    quality_profile: str = "balanced"
    gpu_mode: str = DEFAULT_GPU_MODE
    gpu_preset: str = "1xa100-80gb"
    deployment_mode: str = DEFAULT_DEPLOYMENT_MODE
    public_host: str = ""
    instance_type: str = "any"
    proxy_host: str = DEFAULT_PROXY_HOST
    proxy_port: int = DEFAULT_PROXY_PORT
    cors_origins: list[str] | None = None
    cors_allowed_origins: list[str] | None = None
    idle_timeout_minutes: int = DEFAULT_IDLE_TIMEOUT_MINUTES
    max_session_cost: float = 0.0
    max_hourly_cost: float = 0.0
    max_requests_per_minute: int = 0
    model_profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    telegram_chat_id: str | None = None
    vast_base_url: str = DEFAULT_VAST_BASE_URL
    llama_cpp_image: str = DEFAULT_LLAMA_CPP_IMAGE
    llama_server_jinja: bool = True
    client_profile: str = "openclaw"

    def __post_init__(self) -> None:
        if self.cors_origins is None and self.cors_allowed_origins is not None:
            self.cors_origins = self.cors_allowed_origins
        if self.cors_origins is None:
            self.cors_origins = ["*"] if self.proxy_host in {"127.0.0.1", "localhost"} else []
        # Keep legacy field in sync for backward-compat persistence/tests.
        self.cors_allowed_origins = self.cors_origins


def _set_secret(name: str, value: str | None) -> tuple[str, str | None]:
    if not value:
        return "", None
    if keyring is None:
        console.print(f"[yellow]Warning: keyring unavailable; storing {name} in plaintext config[/yellow]")
        return "", value
    key = f"vasted:{name}"
    try:
        keyring.set_password("vasted", key, value)
        return f"keyring:{key}", None
    except Exception:
        console.print(f"[yellow]Warning: keyring failed; storing {name} in plaintext config[/yellow]")
        return "", value


def _get_secret(ref: str | None, plain: str | None) -> str:
    if ref and ref.startswith("keyring:") and keyring is not None:
        key = ref.split(":", 1)[1]
        try:
            return keyring.get_password("vasted", key) or ""
        except Exception:
            return plain or ""
    return plain or ""


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> UserConfig:
    cfg = cast(UserConfig, load_dataclass(path, UserConfig, UserConfig(), SCHEMA_VERSION))
    cfg.vast_api_key_plain = _get_secret(cfg.vast_api_key_ref, cfg.vast_api_key_plain)
    cfg.bearer_token_plain = _get_secret(cfg.bearer_token_ref, cfg.bearer_token_plain)
    cfg.telegram_token_plain = _get_secret(cfg.telegram_token_ref, cfg.telegram_token_plain)
    return cfg


def save_config(config: UserConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    cfg = config
    original_vast = cfg.vast_api_key_plain
    original_bearer = cfg.bearer_token_plain
    original_telegram = cfg.telegram_token_plain
    use_keyring = path == DEFAULT_CONFIG_PATH
    if use_keyring:
        vast_ref, vast_plain = _set_secret("vast_api_key", cfg.vast_api_key_plain)
        bearer_ref, bearer_plain = _set_secret("bearer_token", cfg.bearer_token_plain)
    else:
        vast_ref, vast_plain = "", cfg.vast_api_key_plain
        bearer_ref, bearer_plain = "", cfg.bearer_token_plain
    cfg.vast_api_key_ref = vast_ref
    cfg.vast_api_key_plain = vast_plain or ""
    cfg.bearer_token_ref = bearer_ref
    cfg.bearer_token_plain = bearer_plain or ""
    if use_keyring:
        token_ref, token_plain = _set_secret("telegram_token", cfg.telegram_token_plain)
    else:
        token_ref, token_plain = "", cfg.telegram_token_plain
    cfg.telegram_token_ref = token_ref or None
    cfg.telegram_token_plain = token_plain
    save_dataclass(path, cfg)
    # Keep the caller's in-memory object usable after persistence even when secrets
    # were stored in keyring instead of plaintext YAML.
    cfg.vast_api_key_plain = original_vast
    cfg.bearer_token_plain = original_bearer
    cfg.telegram_token_plain = original_telegram


def config_exists(path: Path = DEFAULT_CONFIG_PATH) -> bool:
    return path.exists()
