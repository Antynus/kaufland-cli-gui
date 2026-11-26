"""Lidl Plus receipts from the mobile app API (tickets.lidlplus.com).

Endpoints and headers follow the community client https://github.com/Andre0512/lidl-plus.
Lidl's login page is protected by reCAPTCHA, so this provider does not sign in with a
password. It takes a refresh token obtained once in a real browser (for example with
``lidl-plus auth``) and keeps it fresh from then on.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .. import http
from ..config import SlipError, read_json, store_dir, write_private_json
from ..output import to_float
from .base import ApiProvider, item_row, summary_row

AUTH_URL = "https://accounts.lidl.com/connect/token"
TICKETS_API = "https://tickets.lidlplus.com/api/v2"
CLIENT_ID = "LidlPlusNativeClient"
APP_ID = "com.lidl.eci.lidl.plus"
DEFAULT_APP_VERSION = "999.99.9"


def country() -> str:
    value = os.getenv("LIDL_COUNTRY", "HR").upper()
    if len(value) != 2 or not value.isalpha():
        raise SlipError("LIDL_COUNTRY must be a two-letter country code")
    return value


def language() -> str:
    return os.getenv("LIDL_LANGUAGE") or country().lower()


def token_path() -> Path:
    return store_dir("lidl") / "tokens.json"


def _token_request(form: dict[str, str]) -> dict:
    basic = base64.b64encode(f"{CLIENT_ID}:secret".encode()).decode()
    status, payload = http.request_json(
        "POST",
        AUTH_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        body=urllib.parse.urlencode(form).encode(),
    )
    if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
        raise SlipError(f"Lidl token endpoint returned HTTP {status}: {payload}")
    return payload


def _store_tokens(payload: dict, previous_refresh: str) -> dict:
    tokens = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or previous_refresh,
        "expires_at": time.time() + float(payload.get("expires_in", 3600)),
        "country": country(),
    }
    write_private_json(token_path(), tokens)
    return tokens


def _refresh(refresh_token: str) -> dict:
    payload = _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
    return _store_tokens(payload, refresh_token)


def _load_tokens() -> dict:
    path = token_path()
    if not path.exists():
        raise SlipError("no saved Lidl Plus login; run: slip lidl login")
    return read_json(path)


def access_token(*, force_refresh: bool = False) -> str:
    tokens = _load_tokens()
    if not force_refresh and tokens.get("expires_at", 0) > time.time() + 60:
        return tokens["access_token"]
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise SlipError("Lidl Plus token expired and no refresh token is saved; run: slip lidl login")
    return _refresh(refresh)["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "App-Version": os.getenv("LIDL_APP_VERSION", DEFAULT_APP_VERSION),
        "Operating-System": "iOs",
        "App": APP_ID,
        "Accept-Language": language(),
    }


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{TICKETS_API}/{country()}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, payload = http.request_json("GET", url, headers=_headers(access_token()))
    if status == 401:
        status, payload = http.request_json("GET", url, headers=_headers(access_token(force_refresh=True)))
    if status != 200:
        raise SlipError(f"Lidl Plus {path} returned HTTP {status}: {payload!r:.300}")
    return payload


def _amount(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("amount", value.get("value"))
    return to_float(value)


def _currency(ticket: dict) -> str | None:
    currency = ticket.get("currency")
    if isinstance(currency, dict):
        return currency.get("code")
    return currency or ("EUR" if country() == "HR" else None)


def _store(ticket: dict) -> dict:
    store = ticket.get("store")
    return store if isinstance(store, dict) else {"name": store} if store else {}


def _discount(line: dict) -> float:
    return round(sum(_amount(d.get("amount")) or 0.0 for d in line.get("discounts") or []), 2)


class LidlProvider(ApiProvider):
    key = "lidl"
    title = "Lidl Plus"

    def add_login_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--refresh-token",
            help="Lidl Plus refresh token (or LIDL_REFRESH_TOKEN); see README for how to get one",
        )

    def login(self, args: argparse.Namespace) -> str:
        refresh = (
            getattr(args, "refresh_token", None)
            or os.getenv("LIDL_REFRESH_TOKEN")
            or getpass.getpass("Lidl Plus refresh token: ").strip()
        )
        if not refresh:
            raise SlipError("a refresh token is required")
        _refresh(refresh)
        return f"Lidl Plus token saved for country {country()}"

    def logout(self) -> Path | None:
        path = token_path()
        if not path.exists():
            return None
        path.unlink()
        return path

    def status(self) -> dict:
        tokens = _load_tokens()
        expires_at = tokens.get("expires_at", 0)
        return {
            "token_file": str(token_path()),
            "country": tokens.get("country") or country(),
            "access_token_valid": expires_at > time.time(),
            "expires_in_seconds": max(0, int(expires_at - time.time())),
            "has_refresh_token": bool(tokens.get("refresh_token")),
        }

    def fetch(self, limit: int, *, fetch_all: bool = False, details: bool = True) -> list[dict]:
        tickets: list[dict] = []
        page = 1
        while True:
            data = _get("tickets", {"pageNumber": page, "onlyFavorite": "false"})
            batch = data.get("tickets") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                raise SlipError(f"unexpected Lidl Plus response: {data!r:.300}")
            tickets.extend(batch)
            size = data.get("size") or len(batch)
            total = data.get("totalCount") or 0
            if not batch or page * size >= total or (not fetch_all and len(tickets) >= limit):
                break
            page += 1
        if not fetch_all:
            tickets = tickets[:limit]
        if details:
            tickets = [{**t, **_get(f"tickets/{urllib.parse.quote(str(t['id']), safe='')}")} for t in tickets]
        return tickets

    def summaries(self, raw: list[dict]) -> list[dict]:
        rows = []
        for ticket in raw:
            store = _store(ticket)
            lines = ticket.get("itemsLine") or []
            saving = ticket.get("totalDiscount")
            rows.append(
                summary_row(
                    "lidl",
                    receipt_id=ticket.get("id"),
                    timestamp=ticket.get("date"),
                    store=store.get("name"),
                    city=store.get("locality") or store.get("city"),
                    total=_amount(ticket.get("totalAmount")),
                    saving=_amount(saving) if saving is not None else round(sum(map(_discount, lines)), 2),
                    currency=_currency(ticket),
                    items=ticket.get("articlesCount") or len(lines),
                )
            )
        return rows

    def line_items(self, raw: list[dict]) -> list[dict]:
        rows = []
        for ticket in raw:
            store = _store(ticket)
            for line in ticket.get("itemsLine") or []:
                if not isinstance(line, dict):
                    continue
                rows.append(
                    item_row(
                        "lidl",
                        receipt_id=ticket.get("id"),
                        timestamp=ticket.get("date"),
                        store=store.get("name"),
                        city=store.get("locality") or store.get("city"),
                        name=line.get("name"),
                        quantity=_amount(line.get("quantity")),
                        unit="kg" if line.get("isWeight") else None,
                        unit_price=_amount(line.get("currentUnitPrice")),
                        total=_amount(line.get("originalAmount")),
                        discount=_discount(line),
                        currency=_currency(ticket),
                        gtin=line.get("codeInput"),
                    )
                )
        return rows
