from __future__ import annotations

import os
from pathlib import Path


class StoreError(RuntimeError):
    """Any user-facing failure from a store connector."""


# Back-compat alias: earlier single-store code raised KauflandError.
KauflandError = StoreError


TIMEOUT_SECONDS = float(
    os.getenv("RECEIPTS_TIMEOUT_SECONDS") or os.getenv("KAUFLAND_TIMEOUT_SECONDS") or "30"
)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def data_dir() -> Path:
    """Base directory that holds one sub-directory of tokens per store."""
    override = os.getenv("RECEIPTS_DATA_DIR") or os.getenv("KAUFLAND_DATA_DIR")
    if override:
        return Path(override).expanduser()
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "receipts-cli"


def store_data_dir(store: str) -> Path:
    return data_dir() / store


def store_token_path(store: str) -> Path:
    return store_data_dir(store) / "tokens.json"
