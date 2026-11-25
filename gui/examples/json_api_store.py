"""Example plug-in: receipts from any HTTP API that returns JSON.

Copy this file, change the constants and ``to_receipt`` to match your API, then
install it from Stores › Plug-ins › Install plug-in. The user connects by pasting
an API token, which is stored on the device and sent as a Bearer token.

The API is expected to return either a list of receipts or {"receipts": [...]}.
"""

from __future__ import annotations

import json
import urllib.request

from kaufland_gui.stores.base import Store, StoreError, amount

API_URL = "https://example.com/api/receipts"  # change me


class JsonApiStore(Store):
    id = "my-json-api"  # unique, lowercase, no spaces
    name = "My shop"
    description = "Receipts from my shop's API"
    color = "#9333EA"
    auth = "token"

    def connected(self) -> bool:
        return bool(self.read_state("token.json", {}).get("token"))

    def account_label(self) -> str:
        return "API token"

    def set_token(self, token: str) -> str:
        if not token.strip():
            raise StoreError("Paste a token.")
        self.write_state("token.json", {"token": token.strip()}, private=True)
        return "API token"

    def logout(self) -> None:
        self.delete_state("token.json")

    def fetch(self, limit, *, cached, progress=None):
        token = self.read_state("token.json", {}).get("token")
        request = urllib.request.Request(
            API_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except OSError as exc:
            raise StoreError(f"could not reach the API: {exc}") from exc
        rows = payload.get("receipts", []) if isinstance(payload, dict) else payload
        receipts = [self.to_receipt(r) for r in rows]
        return receipts[:limit] if limit else receipts

    def to_receipt(self, row: dict) -> dict:
        """Map one API record to the app's receipt format (see stores/base.py)."""
        return {
            "uid": f"{self.id}:{row['id']}",
            "source": self.id,
            "id": str(row["id"]),
            "merchant": row.get("shop") or self.name,
            "branch": row.get("branch"),
            "city": row.get("city"),
            "timestamp": row.get("date"),  # ISO 8601
            "currency": row.get("currency", "EUR"),
            "total": amount(row.get("total")),
            "saving": amount(row.get("discount")),
            "items": [
                {
                    "name": item.get("name"),
                    "quantity": item.get("quantity", 1),
                    "unit": item.get("unit", ""),
                    "unit_price": amount(item.get("price")),
                    "total": amount(item.get("total")),
                    "refund": False,
                }
                for item in row.get("items", [])
            ],
        }


STORE = JsonApiStore()
