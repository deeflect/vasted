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
    kind: str = "coding"
    featured: bool = False
    size_gb: float | None = None


CURATED_MODELS: dict[str, CuratedModel] = {
    "qwen3-8b": CuratedModel(
        "qwen3-8b",
        "bartowski/Qwen_Qwen3-8B-GGUF",
        "Qwen_Qwen3-8B-Q4_K_M.gguf",
        32768,
        "Fast general-purpose model, great for chat and reasoning. [recommended]",
        kind="general",
        featured=True,
        size_gb=5.2,
    ),
    "gemma-3-12b": CuratedModel(
        "gemma-3-12b",
        "bartowski/google_gemma-3-12b-it-GGUF",
        "google_gemma-3-12b-it-Q4_K_M.gguf",
        32768,
        "Google's latest, strong reasoning and instruction following.",
        kind="general",
        featured=True,
        size_gb=8.1,
    ),
    "qwen3-coder-30b": CuratedModel(
        "qwen3-coder-30b",
        "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
        65536,
        "Best open-source coding model. MoE architecture, fast for its size.",
        kind="coding",
        featured=True,
        size_gb=19.0,
    ),
    "deepseek-coder-v2-lite": CuratedModel(
        "deepseek-coder-v2-lite",
        "bartowski/DeepSeek-Coder-V2-Lite-Instruct-GGUF",
        "DeepSeek-Coder-V2-Lite-Instruct-Q4_K_M.gguf",
        65536,
        "Lightweight coding model, low VRAM, solid performance.",
        kind="coding",
        size_gb=9.4,
    ),
    "qwen2.5-7b": CuratedModel(
        "qwen2.5-7b",
        "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        32768,
        "Proven workhorse, great speed/quality tradeoff.",
        kind="general",
        size_gb=5.1,
    ),
    "qwen2.5-coder-7b": CuratedModel(
        "qwen2.5-coder-7b",
        "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        65536,
        "Code-focused, good quality/cost balance.",
        kind="coding",
        featured=True,
        size_gb=5.2,
    ),
    "codestral-22b": CuratedModel(
        "codestral-22b",
        "bartowski/Codestral-22B-v0.1-GGUF",
        "Codestral-22B-v0.1-Q4_K_M.gguf",
        32768,
        "High-quality coding, needs bigger GPU (L40S/A100).",
        kind="coding",
        size_gb=13.8,
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
    search_aliases: tuple[str, ...] = ()

    @property
    def total_vram_gb(self) -> int:
        return self.min_vram_gb * self.num_gpus

    @property
    def vast_gpu_names(self) -> tuple[str, ...]:
        return self.search_aliases or (self.search,)


GPU_PRESETS: dict[str, GpuPreset] = {
    "1xa100-80gb": GpuPreset(
        "1xa100-80gb",
        "1x A100 80GB",
        80,
        "A100",
        "$0.90-$1.80/hr",
        search_aliases=("A100 SXM4", "A100 PCIe"),
    ),
    "1xh100": GpuPreset(
        "1xh100",
        "1x H100",
        80,
        "H100",
        "$1.80-$3.50/hr",
        search_aliases=("H100 SXM5", "H100 PCIe", "H100 NVL"),
    ),
    "1xrtx4090": GpuPreset("1xrtx4090", "1x RTX 4090", 24, "RTX 4090", "$0.20-$0.70/hr"),
    "1xl40s": GpuPreset("1xl40s", "1x L40S", 48, "L40S", "$0.60-$1.40/hr"),
    "2xa100-80gb": GpuPreset(
        "2xa100-80gb",
        "2x A100 80GB",
        80,
        "A100",
        "$1.80-$3.60/hr",
        num_gpus=2,
        search_aliases=("A100 SXM4", "A100 PCIe"),
    ),
    "4xa100-80gb": GpuPreset(
        "4xa100-80gb",
        "4x A100 80GB",
        80,
        "A100",
        "$3.60-$7.20/hr",
        num_gpus=4,
        search_aliases=("A100 SXM4", "A100 PCIe"),
    ),
    "2xh100": GpuPreset(
        "2xh100",
        "2x H100",
        80,
        "H100",
        "$3.60-$7.00/hr",
        num_gpus=2,
        search_aliases=("H100 SXM5", "H100 PCIe", "H100 NVL"),
    ),
    "4xh100": GpuPreset(
        "4xh100",
        "4x H100",
        80,
        "H100",
        "$7.00-$14.00/hr",
        num_gpus=4,
        search_aliases=("H100 SXM5", "H100 PCIe", "H100 NVL"),
    ),
}


@dataclass(frozen=True, slots=True)
class QualityProfile:
    key: str
    context_length: int
    use_case: str


QUALITY_PROFILES: dict[str, QualityProfile] = {
    "fast": QualityProfile("fast", 32768, "Lower-cost coding and agentic tasks"),
    "balanced": QualityProfile("balanced", 65536, "Best default for coding and agents"),
    "max": QualityProfile("max", 131072, "Very long context coding and tool workflows"),
}


def ensure_dirs() -> None:
    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
