from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

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
    kind: str = "custom"
    source_key: str | None = None


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


def _is_repo_slug(value: str) -> bool:
    return bool(re.fullmatch(r"[^/\s]+/[^/\s]+", value))


def list_hf_gguf_files(repo: str) -> list[str]:
    response = httpx.get(
        f"https://huggingface.co/api/models/{repo}",
        timeout=20.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    data = response.json()
    siblings = data.get("siblings", [])
    if not isinstance(siblings, list):
        return []

    filenames = []
    for entry in siblings:
        if not isinstance(entry, dict):
            continue
        filename = entry.get("rfilename")
        if isinstance(filename, str) and filename.lower().endswith(".gguf"):
            filenames.append(filename)
    return sorted(set(filenames))


def _gguf_preference_rank(filename: str) -> tuple[int, int, str]:
    lower = filename.lower()
    is_split = bool(re.search(r"-\d{5}-of-\d{5}\.gguf$", lower))
    preference = [
        "q4_k_m",
        "q4_k_s",
        "q5_k_m",
        "q5_k_s",
        "q6_k",
        "q8_0",
        "iq4_xs",
        "iq4_nl",
        "f16",
        "fp16",
    ]
    for index, token in enumerate(preference):
        if token in lower:
            return (1 if is_split else 0, index, lower)
    return (1 if is_split else 0, len(preference), lower)


def choose_default_gguf_file(filenames: list[str]) -> str:
    if not filenames:
        raise ValueError("No GGUF files found in the Hugging Face repo.")
    return min(filenames, key=_gguf_preference_rank)


def _extract_param_billions(model_spec: ModelSpec) -> int | None:
    hay = f"{model_spec.name} {model_spec.hf_repo} {model_spec.filename}".lower()
    matches = re.findall(r"(\d{1,3})\s*b", hay)
    if not matches:
        return None
    return max(int(m) for m in matches)


def featured_model_keys() -> list[str]:
    return [key for key, model in CURATED_MODELS.items() if model.featured]


def suggest_gpu_preset(model_spec: ModelSpec, quality_profile: str = "balanced") -> str:
    from app.sizing import plan_launch_sizing

    return plan_launch_sizing(model_spec, quality_profile).minimum_gpu_preset


def resolve_model(
    model_input: str,
    gguf_selector: Callable[[str, list[str]], str] | None = None,
) -> ModelSpec:
    value = model_input.strip()
    if not value:
        value = "qwen3-8b"

    if value in CURATED_MODELS:
        m = CURATED_MODELS[value]
        return ModelSpec(m.name, m.hf_repo, m.filename, m.recommended_context, kind=m.kind, source_key=value)

    alias_target = OLLAMA_ALIASES.get(value.lower())
    if alias_target and alias_target in CURATED_MODELS:
        m = CURATED_MODELS[alias_target]
        return ModelSpec(m.name, m.hf_repo, m.filename, m.recommended_context, kind=m.kind, source_key=alias_target)

    parsed = _parse_hf_url(value)
    if parsed:
        repo, filename = parsed
        _validate_gguf(filename)
        return ModelSpec(name=filename, hf_repo=repo, filename=filename, context_length=65536, kind="custom")

    if "/" in value and ":" in value:
        repo, filename = value.split(":", 1)
        _validate_gguf(filename)
        return ModelSpec(name=filename, hf_repo=repo, filename=filename, context_length=65536, kind="custom")

    if _is_repo_slug(value):
        try:
            filenames = list_hf_gguf_files(value)
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not read Hugging Face repo: {value} ({exc})") from exc
        if not filenames:
            raise ValueError(
                f"No .gguf files found in Hugging Face repo: {value}. "
                "This project needs a GGUF repo or a specific .gguf file."
            )
        if gguf_selector and len(filenames) > 1:
            filename = gguf_selector(value, filenames)
        else:
            filename = choose_default_gguf_file(filenames)
        _validate_gguf(filename)
        return ModelSpec(name=filename, hf_repo=value, filename=filename, context_length=65536, kind="custom")

    raise ValueError(
        f"Could not resolve model input: {model_input}. Use curated model key, known Ollama alias, "
        "a Hugging Face repo like org/repo, or HF format like org/repo:model.gguf"
    )
