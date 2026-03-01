from __future__ import annotations

import tomllib
from pathlib import Path

from app import __version__


def test_project_version_matches_package_version() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project_version = str(data["project"]["version"])
    assert project_version == __version__
