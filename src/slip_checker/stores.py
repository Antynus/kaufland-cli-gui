"""Every store the checker knows about and how its receipts get in.

``api`` stores pull the purchase history from the loyalty app. ``fiscal`` stores have no
known receipt API yet; their slips are added from the fiscal QR code on the paper receipt.
To promote a store to ``api``, write a provider in ``providers/`` and register it here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .providers.base import ApiProvider
from .providers.kaufland import KauflandProvider
from .providers.lidl import LidlProvider


@dataclass(frozen=True)
class Store:
    key: str
    title: str
    provider: ApiProvider | None = None

    @property
    def source(self) -> str:
        return "api" if self.provider else "fiscal"


STORES: dict[str, Store] = {
    s.key: s
    for s in (
        Store("kaufland", "Kaufland", KauflandProvider()),
        Store("lidl", "Lidl Plus", LidlProvider()),
        Store("spar", "SPAR"),
        Store("plodine", "Plodine"),
        Store("studenac", "Studenac"),
        Store("ikea", "IKEA"),
        Store("decathlon", "Decathlon"),
        Store("shell", "Shell"),
        Store("ina", "INA"),
        Store("multiplus", "Multiplus Card"),
        Store("offertisima", "Offertisima"),
        Store("other", "Any other Croatian shop"),
    )
}


def api_stores() -> list[Store]:
    return [s for s in STORES.values() if s.provider]
