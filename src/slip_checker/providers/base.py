from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from pathlib import Path


class ApiProvider(ABC):
    """A store whose loyalty app exposes the receipt history over an API.

    Rows from ``summaries`` and ``line_items`` share one column layout across providers
    so their CSV output can be concatenated.
    """

    key: str
    title: str

    def add_login_args(self, parser: argparse.ArgumentParser) -> None:  # noqa: B027 - optional hook
        pass

    @abstractmethod
    def login(self, args: argparse.Namespace) -> str:
        """Sign in, store tokens and return a line to show the user."""

    @abstractmethod
    def logout(self) -> Path | None: ...

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def fetch(self, limit: int, *, fetch_all: bool = False, details: bool = True) -> list[dict]:
        """Return raw receipts, newest first. ``details`` asks for line items where that costs extra."""

    @abstractmethod
    def summaries(self, receipts: list[dict]) -> list[dict]: ...

    @abstractmethod
    def line_items(self, receipts: list[dict]) -> list[dict]: ...


def summary_row(
    chain: str,
    *,
    receipt_id,
    timestamp,
    store,
    city,
    total,
    saving,
    currency,
    items,
    bonus_points=None,
) -> dict:
    return {
        "chain": chain,
        "receipt_id": receipt_id,
        "timestamp": timestamp,
        "store": store,
        "city": city,
        "total": total,
        "saving": saving,
        "currency": currency,
        "items": items,
        "bonus_points": bonus_points,
    }


def item_row(
    chain: str,
    *,
    receipt_id,
    timestamp,
    store,
    city,
    name,
    quantity,
    unit,
    unit_price,
    total,
    discount,
    currency,
    gtin,
    refund=False,
) -> dict:
    return {
        "chain": chain,
        "receipt_id": receipt_id,
        "timestamp": timestamp,
        "store": store,
        "city": city,
        "name": name,
        "quantity": quantity,
        "unit": unit,
        "unit_price": unit_price,
        "total": total,
        "discount": discount,
        "currency": currency,
        "gtin": gtin,
        "refund": refund,
    }
