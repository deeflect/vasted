from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config import CURATED_MODELS, GPU_PRESETS, QUALITY_PROFILES
from app.models import ModelSpec

CURATED_GPU_FLOORS: dict[str, dict[str, str]] = {
    "qwen3-coder-30b": {"fast": "1xa100-80gb", "balanced": "1xa100-80gb", "max": "2xa100-80gb"},
    "qwen2.5-coder-7b": {"fast": "1xrtx4090", "balanced": "1xl40s", "max": "1xa100-80gb"},
    "qwen3-8b": {"fast": "1xrtx4090", "balanced": "1xl40s", "max": "1xa100-80gb"},
    "gemma-3-12b": {"fast": "1xl40s", "balanced": "1xl40s", "max": "1xa100-80gb"},
}


@dataclass(frozen=True, slots=True)
class LaunchSizing:
    target_context: int
    model_size_gb: float
    required_vram_gb: float
    minimum_gpu_preset: str
    rationale: str


def _sorted_gpu_keys() -> list[str]:
    return sorted(
        GPU_PRESETS,
        key=lambda key: (GPU_PRESETS[key].total_vram_gb, GPU_PRESETS[key].num_gpus, GPU_PRESETS[key].min_vram_gb),
    )


def iter_candidate_gpu_keys(minimum_key: str) -> Iterable[str]:
    keys = _sorted_gpu_keys()
    start = keys.index(minimum_key)
    yield from keys[start:]


def quality_context(quality_profile: str) -> int:
    if quality_profile not in QUALITY_PROFILES:
        raise ValueError(f"Unknown quality profile: {quality_profile}")
    return QUALITY_PROFILES[quality_profile].context_length


@lru_cache(maxsize=256)
def _fetch_model_payload(repo: str) -> dict:
    resp = httpx.get(f"https://huggingface.co/api/models/{repo}", timeout=20.0)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected metadata payload for {repo}")
    return payload


@lru_cache(maxsize=512)
def _head_file_size_gb(repo: str, filename: str) -> float:
    file_url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
    head = httpx.head(file_url, timeout=20.0, follow_redirects=True)
    head.raise_for_status()
    for header_name in ("x-linked-size", "content-length"):
        header_value = head.headers.get(header_name)
        if not header_value:
            continue
        try:
            return float(int(header_value)) / (1024**3)
        except ValueError:
            continue
    raise ValueError(f"Could not determine GGUF file size for {repo}:{filename}")


def fetch_model_file_size_gb(model_spec: ModelSpec) -> float:
    if model_spec.source_key:
        curated = CURATED_MODELS.get(model_spec.source_key)
        if curated and curated.size_gb:
            return curated.size_gb

    try:
        payload = _fetch_model_payload(model_spec.hf_repo)
    except Exception as exc:
        raise ValueError(
            f"Could not fetch model metadata for {model_spec.hf_repo}. "
            "Use a valid Hugging Face GGUF link or switch to a curated model."
        ) from exc

    siblings = payload.get("siblings", [])
    for sibling in siblings:
        if sibling.get("rfilename") == model_spec.filename:
            size_b = sibling.get("size")
            if size_b:
                return float(size_b) / (1024**3)
            break

    try:
        return _head_file_size_gb(model_spec.hf_repo, model_spec.filename)
    except Exception as exc:
        raise ValueError(f"Could not determine GGUF file size for {model_spec.hf_repo}:{model_spec.filename}") from exc


@lru_cache(maxsize=128)
def _fetch_model_config(repo: str) -> dict:
    try:
        resp = httpx.get(
            f"https://huggingface.co/{repo}/raw/main/config.json",
            timeout=20.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _estimate_kv_cache_gb(model_spec: ModelSpec, target_context: int) -> float | None:
    config = _fetch_model_config(model_spec.hf_repo)
    num_layers = config.get("num_hidden_layers")
    if not isinstance(num_layers, int) or num_layers <= 0:
        return None

    head_dim = config.get("head_dim")
    if not isinstance(head_dim, int) or head_dim <= 0:
        hidden_size = config.get("hidden_size")
        num_heads = config.get("num_attention_heads")
        if isinstance(hidden_size, int) and isinstance(num_heads, int) and num_heads > 0:
            head_dim = hidden_size // num_heads
    if not isinstance(head_dim, int) or head_dim <= 0:
        return None

    num_kv_heads = config.get("num_key_value_heads", config.get("num_attention_heads"))
    if not isinstance(num_kv_heads, int) or num_kv_heads <= 0:
        return None

    kv_bytes = float(2 * num_kv_heads * head_dim * 2 * num_layers * target_context)
    return kv_bytes / (1024**3)


def _estimate_required_vram_gb(model_spec: ModelSpec, target_context: int, model_size_gb: float) -> float:
    # Weight residency is dominated by the GGUF file, while KV cache is architecture-dependent.
    kv_cache_gb = _estimate_kv_cache_gb(model_spec, target_context)
    if kv_cache_gb is None:
        # Fallback when model config is missing: keep a conservative default, but do not
        # scale KV directly with total file size because that badly overstates MoE models.
        context_blocks = max(1.0, target_context / 32768.0)
        kv_cache_gb = max(6.0, context_blocks * 6.0)
    runtime_reserve_gb = max(4.0, model_size_gb * 0.2)
    return model_size_gb + kv_cache_gb + runtime_reserve_gb


def _preset_meets_requirement(preset_key: str, required_vram_gb: float) -> bool:
    return GPU_PRESETS[preset_key].total_vram_gb >= required_vram_gb


def _pick_smallest_gpu(required_vram_gb: float) -> str:
    for key in _sorted_gpu_keys():
        if _preset_meets_requirement(key, required_vram_gb):
            return key
    raise ValueError(
        f"Required VRAM is approximately {required_vram_gb:.1f} GB, which exceeds the largest configured GPU preset."
    )


def _apply_curated_floor(model_spec: ModelSpec, quality_profile: str, selected_key: str) -> str:
    if not model_spec.source_key:
        return selected_key
    floors = CURATED_GPU_FLOORS.get(model_spec.source_key)
    if not floors:
        return selected_key
    floor_key = floors.get(quality_profile)
    if not floor_key:
        return selected_key
    if GPU_PRESETS[selected_key].total_vram_gb >= GPU_PRESETS[floor_key].total_vram_gb:
        return selected_key
    return floor_key


def plan_launch_sizing(model_spec: ModelSpec, quality_profile: str) -> LaunchSizing:
    target_context = quality_context(quality_profile)
    model_size_gb = fetch_model_file_size_gb(model_spec)
    required_vram_gb = _estimate_required_vram_gb(model_spec, target_context, model_size_gb)
    minimum_gpu_preset = _pick_smallest_gpu(required_vram_gb)
    minimum_gpu_preset = _apply_curated_floor(model_spec, quality_profile, minimum_gpu_preset)
    rationale = (
        f"{model_size_gb:.1f} GB GGUF + {target_context // 1024}k context + runtime reserve "
        f"requires about {required_vram_gb:.1f} GB VRAM"
    )
    return LaunchSizing(
        target_context=target_context,
        model_size_gb=model_size_gb,
        required_vram_gb=required_vram_gb,
        minimum_gpu_preset=minimum_gpu_preset,
        rationale=rationale,
    )
