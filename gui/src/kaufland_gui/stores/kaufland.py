"""Kaufland Card digital receipts, through the kaufland_cli package."""

from __future__ import annotations

from collections.abc import Callable

from kaufland_cli import auth, receipts
from kaufland_cli.config import KauflandError, token_path

from .base import Progress, Store, StoreError


class KauflandStore(Store):
    id = "kaufland"
    name = "Kaufland"
    description = "Kaufland Card digital receipts"
    color = "#E10915"
    auth = "password"

    def connected(self) -> bool:
        return token_path().exists()

    def account_label(self) -> str:
        return self.read_state("account.json", {}).get("label", "")

    def login(self, email: str, password: str, *, otp: Callable[[], str] | None = None) -> str:
        try:
            info = auth.login(email, password, otp=otp)
        except KauflandError as exc:
            raise StoreError(str(exc)) from exc
        label = info.get("email") or email
        self.write_state("account.json", {"label": label})
        return label

    def logout(self) -> None:
        auth.logout()
        self.delete_state("account.json")

    def fetch(self, limit: int, *, cached: list[dict], progress: Progress | None = None) -> list[dict]:
        if progress:
            progress("Kaufland: downloading receipts")
        try:
            raw = receipts.fetch(limit or 20, fetch_all=limit == 0)
        except KauflandError as exc:
            raise StoreError(str(exc)) from exc
        return [normalize(r) for r in raw]


def normalize(receipt: dict) -> dict:
    summary = receipts.summaries([receipt])[0]
    items = [
        {
            "name": i["name"],
            "quantity": i["quantity"],
            "unit": i["unit"],
            "unit_price": i["unit_price"],
            "total": i["total"],
            "refund": i["refund"],
        }
        for i in receipts.line_items([receipt])
    ]
    return {
        "uid": f"kaufland:{summary['receipt_id']}",
        "source": "kaufland",
        "id": str(summary["receipt_id"]),
        "merchant": "Kaufland",
        "branch": summary["store"],
        "city": summary["city"],
        "timestamp": summary["timestamp"],
        "currency": summary["currency"],
        "total": summary["total"],
        "saving": summary["saving"],
        "points": summary["bonus_points"],
        "items": items,
    }
