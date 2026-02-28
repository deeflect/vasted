from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import CURATED_MODELS

OLLAMA_ALIASES = {
    "qwen3:8b": "qwen3-8b",
    "gemma3:12b": "gemma-3-12b",
    "qwen3-coder:30b": "qwen3-coder-30b",
    "deepseek-coder:lite": "deepseek-coder-v2-lite",
    "qwen2.5:7b": "qwen2.5-7b",
    "qwen2.5-coder:7b": "qwen2.5-coder-7b",
    "codestral:22b": "codestral-22b",
    "codestral": "codestral-22b",
}


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    hf_repo: str
    filename: str
    context_length: int


def _parse_hf_url(text: str) -> tuple[str, str] | None:
    if "huggingface.co" not in text:
        return None
    p = urlparse(text)
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 5:
        return None
    repo = f"{parts[0]}/{parts[1]}"
    filename = parts[-1]
    return repo, filename


def _validate_gguf(filename: str) -> None:
    if not filename.lower().endswith(".gguf"):
        raise ValueError("Model file must be a .gguf file")


def _extract_param_billions(model_spec: ModelSpec) -> int | None:
    hay = f"{model_spec.name} {model_spec.hf_repo} {model_spec.filename}".lower()
    matches = re.findall(r"(\d{1,3})\s*b", hay)
    if not matches:
        return None
    return max(int(m) for m in matches)


def suggest_gpu_preset(model_spec: ModelSpec) -> str:
    hay = f"{model_spec.name} {model_spec.hf_repo} {model_spec.filename}".lower()
    if re.search(r"\b\d{1,3}b-a\d{1,3}b\b", hay):
        return "1xrtx4090"

    params_b = _extract_param_billions(model_spec)
    if params_b is None:
        return "1xrtx4090"
    if params_b <= 8:
        return "1xrtx4090"
    if params_b <= 22:
        return "1xl40s"
    return "1xa100-80gb"


def resolve_model(model_input: str) -> ModelSpec:
    value = model_input.strip()
    if not value:
        value = "qwen3-8b"

    if value in CURATED_MODELS:
        m = CURATED_MODELS[value]
        return ModelSpec(m.name, m.hf_repo, m.filename, m.recommended_context)

    alias_target = OLLAMA_ALIASES.get(value.lower())
    if alias_target and alias_target in CURATED_MODELS:
        m = CURATED_MODELS[alias_target]
        return ModelSpec(m.name, m.hf_repo, m.filename, m.recommended_context)

    parsed = _parse_hf_url(value)
    if parsed:
        repo, filename = parsed
        _validate_gguf(filename)
        return ModelSpec(name=filename, hf_repo=repo, filename=filename, context_length=65536)

    if "/" in value and ":" in value:
        repo, filename = value.split(":", 1)
        _validate_gguf(filename)
        return ModelSpec(name=filename, hf_repo=repo, filename=filename, context_length=65536)

    raise ValueError(
        f"Could not resolve model input: {model_input}. Use curated model key, known Ollama alias, "
        "or HF format like org/repo:model.gguf"
    )
