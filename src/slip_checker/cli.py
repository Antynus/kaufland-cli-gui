from __future__ import annotations

import argparse
import json
import sys
import webbrowser

from . import fiscal, ledger
from .config import SlipError
from .output import emit
from .stores import STORES, api_stores


def _add_output_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", choices=("json", "csv"), default="json", help="output format")


def _add_fetch_args(parser: argparse.ArgumentParser, *, tabular: bool = True) -> None:
    parser.add_argument("-n", "--limit", type=int, default=20, help="number of receipts (default: 20)")
    parser.add_argument("--all", action="store_true", dest="fetch_all", help="fetch the whole history")
    if tabular:
        _add_output_arg(parser)


def _add_store_commands(sub: argparse._SubParsersAction) -> None:
    for store in api_stores():
        provider = store.provider
        parser = sub.add_parser(store.key, help=f"{store.title} receipts from the app API")
        actions = parser.add_subparsers(dest="action", required=True)
        provider.add_login_args(actions.add_parser("login", help="sign in and store tokens"))
        actions.add_parser("status", help="show the saved login")
        actions.add_parser("logout", help="delete the stored tokens")
        _add_fetch_args(
            actions.add_parser("receipts", help="full receipts as returned by the API"), tabular=False
        )
        _add_fetch_args(actions.add_parser("items", help="one row per purchased item"))
        _add_fetch_args(actions.add_parser("list", help="one row per receipt"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slip",
        description="Collect and check shopping receipts from loyalty apps and Croatian fiscal slips.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    stores = sub.add_parser("stores", help="list supported stores")
    _add_output_arg(stores)

    _add_store_commands(sub)

    add = sub.add_parser("add", help="add a fiscal slip from its QR code or printed fields")
    add.add_argument("store", choices=sorted(STORES), help="which store the slip is from")
    add.add_argument("qr", nargs="?", help="QR code text, e.g. https://porezna.gov.hr/rn?jir=...")
    add.add_argument("--jir", help="JIR printed on the receipt")
    add.add_argument("--zki", help="ZKI printed on the receipt (if there is no JIR)")
    add.add_argument("--date", help="issue date, YYYY-MM-DD")
    add.add_argument("--time", help="issue time, HH:MM")
    add.add_argument("--amount", help="total in euros, e.g. 12.34")
    add.add_argument("--note", help="free-text note")

    slips = sub.add_parser("slips", help="list saved fiscal slips")
    slips.add_argument("--store", choices=sorted(STORES), help="only this store")
    _add_output_arg(slips)

    verify = sub.add_parser("verify", help="show or open the Tax Administration check for a slip")
    verify.add_argument("slip_id", help="slip id from `slip slips` (or a prefix of its JIR/ZKI)")
    verify.add_argument("--open", action="store_true", help="open the check page in a browser")

    remove = sub.add_parser("remove", help="delete a saved fiscal slip")
    remove.add_argument("slip_id")

    return parser


def _run_store(args: argparse.Namespace) -> None:
    provider = STORES[args.command].provider
    if args.action == "login":
        print(provider.login(args))
    elif args.action == "status":
        print(json.dumps(provider.status(), indent=2))
    elif args.action == "logout":
        path = provider.logout()
        print(f"removed {path}" if path else "no saved login")
    else:
        details = args.action != "list"
        fetched = provider.fetch(args.limit, fetch_all=args.fetch_all, details=details)
        if args.action == "receipts":
            emit(fetched, "json")
        elif args.action == "items":
            emit(provider.line_items(fetched), args.output)
        else:
            emit(provider.summaries(fetched), args.output)


def _run_add(args: argparse.Namespace) -> None:
    if args.qr:
        slip = fiscal.parse_qr(args.qr)
    elif (args.jir or args.zki) and args.date and args.time and args.amount:
        slip = fiscal.from_fields(
            jir=args.jir, zki=args.zki, date=args.date, time=args.time, amount=args.amount
        )
    else:
        raise SlipError("give the QR code text, or --jir/--zki with --date, --time and --amount")
    entry, new = ledger.add(args.store, slip, args.note)
    if not new:
        print(f"already saved as {entry['id']} ({entry['store']})", file=sys.stderr)
    emit(entry)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "stores":
            rows = [{"store": s.key, "name": s.title, "source": s.source} for s in STORES.values()]
            emit(rows, args.output)
        elif args.command in STORES and STORES[args.command].provider:
            _run_store(args)
        elif args.command == "add":
            _run_add(args)
        elif args.command == "slips":
            emit(ledger.rows(args.store), args.output)
        elif args.command == "verify":
            entry = ledger.find(args.slip_id)
            print(entry["verify_url"])
            if args.open and not webbrowser.open(entry["verify_url"]):
                print("could not open a browser; open the link above", file=sys.stderr)
        elif args.command == "remove":
            entry = ledger.remove(args.slip_id)
            print(f"removed {entry['id']} ({entry['store']}, {entry['issued_at']}, {entry['amount']})")
        return 0
    except SlipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def kaufland_main() -> int:
    """The old `kaufland` command, kept as an alias for `slip kaufland`."""
    return main(["kaufland", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
