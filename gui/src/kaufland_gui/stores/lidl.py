"""Lidl Plus digital receipts.

Sign-in uses the Lidl Plus app's own OAuth client. The login page is protected by
reCAPTCHA and bot detection, so it must run in a real browser: an in-app WebView on
Android, or a Chrome/Edge window on PC (see browser_login.py). The page finally
redirects to ``com.lidlplus.app://callback?code=...``; that code is exchanged for
tokens here, and the refresh token keeps the session alive afterwards.

Receipts: ``tickets.lidlplus.com/api/v2/<country>/tickets`` lists them and
``/api/v3/<country>/tickets/<id>`` returns one. Depending on the country a receipt
carries an ``itemsLine`` array or only ``htmlPrintedReceipt``, a rendering of the
paper receipt whose spans hold the item data in ``data-*`` attributes.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import time
import urllib.parse
from typing import Any

from kaufland_cli import http

from .base import Progress, Store, StoreError, amount

AUTH_BASE = "https://accounts.lidl.com"
TICKETS_BASE = "https://tickets.lidlplus.com/api"
CLIENT_ID = "LidlPlusNativeClient"
CLIENT_SECRET = "secret"  # the app's public client secret
REDIRECT_URI = "com.lidlplus.app://callback"
SCOPE = "openid profile offline_access lpprofile lpapis"
PAGE_SIZE_GUESS = 25
LANGUAGES = {
    "HR": "hr",
    "PL": "pl",
    "DE": "de",
    "CZ": "cs",
    "SK": "sk",
    "RO": "ro",
    "BG": "bg",
    "SI": "sl",
    "AT": "de",
    "HU": "hu",
    "RS": "sr",
    "BA": "bs",
}


def country() -> str:
    return os.getenv("KAUFLAND_COUNTRY", "HR").upper()


class LidlStore(Store):
    id = "lidl"
    name = "Lidl"
    description = "Lidl Plus digital receipts"
    color = "#0050AA"
    auth = "browser"

    def __init__(self) -> None:
        self._pending: dict[str, str] | None = None

    # ---------- auth ----------

    def connected(self) -> bool:
        return bool(self.read_state("tokens.json", {}).get("refresh_token"))

    def account_label(self) -> str:
        return self.read_state("tokens.json", {}).get("label", "")

    def callback_prefix(self) -> str:
        return REDIRECT_URI

    def login_url(self) -> str:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        self._pending = {"verifier": verifier, "state": state}
        code = country()
        query = urllib.parse.urlencode(
            {
                "client_id": CLIENT_ID,
                "response_type": "code",
                "scope": SCOPE,
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "Country": code,
                "language": f"{LANGUAGES.get(code, 'en')}-{code}",
            }
        )
        return f"{AUTH_BASE}/connect/authorize?{query}"

    def complete_login(self, callback_url: str) -> str:
        if not self._pending:
            raise StoreError("Start the Lidl sign-in again: no sign-in is in progress.")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(callback_url.strip()).query)
        if "error" in query:
            raise StoreError(f"Lidl refused the sign-in: {query['error'][0]}")
        code = (query.get("code") or [""])[0]
        if not code:
            raise StoreError(
                "That link has no sign-in code. Copy the whole com.lidlplus.app://callback… link."
            )
        state = (query.get("state") or [""])[0]
        if state and state != self._pending["state"]:
            raise StoreError("That link belongs to an older sign-in. Start again.")
        tokens = self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": self._pending["verifier"],
            }
        )
        self._pending = None
        label = _label_from_id_token(tokens.get("id_token")) or "Lidl Plus"
        self._save(tokens, label=label)
        return label

    def transfer_code(self) -> str:
        """A string that signs another device in (contains the refresh token)."""
        tokens = self.read_state("tokens.json", {})
        if not tokens.get("refresh_token"):
            raise StoreError("Lidl is not connected on this device.")
        payload = {"r": tokens["refresh_token"], "l": tokens.get("label", "")}
        return "lidl1:" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    def set_token(self, token: str) -> str:
        token = token.strip()
        if token.startswith("lidl1:"):
            try:
                payload = json.loads(base64.urlsafe_b64decode(token[6:].encode()))
            except ValueError as exc:
                raise StoreError("That transfer code is damaged. Copy it again.") from exc
            refresh, label = payload["r"], payload.get("l") or "Lidl Plus"
        elif token.startswith(REDIRECT_URI):
            return self.complete_login(token)
        else:
            refresh, label = token, "Lidl Plus"
        tokens = self._token({"grant_type": "refresh_token", "refresh_token": refresh})
        self._save(tokens, label=label)
        return label

    def logout(self) -> None:
        self.delete_state("tokens.json")

    def _token(self, form: dict[str, str]) -> dict:
        basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        status, payload = http.request_json(
            "POST",
            AUTH_BASE + "/connect/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            body=urllib.parse.urlencode(form).encode(),
        )
        if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
            detail = payload.get("error") if isinstance(payload, dict) else payload
            if detail == "invalid_grant" and form.get("grant_type") == "authorization_code":
                raise StoreError(
                    "Lidl rejected the sign-in code (they expire after a few minutes). Try again."
                )
            if detail == "invalid_grant":
                raise StoreError("The Lidl session expired. Connect Lidl again.")
            raise StoreError(f"Lidl sign-in failed (HTTP {status}: {detail})")
        return payload

    def _save(self, payload: dict, *, label: str | None = None) -> dict:
        old = self.read_state("tokens.json", {})
        tokens = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or old.get("refresh_token"),
            "expires_at": time.time() + float(payload.get("expires_in", 3600)),
            "label": label or old.get("label", ""),
        }
        self.write_state("tokens.json", tokens, private=True)
        return tokens

    def _access_token(self, force: bool = False) -> str:
        tokens = self.read_state("tokens.json", {})
        if not tokens.get("refresh_token"):
            raise StoreError("Lidl is not connected.")
        if force or tokens.get("expires_at", 0) < time.time() + 60:
            tokens = self._save(
                self._token({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
            )
        return tokens["access_token"]

    # ---------- receipts ----------

    def _get(self, url: str) -> Any:
        for force in (False, True):
            status, payload = http.request_json(
                "GET",
                url,
                headers={
                    "Authorization": f"Bearer {self._access_token(force)}",
                    # The backend rejects the bogus "999.99.9" some clients send, and the
                    # v3 detail endpoint wants a two-letter Accept-Language.
                    "App-Version": "16.30.0",
                    "Operating-System": "iOS",
                    "App": "com.lidl.eci.lidl.plus",
                    "Accept-Language": country(),
                    "Accept": "application/json",
                },
            )
            if status != 401:
                break
        if status != 200:
            raise StoreError(f"Lidl receipts returned HTTP {status}")
        return payload

    def fetch(self, limit: int, *, cached: list[dict], progress: Progress | None = None) -> list[dict]:
        code = country()
        known = {r["id"]: r for r in cached}
        summaries: list[dict] = []
        page = 1
        while True:
            if progress:
                progress(f"Lidl: listing receipts (page {page})")
            data = self._get(f"{TICKETS_BASE}/v2/{code}/tickets?pageNumber={page}&onlyFavorite=false")
            batch = (data or {}).get("tickets") or []
            summaries.extend(batch)
            total = int((data or {}).get("totalCount") or 0)
            if not batch or len(summaries) >= total or (limit and len(summaries) >= limit):
                break
            page += 1
        if limit:
            summaries = summaries[:limit]

        out = []
        for n, summary in enumerate(summaries, 1):
            ticket_id = str(summary.get("id"))
            if ticket_id in known and known[ticket_id].get("items"):
                out.append(known[ticket_id])
                continue
            if progress:
                progress(f"Lidl: receipt {n} of {len(summaries)}")
            try:
                detail = self._get(f"{TICKETS_BASE}/v3/{code}/tickets/{urllib.parse.quote(ticket_id)}")
            except StoreError:
                detail = {}
            out.append(normalize(summary, detail or {}))
        return out


def _label_from_id_token(token: str | None) -> str:
    if not token or token.count(".") < 2:
        return ""
    try:
        body = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return ""
    return claims.get("email") or claims.get("name") or claims.get("phone_number") or ""


def normalize(summary: dict, detail: dict) -> dict:
    ticket = {**summary, **detail}
    store = ticket.get("store") if isinstance(ticket.get("store"), dict) else {}
    currency = ticket.get("currency")
    if isinstance(currency, dict):
        currency = currency.get("code") or currency.get("symbol")
    items: list[dict] = []
    saving = amount(ticket.get("totalDiscount"))

    for line in ticket.get("itemsLine") or []:
        if not isinstance(line, dict):
            continue
        discounts = sum(amount(d.get("amount")) for d in line.get("discounts") or [] if isinstance(d, dict))
        qty = amount(line.get("quantity")) or 1
        items.append(
            {
                "name": (line.get("description") or "").strip(),
                "quantity": qty,
                "unit": "kg" if line.get("isWeight") else "",
                "unit_price": amount(line.get("currentUnitPrice")),
                "total": round(amount(line.get("originalAmount")) - discounts, 2),
                "refund": False,
            }
        )
        if not ticket.get("totalDiscount"):
            saving += discounts

    if not items and ticket.get("htmlPrintedReceipt"):
        parsed, html_saving, html_currency = parse_printed_receipt(ticket["htmlPrintedReceipt"])
        items = parsed
        saving = saving or html_saving
        currency = currency or html_currency

    address = ", ".join(x for x in (store.get("address"), store.get("locality")) if x)
    return {
        "uid": f"lidl:{ticket.get('id')}",
        "source": "lidl",
        "id": str(ticket.get("id")),
        "merchant": "Lidl",
        "branch": store.get("name") or address or "Lidl",
        "city": store.get("locality") or store.get("city"),
        "timestamp": ticket.get("date"),
        "currency": currency or "EUR",
        "total": amount(ticket.get("totalAmount")),
        "saving": round(abs(saving), 2),
        "items": items,
    }


_SPAN = re.compile(r"<span\b([^>]*)>(.*?)</span>", re.S)
_ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_TRAILING = re.compile(r"(-?\d[\d., ]*)\s*(?:[A-Z]\d{0,2})?\s*$")
_CURRENCY = re.compile(r"\b(EUR|PLN|HUF|CZK|RON|BGN|RSD|BAM|Ft|Kč|zł|lei|€)\b")


def parse_printed_receipt(doc: str) -> tuple[list[dict], float, str | None]:
    """Extract (items, discounts, currency) from ``htmlPrintedReceipt``."""
    lines: dict[str, dict] = {}
    order: list[str] = []
    for n, (raw_attrs, inner) in enumerate(_SPAN.findall(doc)):
        attrs = dict(_ATTR.findall(raw_attrs))
        if "article" not in attrs.get("class", "").split() and "discount" not in attrs.get("class", ""):
            continue
        key = attrs.get("id") or f"span{n}"
        if key not in lines:
            lines[key] = {"text": "", "attrs": {}, "classes": set()}
            order.append(key)
        line = lines[key]
        line["text"] += html.unescape(re.sub(r"<[^>]+>", "", inner))
        line["classes"].update(attrs.get("class", "").split())
        line["attrs"].update({k: html.unescape(v) for k, v in attrs.items() if k.startswith("data-")})

    items: list[dict] = []
    saving = 0.0
    for key in order:
        line = lines[key]
        text = line["text"].strip()
        attrs = line["attrs"]
        if "*" in text:
            continue  # "2 * 1,99" breakdown line
        match = _TRAILING.search(text)
        value = amount(match.group(1)) if match else None
        name = (attrs.get("data-art-description") or "").strip()
        if not name or "article" not in line["classes"]:
            if value and value < 0:
                saving += value
            continue
        if value is None:
            continue  # weight breakdown line ("0,792 kg x 1,39 EUR/kg")
        if value < 0 and items:
            items[-1]["total"] = round(items[-1]["total"] + value, 2)
            saving += value
            continue
        qty = amount(attrs.get("data-art-quantity")) or 1
        unit_price = amount(attrs.get("data-unit-price"))
        items.append(
            {
                "name": name,
                "quantity": qty,
                "unit": "kg" if "kg" in text.lower() or (qty and qty != int(qty)) else "",
                "unit_price": unit_price,
                "total": value if value else round(qty * unit_price, 2),
                "refund": False,
            }
        )
    found = _CURRENCY.search(doc)
    symbol = found.group(1) if found else None
    currency = {"€": "EUR", "Ft": "HUF", "Kč": "CZK", "zł": "PLN", "lei": "RON"}.get(symbol, symbol)
    return items, abs(saving), currency
