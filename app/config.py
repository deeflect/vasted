from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.defaults import APP_NAME

DEFAULT_CONFIG_PATH = Path.home() / ".config" / APP_NAME / "config.yaml"
DEFAULT_STATE_PATH = Path.home() / ".config" / APP_NAME / "state.yaml"


@dataclass(frozen=True, slots=True)
class CuratedModel:
    name: str
    hf_repo: str
    filename: str
    recommended_context: int
    description: str


CURATED_MODELS: dict[str, CuratedModel] = {
    "qwen2.5-7b": CuratedModel(
        "qwen2.5-7b",
        "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "qwen2.5-7b-instruct-q4_k_m.gguf",
        32768,
        "Great speed/quality tradeoff for general assistant tasks.",
    ),
    "llama-3.1-8b": CuratedModel(
        "llama-3.1-8b",
        "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        32768,
        "Strong general model with broad ecosystem support.",
    ),
    "qwen2.5-coder-7b": CuratedModel(
        "qwen2.5-coder-7b",
        "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        65536,
        "Code-focused model with good quality/cost balance.",
    ),
    "deepseek-coder-v2-lite": CuratedModel(
        "deepseek-coder-v2-lite",
        "bartowski/DeepSeek-Coder-V2-Lite-Instruct-GGUF",
        "DeepSeek-Coder-V2-Lite-Instruct-Q4_K_M.gguf",
        65536,
        "Strong coding model with long context and low VRAM pressure.",
    ),
    "codestral": CuratedModel(
        "codestral",
        "bartowski/Codestral-22B-v0.1-GGUF",
        "Codestral-22B-v0.1-Q4_K_M.gguf",
        32768,
        "High-quality coding model; best on bigger GPUs.",
    ),
    "phi-3": CuratedModel(
        "phi-3",
        "bartowski/Phi-3-medium-128k-instruct-GGUF",
        "Phi-3-medium-128k-instruct-Q4_K_M.gguf",
        131072,
        "Compact model with excellent long-context efficiency.",
    ),
    "mistral-nemo": CuratedModel(
        "mistral-nemo",
        "bartowski/Mistral-Nemo-Instruct-2407-GGUF",
        "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf",
        131072,
        "General-purpose long-context model with strong reasoning.",
    ),
}


@dataclass(frozen=True, slots=True)
class GpuPreset:
    key: str
    name: str
    min_vram_gb: int
    search: str
    typical_price: str
    num_gpus: int = 1


GPU_PRESETS: dict[str, GpuPreset] = {
    "1xa100-80gb": GpuPreset("1xa100-80gb", "1x A100 80GB", 80, "A100", "$0.90-$1.80/hr"),
    "1xh100": GpuPreset("1xh100", "1x H100", 80, "H100", "$1.80-$3.50/hr"),
    "1xrtx4090": GpuPreset("1xrtx4090", "1x RTX 4090", 24, "RTX 4090", "$0.20-$0.70/hr"),
    "1xl40s": GpuPreset("1xl40s", "1x L40S", 48, "L40S", "$0.60-$1.40/hr"),
}


@dataclass(frozen=True, slots=True)
class QualityProfile:
    key: str
    context_length: int
    use_case: str


QUALITY_PROFILES: dict[str, QualityProfile] = {
    "fast": QualityProfile("fast", 8192, "Lowest latency chat, short prompts"),
    "balanced": QualityProfile("balanced", 65536, "Best default for coding and assistants"),
    "max": QualityProfile("max", 131072, "Very long context tasks"),
}


def ensure_dirs() -> None:
    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
