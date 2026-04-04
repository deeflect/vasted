from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config import CURATED_MODELS, GPU_PRESETS, QUALITY_PROFILES
from app.models import ModelSpec

CURATED_GPU_FLOORS: dict[str, dict[str, str]] = {
    "qwen3-coder-30b": {"fast": "1xrtx4090", "balanced": "1xl40s", "max": "1xl40s", "ultra": "1xa100-80gb"},
    "qwen2.5-coder-7b": {"fast": "1xrtx4090", "balanced": "1xl40s", "max": "1xa100-80gb", "ultra": "1xa100-80gb"},
    "qwen3-8b": {"fast": "1xrtx4090", "balanced": "1xl40s", "max": "1xa100-80gb", "ultra": "1xa100-80gb"},
    "gemma-3-12b": {"fast": "1xl40s", "balanced": "1xl40s", "max": "1xa100-80gb", "ultra": "1xa100-80gb"},
}

_BASE_MODEL_TAG_RE = re.compile(r"^base_model(?::quantized)?:([^:\s]+/[^:\s]+)$")
_ACTIVE_PARAMS_RE = re.compile(r"\ba(\d+(?:\.\d+)?)b\b", re.IGNORECASE)
_CONTEXT_HINT_RE = re.compile(r"(?<!\d)(\d{2,3})k(?!\d)", re.IGNORECASE)


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


def _normalize_model_config(payload: dict) -> dict:
    text_cfg = payload.get("text_config")
    if isinstance(text_cfg, dict) and text_cfg:
        return text_cfg
    return payload


def _is_repo_slug(value: str) -> bool:
    return bool(re.fullmatch(r"[^/\s]+/[^/\s]+", value))


def _extract_base_model_repo(payload: dict) -> str | None:
    card_data = payload.get("cardData")
    if isinstance(card_data, dict):
        base_model = card_data.get("base_model")
        if isinstance(base_model, str) and _is_repo_slug(base_model):
            return base_model
        if isinstance(base_model, list):
            for item in base_model:
                if isinstance(item, str) and _is_repo_slug(item):
                    return item

    tags = payload.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if not isinstance(tag, str):
                continue
            match = _BASE_MODEL_TAG_RE.match(tag)
            if match and _is_repo_slug(match.group(1)):
                return match.group(1)
    return None


def _candidate_config_repos(model_spec: ModelSpec) -> list[str]:
    repos = [model_spec.hf_repo]
    try:
        payload = _fetch_model_payload(model_spec.hf_repo)
        base_repo = _extract_base_model_repo(payload)
        if base_repo and base_repo not in repos:
            repos.append(base_repo)
    except Exception:
        pass
    return repos


@lru_cache(maxsize=256)
def _fetch_repo_config(repo: str) -> dict:
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
    if not isinstance(payload, dict):
        return {}
    return _normalize_model_config(payload)


def _fetch_model_config(model_spec: ModelSpec) -> dict:
    for repo in _candidate_config_repos(model_spec):
        cfg = _fetch_repo_config(repo)
        if cfg:
            return cfg
    return {}


def _context_hint_from_name(model_spec: ModelSpec) -> int | None:
    haystack = f"{model_spec.name} {model_spec.hf_repo} {model_spec.filename}"
    values = [int(match.group(1)) for match in _CONTEXT_HINT_RE.finditer(haystack)]
    if not values:
        return None
    return max(values) * 1024


def model_max_context(model_spec: ModelSpec) -> int | None:
    config = _fetch_model_config(model_spec)
    candidates: list[int] = []
    for key in ("max_position_embeddings", "model_max_length", "max_seq_len", "seq_length", "n_ctx"):
        value = config.get(key)
        if isinstance(value, int) and value > 0:
            candidates.append(value)

    rope_scaling = config.get("rope_scaling")
    if isinstance(rope_scaling, dict):
        for key in ("original_max_position_embeddings", "max_position_embeddings"):
            value = rope_scaling.get(key)
            if isinstance(value, int) and value > 0:
                candidates.append(value)

    if candidates:
        return max(candidates)

    return _context_hint_from_name(model_spec)


def supported_quality_keys(model_spec: ModelSpec | None = None) -> list[str]:
    keys = list(QUALITY_PROFILES.keys())
    if model_spec is None:
        return keys
    max_ctx = model_max_context(model_spec)
    if max_ctx is None:
        return keys
    supported = [key for key in keys if QUALITY_PROFILES[key].context_length <= max_ctx]
    return supported or [keys[0]]


def quality_context(quality_profile: str, model_spec: ModelSpec | None = None) -> int:
    if quality_profile not in QUALITY_PROFILES:
        raise ValueError(f"Unknown quality profile: {quality_profile}")
    requested = QUALITY_PROFILES[quality_profile].context_length
    if model_spec is None:
        return requested
    max_ctx = model_max_context(model_spec)
    if max_ctx is None:
        return requested
    return min(requested, max_ctx)


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


