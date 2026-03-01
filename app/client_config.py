from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OPENCODE_CONFIG_PATH = Path.home() / ".config" / "opencode" / "opencode.json"
OPENCODE_SCHEMA = "https://opencode.ai/config.json"


def _model_label(model: str) -> str:
    return model.replace("-", " ").replace("_", " ").title()


def _opencode_provider(base_url: str, api_key: str, model: str, context_length: int) -> dict[str, Any]:
    return {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Vasted (Vast.ai)",
        "options": {
            "baseURL": base_url,
            "apiKey": api_key,
            "timeout": 600000,
        },
        "models": {
            model: {
                "name": _model_label(model),
                "limit": {
                    "context": int(context_length),
                    "output": 8192,
                },
            }
        },
    }


def render_opencode_config(
    base_url: str,
    api_key: str,
    model: str,
    context_length: int,
    set_default_model: bool,
) -> str:
    data: dict[str, Any] = {
        "$schema": OPENCODE_SCHEMA,
        "provider": {
            "vasted": _opencode_provider(base_url, api_key, model, context_length),
        },
    }
    if set_default_model:
        data["model"] = f"vasted/{model}"
    return json.dumps(data, indent=2) + "\n"


def write_or_merge_opencode_config(
    path: Path,
    base_url: str,
    api_key: str,
    model: str,
    context_length: int,
    set_default_model: bool,
) -> None:
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("OpenCode config must be a JSON object.")
        data = dict(raw)
    else:
        data = {}

    provider = data.get("provider", {})
    if not isinstance(provider, dict):
        raise ValueError("OpenCode config 'provider' must be a JSON object.")

    provider = dict(provider)
    provider["vasted"] = _opencode_provider(base_url, api_key, model, context_length)
    data["provider"] = provider
    data.setdefault("$schema", OPENCODE_SCHEMA)

    if set_default_model:
        data["model"] = f"vasted/{model}"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
