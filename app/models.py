from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import CURATED_MODELS

OLLAMA_ALIASES = {
    "qwen2.5:7b": "qwen2.5-7b",
    "llama3.1:8b": "llama-3.1-8b",
    "qwen2.5-coder:7b": "qwen2.5-coder-7b",
    "deepseek-coder-v2:16b": "deepseek-coder-v2-lite",
    "codestral:22b": "codestral",
    "phi3:14b": "phi-3",
    "phi3:medium": "phi-3",
    "mistral-nemo:12b": "mistral-nemo",
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


def resolve_model(model_input: str) -> ModelSpec:
    value = model_input.strip()
    if not value:
        value = "qwen2.5-7b"

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