def estimate_active_params_b(model_spec: ModelSpec) -> float | None:
    haystack = f"{model_spec.name} {model_spec.hf_repo} {model_spec.filename}"
    name_hits = [float(match.group(1)) for match in _ACTIVE_PARAMS_RE.finditer(haystack)]
    if name_hits:
        return max(name_hits)

    config = _fetch_model_config(model_spec)
    for key in ("active_parameters", "active_parameter_count", "num_active_parameters"):
        value = config.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value) / 1_000_000_000 if value > 1000 else float(value)

    num_layers = config.get("num_hidden_layers")
    hidden_size = config.get("hidden_size")
    num_experts_per_tok = config.get("num_experts_per_tok")
    moe_intermediate_size = config.get("moe_intermediate_size")
    required_values = (num_layers, hidden_size, num_experts_per_tok, moe_intermediate_size)
    if not all(isinstance(v, int) and v > 0 for v in required_values):
        return None
    shared = config.get("shared_expert_intermediate_size")
    shared_expert = shared if isinstance(shared, int) and shared > 0 else 0

    # Coarse estimate: active MoE FFN work + dense attention projection cost.
    active_per_layer = (
        4 * hidden_size * hidden_size
        + 3 * hidden_size * (num_experts_per_tok * moe_intermediate_size + shared_expert)
    )
    return float(active_per_layer * num_layers) / 1_000_000_000


def _estimate_kv_cache_gb(model_spec: ModelSpec, target_context: int) -> float | None:
    config = _fetch_model_config(model_spec)
    num_layers = config.get("num_hidden_layers")
    if not isinstance(num_layers, int) or num_layers <= 0:
        return None

    head_dim = config.get("head_dim")
    if not isinstance(head_dim, int) or head_dim <= 0:
        linear_head_dim = config.get("linear_value_head_dim")
        if isinstance(linear_head_dim, int) and linear_head_dim > 0:
            head_dim = linear_head_dim
    if not isinstance(head_dim, int) or head_dim <= 0:
        hidden_size = config.get("hidden_size")
        num_heads = config.get("num_attention_heads")
        if isinstance(hidden_size, int) and isinstance(num_heads, int) and num_heads > 0:
            head_dim = hidden_size // num_heads
    if not isinstance(head_dim, int) or head_dim <= 0:
        return None

    num_kv_heads = config.get(
        "num_key_value_heads",
        config.get("linear_num_value_heads", config.get("num_attention_heads")),
    )
    if not isinstance(num_kv_heads, int) or num_kv_heads <= 0:
        return None

    kv_bytes = float(2 * num_kv_heads * head_dim * 2 * num_layers * target_context)
    return kv_bytes / (1024**3)


def _estimate_required_vram_gb(model_spec: ModelSpec, target_context: int, model_size_gb: float) -> float:
    # Weight residency is dominated by the GGUF file, while KV cache is architecture-dependent.
    active_params_b = estimate_active_params_b(model_spec)
    kv_cache_gb = _estimate_kv_cache_gb(model_spec, target_context)
    if kv_cache_gb is None:
        # Fallback when model config is missing: keep conservative defaults, but use
        # active-parameter hints for MoE models so we avoid chronic oversizing.
        context_blocks = max(1.0, target_context / 32768.0)
        baseline_per_32k = 6.0
        if active_params_b is not None:
            baseline_per_32k = max(2.0, min(6.0, active_params_b * 0.8))
        kv_cache_gb = max(baseline_per_32k, context_blocks * baseline_per_32k)

    reserve_floor = 4.0
    reserve_ratio = 0.2
    if active_params_b is not None:
        if active_params_b <= 4.5:
            reserve_floor = 2.0
            reserve_ratio = 0.1
        elif active_params_b <= 8.0:
            reserve_floor = 3.0
            reserve_ratio = 0.15
    runtime_reserve_gb = max(reserve_floor, model_size_gb * reserve_ratio)
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
    requested_context = quality_context(quality_profile)
    target_context = quality_context(quality_profile, model_spec)
    model_size_gb = fetch_model_file_size_gb(model_spec)
    required_vram_gb = _estimate_required_vram_gb(model_spec, target_context, model_size_gb)
    minimum_gpu_preset = _pick_smallest_gpu(required_vram_gb)
    minimum_gpu_preset = _apply_curated_floor(model_spec, quality_profile, minimum_gpu_preset)
    rationale_parts = [
        f"{model_size_gb:.1f} GB GGUF + {target_context // 1024}k context + runtime reserve "
        f"requires about {required_vram_gb:.1f} GB VRAM"
    ]
    active_params = estimate_active_params_b(model_spec)
    if active_params is not None:
        rationale_parts.append(f"active params hint: ~{active_params:.1f}B")
    if target_context < requested_context:
        rationale_parts.append(f"requested {requested_context // 1024}k clamped to model max")
    rationale = "; ".join(rationale_parts)
    return LaunchSizing(
        target_context=target_context,
        model_size_gb=model_size_gb,
        required_vram_gb=required_vram_gb,
        minimum_gpu_preset=minimum_gpu_preset,
        rationale=rationale,
    )
