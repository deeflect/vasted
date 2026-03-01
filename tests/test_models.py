import app.models as models
from app.models import choose_default_gguf_file, featured_model_keys, resolve_model, suggest_gpu_preset


def test_resolve_curated() -> None:
    spec = resolve_model("qwen3-8b")
    assert "Qwen3" in spec.hf_repo


def test_resolve_alias() -> None:
    assert resolve_model("qwen3:8b").name == "qwen3-8b"
    assert resolve_model("gemma3:12b").name == "gemma-3-12b"
    assert resolve_model("deepseek-coder:lite").name == "deepseek-coder-v2-lite"


def test_resolve_hf_url() -> None:
    spec = resolve_model("https://huggingface.co/org/repo/resolve/main/model.gguf")
    assert spec.hf_repo == "org/repo"
    assert spec.filename == "model.gguf"


def test_resolve_repo_file() -> None:
    spec = resolve_model("org/repo:model.gguf")
    assert spec.hf_repo == "org/repo"


def test_resolve_repo_slug_auto_discovers_default_file(monkeypatch) -> None:
    monkeypatch.setattr(
        models,
        "list_hf_gguf_files",
        lambda repo: ["model-Q6_K.gguf", "model-Q4_K_M.gguf", "model-fp16.gguf"],
    )
    spec = resolve_model("org/repo")
    assert spec.hf_repo == "org/repo"
    assert spec.filename == "model-Q4_K_M.gguf"


def test_resolve_repo_slug_uses_selector_for_multiple_files(monkeypatch) -> None:
    monkeypatch.setattr(
        models,
        "list_hf_gguf_files",
        lambda repo: ["model-Q6_K.gguf", "model-Q4_K_M.gguf"],
    )
    spec = resolve_model("org/repo", lambda _repo, filenames: filenames[0])
    assert spec.filename == "model-Q6_K.gguf"


def test_choose_default_gguf_file_avoids_split_files() -> None:
    filename = choose_default_gguf_file(
        [
            "model-fp16-00001-of-00004.gguf",
            "model-fp16-00002-of-00004.gguf",
            "model-Q4_K_M.gguf",
        ]
    )
    assert filename == "model-Q4_K_M.gguf"


def test_resolve_invalid_ext() -> None:
    try:
        resolve_model("org/repo:model.bin")
        raise AssertionError("expected ValueError")
    except ValueError:
        assert True


def test_resolve_empty_defaults() -> None:
    spec = resolve_model("")
    assert spec.name == "qwen3-8b"


def test_suggest_gpu_preset() -> None:
    assert suggest_gpu_preset(resolve_model("qwen3-8b"), "fast") == "1xrtx4090"
    assert suggest_gpu_preset(resolve_model("qwen3-8b"), "balanced") == "1xl40s"
    assert suggest_gpu_preset(resolve_model("codestral"), "balanced") == "1xl40s"
    assert suggest_gpu_preset(resolve_model("qwen3-coder-30b"), "balanced") == "1xa100-80gb"


def test_featured_models_include_coding_defaults() -> None:
    featured = featured_model_keys()
    assert "qwen3-coder-30b" in featured
    assert "qwen2.5-coder-7b" in featured
