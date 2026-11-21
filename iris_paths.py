"""Configuration locations and crash-safe writes, separate from installed code."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def is_checkout(root: Path) -> bool:
    return (root / "pyproject.toml").is_file() and (root / "iris.py").is_file()


def server_config(path: Path | None = None, *, for_write: bool = False) -> Path | None:
    """Explicit path, then cwd, then an existing source-checkout compatibility file.

    An explicit missing file is an error when reading. New files belong in cwd;
    an installed package is never a configuration or writable-data directory.
    """
    if path is not None:
        path = path.expanduser()
        if not for_write and not path.is_file():
            raise ValueError(f"The requested configuration file does not exist: {path}")
        return path
    current = Path.cwd() / "local-config.json"
    if current.exists():
        return current
    legacy = PROJECT_ROOT / "local-config.json"
    if is_checkout(PROJECT_ROOT) and legacy.is_file() and legacy != current:
        warnings.warn(
            f"Using the checkout configuration at {legacy}. Use --config to choose explicitly.",
            UserWarning,
            stacklevel=2,
        )
        return legacy
    return current if for_write else None


def runtime_data_root() -> Path:
    """Retain prepared source-checkout assets; keep wheel data outside site-packages."""
    if is_checkout(PROJECT_ROOT):
        return PROJECT_ROOT
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "iris-llm"


def atomic_json(path: Path, value: object) -> None:
    """Replace a complete UTF-8 JSON file only after serialization and fsync succeed."""
    content = json.dumps(value, indent=4, allow_nan=False) + "\n"
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
