from app.models import resolve_model


def test_resolve_curated() -> None:
    spec = resolve_model("qwen2.5-7b")
    assert spec.hf_repo == "Qwen/Qwen2.5-7B-Instruct-GGUF"


def test_resolve_alias() -> None:
    spec = resolve_model("qwen2.5:7b")
    assert spec.name == "qwen2.5-7b"


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
    assert spec.name == "qwen2.5-7b"
