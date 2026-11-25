from __future__ import annotations

import abc
import json
import os
import time
from pathlib import Path
from typing import Any

from ..config import StoreError, store_data_dir, store_token_path


class TokenStore:
    """Per-store token file at ``<data_dir>/<store>/tokens.json`` (mode 0600)."""

    def __init__(self, store: str) -> None:
        self.store = store

    @property
    def path(self) -> Path:
        return store_token_path(self.store)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        path = self.path
        if not path.exists():
            raise StoreError(f"no saved login for {self.store}; run: receipts login --store {self.store}")
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"could not read {path}") from exc

    def load_or_empty(self) -> dict[str, Any]:
        try:
            return self.load()
        except StoreError:
            return {}

    def save(self, tokens: dict[str, Any]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(tokens, indent=2))
        os.chmod(temp, 0o600)
        temp.replace(path)

    def delete(self) -> Path | None:
        path = self.path
        if not path.exists():
            return None
        path.unlink()
        return path


class Store(abc.ABC):
    """A receipts source for one retailer.

    Implementations own their auth and HTTP shape. ``fetch`` returns the raw
    receipt objects as the retailer's API gives them; ``line_items`` and
    ``summaries`` normalise those into flat rows the CLI prints as JSON/CSV.
    """

    #: short lowercase id used on the command line and as the token sub-dir
    name: str
    #: human-readable name for messages
    display_name: str

    @abc.abstractmethod
    def login(self, email: str | None = None, password: str | None = None) -> dict:
        """Authenticate and persist tokens. Returns an info dict for display."""

    @abc.abstractmethod
    def status(self) -> dict:
        """Report the saved login state."""

    @abc.abstractmethod
    def logout(self) -> Path | None:
        """Delete stored tokens; return the removed path or None."""

    @abc.abstractmethod
    def fetch(self, limit: int = 20, *, fetch_all: bool = False) -> list[dict]:
        """Return raw receipt objects, newest first where the API allows."""

    @abc.abstractmethod
    def line_items(self, receipts: list[dict]) -> list[dict]:
        """One normalised row per purchased item."""

    @abc.abstractmethod
    def summaries(self, receipts: list[dict]) -> list[dict]:
        """One normalised row per receipt."""

    # -- shared helpers -------------------------------------------------

    def _tokens(self) -> TokenStore:
        return TokenStore(self.name)

    @staticmethod
    def _base_status(tokens: dict[str, Any]) -> dict:
        expires_at = tokens.get("expires_at", 0)
        return {
            "access_token_valid": expires_at > time.time(),
            "expires_in_seconds": max(0, int(expires_at - time.time())),
            "has_refresh_token": bool(tokens.get("refresh_token")),
        }
