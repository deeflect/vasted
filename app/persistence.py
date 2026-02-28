from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


def _ensure_mode_600(path: Path) -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        os.chmod(path, 0o600)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_dataclass(path: Path, cls: type[Any], defaults: Any, schema_version: int) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        save_dataclass(path, defaults)
        return defaults

    _ensure_mode_600(path)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        backup = path.with_suffix(path.suffix + ".corrupt.bak")
        shutil.copy2(path, backup)
        save_dataclass(path, defaults)
        return defaults

    if not isinstance(raw, dict):
        save_dataclass(path, defaults)
        return defaults

    current_version = int(raw.get("schema_version", 0) or 0)
    if current_version != schema_version:
        raw["schema_version"] = schema_version

    allowed = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in raw.items() if k in allowed}

    merged = asdict(defaults)
    merged.update(filtered)
    obj = cls(**merged)
    save_dataclass(path, obj)
    return obj


def save_dataclass(path: Path, obj: Any) -> None:
    if not is_dataclass(obj) or isinstance(obj, type):
        raise TypeError("save_dataclass expects dataclass instance")
    data = asdict(obj)
    text = yaml.safe_dump(data, sort_keys=False)
    _atomic_write_text(path, text)
    _ensure_mode_600(path)


# Backwards-compatible aliases.
load_yaml_dataclass = load_dataclass
save_yaml_dataclass = save_dataclass
