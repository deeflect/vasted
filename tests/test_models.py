from app.models import resolve_model, suggest_gpu_preset


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
    assert suggest_gpu_preset(resolve_model("qwen3-8b")) == "1xrtx4090"
    assert suggest_gpu_preset(resolve_model("codestral")) == "1xl40s"
    assert suggest_gpu_preset(resolve_model("qwen3-coder-30b")) == "1xrtx4090"
