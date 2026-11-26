from __future__ import annotations

import argparse
from pathlib import Path

from ..base import ApiProvider
from . import auth, receipts


class KauflandProvider(ApiProvider):
    key = "kaufland"
    title = "Kaufland Card"

    def add_login_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--email", help="account email (or KAUFLAND_EMAIL)")

    def login(self, args: argparse.Namespace) -> str:
        info = auth.login(getattr(args, "email", None))
        return f"logged in to Kaufland as {info.get('email') or info['sub']}"

    def logout(self) -> Path | None:
        return auth.logout()

    def status(self) -> dict:
        return auth.status()

    def fetch(self, limit: int, *, fetch_all: bool = False, details: bool = True) -> list[dict]:
        return receipts.fetch(limit, fetch_all=fetch_all)

    def summaries(self, raw: list[dict]) -> list[dict]:
        return receipts.summaries(raw)

    def line_items(self, raw: list[dict]) -> list[dict]:
        return receipts.line_items(raw)
