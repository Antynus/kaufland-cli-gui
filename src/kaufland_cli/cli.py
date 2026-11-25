from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Any

from . import stores
from .config import StoreError


def _emit(rows: Any, output: str) -> None:
    if output == "json" or not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def _add_store_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store",
        default=stores.DEFAULT_STORE,
        choices=stores.names(),
        help=f"retailer to use (default: {stores.DEFAULT_STORE})",
    )


def _add_fetch_args(parser: argparse.ArgumentParser, *, tabular: bool = True) -> None:
    _add_store_arg(parser)
    parser.add_argument("-n", "--limit", type=int, default=20, help="number of receipts (default: 20)")
    parser.add_argument("--all", action="store_true", dest="fetch_all", help="fetch the whole history")
    if tabular:
        parser.add_argument("--output", choices=("json", "csv"), default="json", help="output format")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="receipts", description="Fetch loyalty-card digital receipts.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stores", help="list supported stores")

    login = sub.add_parser("login", help="sign in and store tokens")
    _add_store_arg(login)
    login.add_argument("--email", help="account email (or the store's *_EMAIL env var)")

    _add_store_arg(sub.add_parser("status", help="show the saved login"))
    _add_store_arg(sub.add_parser("logout", help="delete the stored tokens"))

    _add_fetch_args(sub.add_parser("receipts", help="full receipts as returned by the API"), tabular=False)
    _add_fetch_args(sub.add_parser("items", help="one row per purchased item"))
    _add_fetch_args(sub.add_parser("list", help="one row per receipt"))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "stores":
            print("\n".join(f"{name}  ({stores.get_store(name).display_name})" for name in stores.names()))
            return 0

        store = stores.get_store(args.store)

        if args.command == "login":
            info = store.login(args.email)
            who = info.get("email") or info.get("sub") or info.get("via") or ""
            print(f"logged in to {store.display_name}" + (f" as {who}" if who else ""))
            return 0
        if args.command == "status":
            print(json.dumps(store.status(), indent=2))
            return 0
        if args.command == "logout":
            path = store.logout()
            print(f"removed {path}" if path else "no saved login")
            return 0

        fetched = store.fetch(args.limit, fetch_all=args.fetch_all)
        if args.command == "receipts":
            _emit(fetched, "json")
        elif args.command == "items":
            _emit(store.line_items(fetched), args.output)
        else:
            _emit(store.summaries(fetched), args.output)
        return 0
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
