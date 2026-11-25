"""Store registry — one connector per retailer, keyed by its CLI name."""

from __future__ import annotations

from ..config import StoreError
from .base import Store
from .kaufland import KauflandStore
from .lidl import LidlStore

_REGISTRY: dict[str, Store] = {s.name: s for s in (KauflandStore(), LidlStore())}

DEFAULT_STORE = "kaufland"


def get_store(name: str) -> Store:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise StoreError(f"unknown store {name!r}; choose from: {', '.join(names())}") from None


def names() -> list[str]:
    return sorted(_REGISTRY)


__all__ = ["Store", "get_store", "names", "DEFAULT_STORE"]
