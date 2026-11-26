"""Local store of fiscal slips added by hand or from QR codes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import SlipError, data_dir, read_json, write_private_json
from .fiscal import FiscalSlip


def path() -> Path:
    return data_dir() / "slips.json"


def load() -> list[dict]:
    file = path()
    if not file.exists():
        return []
    entries = read_json(file)
    if not isinstance(entries, list):
        raise SlipError(f"{file} is not a list of slips")
    return entries


def _save(entries: list[dict]) -> None:
    write_private_json(path(), entries)


def add(store: str, slip: FiscalSlip, note: str | None = None) -> tuple[dict, bool]:
    """Store a slip. Returns the entry and whether it was new."""
    entries = load()
    for entry in entries:
        if entry.get("code") == slip.code:
            return entry, False
    entry = {
        "id": slip.code.replace("-", "")[:8],
        "store": store,
        "issued_at": slip.issued_at.isoformat(timespec="minutes"),
        "amount": f"{slip.amount:.2f}",
        "currency": slip.currency,
        "code_type": "jir" if slip.jir else "zki",
        "code": slip.code,
        "verify_url": slip.verify_url,
        "note": note,
        "added_at": datetime.now().isoformat(timespec="seconds"),
    }
    entries.append(entry)
    entries.sort(key=lambda e: e.get("issued_at", ""), reverse=True)
    _save(entries)
    return entry, True


def find(slip_id: str) -> dict:
    key = slip_id.lower().replace("-", "")
    matches = [e for e in load() if e.get("id") == key or e.get("code", "").replace("-", "").startswith(key)]
    if not matches:
        raise SlipError(f"no slip matches {slip_id!r}; see: slip slips")
    if len(matches) > 1:
        raise SlipError(f"{slip_id!r} matches {len(matches)} slips; give more characters of the code")
    return matches[0]


def remove(slip_id: str) -> dict:
    entry = find(slip_id)
    _save([e for e in load() if e.get("code") != entry.get("code")])
    return entry


def rows(store: str | None = None) -> list[dict]:
    return [
        {k: e.get(k) for k in ("id", "store", "issued_at", "amount", "currency", "code_type", "code", "note")}
        | {"verify_url": e.get("verify_url")}
        for e in load()
        if store is None or e.get("store") == store
    ]
