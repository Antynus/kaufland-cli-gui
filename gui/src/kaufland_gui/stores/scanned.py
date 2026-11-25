"""Receipts the user adds by hand: scanned fiscal QR codes, photos, or typed in.

Works for any shop. A fiscal QR code gives the date, time, total and receipt id;
the shop name is chosen by the user and remembered.
"""

from __future__ import annotations

import time
import uuid

from .. import qr
from .base import Progress, Store, StoreError


class ScannedStore(Store):
    id = "scanned"
    name = "Scanned & manual"
    description = "Fiscal QR codes and receipts you add yourself, from any shop"
    color = "#0F766E"
    auth = "none"
    local = True

    def receipts(self) -> list[dict]:
        return self.read_state("receipts.json", [])

    def fetch(self, limit: int, *, cached: list[dict], progress: Progress | None = None) -> list[dict]:
        return self.receipts()

    def find_fiscal(self, key: str) -> dict | None:
        key = key.lower()
        return next((r for r in self.receipts() if (r.get("fiscal") or {}).get("key") == key), None)

    def add(
        self,
        *,
        merchant: str,
        total: float,
        timestamp: str,
        currency: str = "EUR",
        city: str = "",
        note: str = "",
        items: list[dict] | None = None,
        fiscal: qr.FiscalReceipt | None = None,
    ) -> dict:
        merchant = merchant.strip()
        if not merchant:
            raise StoreError("Enter the shop name.")
        if fiscal and self.find_fiscal(fiscal.key):
            raise StoreError("This receipt is already saved.")
        rid = fiscal.key if fiscal else uuid.uuid4().hex[:16]
        receipt = {
            "uid": f"scanned:{rid}",
            "source": "scanned",
            "id": rid,
            "merchant": merchant,
            "branch": merchant,
            "city": city.strip() or None,
            "timestamp": timestamp,
            "currency": currency,
            "total": round(float(total), 2),
            "saving": 0.0,
            "items": items or [],
            "note": note.strip(),
            "added_at": time.time(),
        }
        if fiscal:
            receipt["fiscal"] = {"jir": fiscal.jir, "zki": fiscal.zki, "key": fiscal.key, "url": fiscal.url}
        rows = self.receipts()
        rows.append(receipt)
        rows.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
        self.write_state("receipts.json", rows)
        return receipt

    def update(self, uid: str, **changes) -> None:
        rows = self.receipts()
        for row in rows:
            if row["uid"] == uid:
                row.update(changes)
                if "merchant" in changes:
                    row["branch"] = changes["merchant"]
        self.write_state("receipts.json", rows)

    def delete(self, uid: str) -> None:
        self.write_state("receipts.json", [r for r in self.receipts() if r["uid"] != uid])
