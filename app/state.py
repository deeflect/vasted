from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from app.config import DEFAULT_STATE_PATH
from app.defaults import SCHEMA_VERSION
from app.persistence import load_dataclass, save_dataclass


@dataclass(slots=True)
class RuntimeState:
    schema_version: int = SCHEMA_VERSION
    instance_id: int | None = None
    worker_url: str | None = None
    model_name: str | None = None
    started_at: float | None = None
    price_per_hour: float = 0.0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    session_start: float | None = None
    last_request_at: float | None = None


def load_state(path: Path = DEFAULT_STATE_PATH) -> RuntimeState:
    return cast(RuntimeState, load_dataclass(path, RuntimeState, RuntimeState(), SCHEMA_VERSION))


def save_state(state: RuntimeState, path: Path = DEFAULT_STATE_PATH) -> None:
    save_dataclass(path, state)


def clear_state(path: Path = DEFAULT_STATE_PATH) -> None:
    save_dataclass(path, RuntimeState())
