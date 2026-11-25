"""The store plug-in contract.

Every source of receipts, built in or dropped into the plugins folder, is a ``Store``.
A store turns whatever its backend returns into *normalized receipts*: plain dicts that
the whole app (dashboard, search, export, categories) understands.

Normalized receipt::

    {
        "uid": "lidl:1234",          # globally unique: "<store id>:<receipt id>"
        "source": "lidl",            # Store.id that produced it
        "id": "1234",                # the backend's own id
        "merchant": "Lidl",          # brand name, used for grouping and categories
        "branch": "Lidl Zagreb",     # optional: shop name/address
        "city": "Zagreb",            # optional
        "timestamp": "2026-11-20T17:03:00+01:00",   # ISO 8601
        "currency": "EUR",
        "total": 23.45,              # what was paid, as a float in major units
        "saving": 1.20,              # optional: discounts
        "points": 12,                # optional: loyalty points
        "items": [                   # optional: may be empty
            {"name": "Mlijeko", "quantity": 1, "unit": "kom", "unit_price": 0.99,
             "total": 0.99, "refund": False},
        ],
        "note": "",                  # optional free text
        "fiscal": {"jir": "...", "zki": "..."},  # optional: Croatian fiscal ids
    }
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from kaufland_cli.config import data_dir

Progress = Callable[[str], None]


class StoreError(RuntimeError):
    """A problem the user should see, e.g. a rejected login or an API error."""


class Store:
    # --- identity, override in subclasses ---
    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    color: ClassVar[str] = "#64748B"
    #: "password" (email + password form), "browser" (sign-in page, then a callback URL),
    #: "token" (paste an API key or token) or "none" (local data, nothing to sign in to)
    auth: ClassVar[str] = "none"
    #: True for stores whose receipts the user adds by hand (scanning, manual entry)
    local: ClassVar[bool] = False
    #: set for plugins loaded from the plugins folder
    plugin_path: Path | None = None

    # --- state ---
    def connected(self) -> bool:
        return self.auth == "none"

    def account_label(self) -> str:
        return ""

    # --- auth: implement what your `auth` kind needs ---
    def login(self, email: str, password: str, *, otp: Callable[[], str] | None = None) -> str:
        """auth == "password". Return a label for the account (e.g. the email)."""
        raise NotImplementedError

    def login_url(self) -> str:
        """auth == "browser". Return the URL the user signs in at."""
        raise NotImplementedError

    def complete_login(self, callback_url: str) -> str:
        """auth == "browser". Finish sign-in from the URL the sign-in page redirected to."""
        raise NotImplementedError

    def callback_prefix(self) -> str:
        """auth == "browser". URL prefix that marks the end of the sign-in flow."""
        return ""

    def set_token(self, token: str) -> str:
        """auth == "token". Store an API token the user pasted."""
        raise NotImplementedError

    def logout(self) -> None:
        pass

    # --- data ---
    def fetch(self, limit: int, *, cached: list[dict], progress: Progress | None = None) -> list[dict]:
        """Return normalized receipts, newest first.

        ``limit`` is the number of receipts to fetch; 0 means the whole history.
        ``cached`` holds what was fetched last time, so a store can skip re-downloading
        receipts it already has.
        """
        return cached

    # --- helpers for subclasses ---
    def state_path(self, name: str) -> Path:
        return data_dir() / "stores" / self.id / name

    def read_state(self, name: str, default: Any = None) -> Any:
        try:
            return json.loads(self.state_path(name).read_text())
        except (OSError, json.JSONDecodeError):
            return default

    def write_state(self, name: str, value: Any, *, private: bool = False) -> None:
        path = self.state_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1))
        if private:
            os.chmod(tmp, 0o600)
        tmp.replace(path)

    def delete_state(self, name: str) -> None:
        self.state_path(name).unlink(missing_ok=True)


def amount(value: Any) -> float:
    """Parse "1.234,56", "1,234.56", "12,99", 12.99 or None into a float."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = "".join(ch for ch in str(value) if ch.isdigit() or ch in ",.-")
    comma, dot = text.rfind(","), text.rfind(".")
    if comma >= 0 and dot >= 0:
        text = text.replace(".", "").replace(",", ".") if comma > dot else text.replace(",", "")
    elif comma >= 0:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0
