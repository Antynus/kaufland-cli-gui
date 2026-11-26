from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "slip-checker"
TIMEOUT_SECONDS = float(os.getenv("SLIP_TIMEOUT_SECONDS") or os.getenv("KAUFLAND_TIMEOUT_SECONDS") or "30")


class SlipError(RuntimeError):
    pass


def config_home() -> Path:
    return Path(os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")).expanduser()


def data_dir() -> Path:
    override = os.getenv("SLIP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return config_home() / APP_NAME


def store_dir(store: str) -> Path:
    return data_dir() / store


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SlipError(f"could not read {path}") from exc


def write_private_json(path: Path, data: Any) -> None:
    """Write JSON readable only by the current user, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.chmod(temp, 0o600)
    temp.replace(path)
