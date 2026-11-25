"""Lidl Plus digital receipts ("tickets").

Auth is OAuth2 + PKCE against accounts.lidl.com (IdentityServer); receipts come
from tickets.lidlplus.com. Endpoints and the native client id are the ones the
public `lidl-plus` project documents.

Scope of confidence:
- token refresh, the tickets list/detail calls and the data mapping are
  implemented from the documented API and should be correct;
- the *interactive* credential login against accounts.lidl.com is EXPERIMENTAL
  (its hosted-login form and 2FA cannot be verified without a live account).
  If `login` fails, seed a refresh token obtained from the official flow via
  LIDL_REFRESH_TOKEN and the connector will refresh from there.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
import secrets
import time
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .. import http
from ..config import BROWSER_UA, StoreError
from .base import Store

AUTH_BASE = "https://accounts.lidl.com"
TICKETS_BASE = "https://tickets.lidlplus.com"
AUTHORIZE_URL = f"{AUTH_BASE}/connect/authorize"
TOKEN_URL = f"{AUTH_BASE}/connect/token"
LOGIN_SRV = f"{AUTH_BASE}/login-srv/login"

CLIENT_ID = "LidlPlusNativeClient"
CLIENT_SECRET = "secret"
REDIRECT_URI = "com.lidlplus.app://callback"
CALLBACK_SCHEME = "com.lidlplus.app://"
SCOPE = "openid profile offline_access lpprofile lpapis"
APP_VERSION = "15.34.3"
OPERATING_SYSTEM = "android"
MAX_REDIRECT_HOPS = 10


def _basic_auth() -> str:
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _country() -> str:
    value = os.getenv("LIDL_COUNTRY", "HR").upper()
    if len(value) != 2 or not value.isalpha():
        raise StoreError("LIDL_COUNTRY must be a two-letter country code")
    return value


def _language() -> str:
    return os.getenv("LIDL_LANGUAGE", _country().lower())


def _decimal(value: Any) -> float:
    """Lidl amounts are locale decimal strings like '1,99' (not minor units)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("\xa0", "").replace(" ", "")
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0


class LidlStore(Store):
    name = "lidl"
    display_name = "Lidl Plus"

    # -- tokens ---------------------------------------------------------

    def _store_tokens(self, payload: Mapping[str, Any], *, previous_refresh: str | None = None) -> dict:
        record = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or previous_refresh,
            "expires_at": time.time() + float(payload.get("expires_in", 3600)),
        }
        self._tokens().save(record)
        return record

    def _token_request(self, form: Mapping[str, str]) -> dict:
        status, payload = http.request_json(
            "POST",
            TOKEN_URL,
            headers={
                "Accept": "application/json",
                "Authorization": _basic_auth(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=urllib.parse.urlencode(form).encode(),
        )
        if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
            raise StoreError(f"token endpoint returned HTTP {status}: {payload}")
        return payload

    def _seed_refresh_token(self) -> dict | None:
        seed = os.getenv("LIDL_REFRESH_TOKEN")
        if not seed:
            return None
        payload = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": seed, "client_id": CLIENT_ID}
        )
        return self._store_tokens(payload, previous_refresh=seed)

    def _access_token(self, *, force_refresh: bool = False) -> str:
        tokens = self._tokens().load_or_empty()
        if not tokens:
            seeded = self._seed_refresh_token()
            if not seeded:
                raise StoreError("no saved login for lidl; run: receipts login --store lidl")
            tokens = seeded
        if not force_refresh and tokens.get("expires_at", 0) > time.time() + 60:
            return tokens["access_token"]
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise StoreError("access token expired and no refresh token; run: receipts login --store lidl")
        payload = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT_ID}
        )
        return self._store_tokens(payload, previous_refresh=refresh)["access_token"]

    # -- login (experimental) ------------------------------------------

    @staticmethod
    def _pkce() -> tuple[str, str]:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return verifier, challenge

    def _authorize_url(self, challenge: str, state: str, nonce: str) -> str:
        return (
            AUTHORIZE_URL
            + "?"
            + urllib.parse.urlencode(
                {
                    "client_id": CLIENT_ID,
                    "response_type": "code",
                    "scope": SCOPE,
                    "redirect_uri": REDIRECT_URI,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                    "nonce": nonce,
                    "Country": _country(),
                    "language": f"{_language()}-{_country()}",
                }
            )
        )

    @staticmethod
    def _request_id(authorize_url: str) -> str:
        _, _, headers = http.request("GET", authorize_url, headers={"User-Agent": BROWSER_UA})
        location = http.header(headers, "Location") or ""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        for key in ("requestId", "request_id"):
            if key in query:
                return query[key][0]
        raise StoreError(
            "could not start Lidl login (no requestId from authorize); "
            "the hosted-login flow may have changed — seed LIDL_REFRESH_TOKEN instead"
        )

    def _follow_to_callback(self, url: str) -> str | None:
        for _ in range(MAX_REDIRECT_HOPS):
            if url.startswith(CALLBACK_SCHEME):
                return url
            _, _, headers = http.request("GET", url, headers={"User-Agent": BROWSER_UA})
            location = http.header(headers, "Location")
            if not location:
                return None
            url = urllib.parse.urljoin(url, location)
        return None

    def login(self, email: str | None = None, password: str | None = None) -> dict:
        # Prefer a seeded refresh token: reliable and avoids the hosted-login guesswork.
        seeded = self._seed_refresh_token()
        if seeded:
            return {"store": self.name, "via": "refresh_token"}

        email = email or os.getenv("LIDL_EMAIL") or input("Lidl email/phone: ").strip()
        password = password or os.getenv("LIDL_PASSWORD") or getpass.getpass("Password: ")

        verifier, challenge = self._pkce()
        state = secrets.token_urlsafe(16)
        nonce = secrets.token_urlsafe(16)
        authorize_url = self._authorize_url(challenge, state, nonce)
        request_id = self._request_id(authorize_url)

        status, raw, headers = http.request(
            "POST",
            LOGIN_SRV,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            body=http.encode_json(
                {"username": email, "password": password, "requestId": request_id}
            ),
        )
        payload = http.decode_json(raw) or {}
        redirect = http.header(headers, "Location") or payload.get("redirectUrl") or payload.get("location")
        if not redirect and not payload.get("success", status in (200, 204)):
            raise StoreError(
                f"Lidl login rejected (HTTP {status}): {payload or raw[:200]!r}. "
                "If your account needs SMS/2FA, the hosted flow differs — seed LIDL_REFRESH_TOKEN."
            )
        callback = self._follow_to_callback(redirect or authorize_url)
        if not callback:
            raise StoreError(
                "Lidl login did not reach the app callback (2FA or a changed flow); "
                "seed LIDL_REFRESH_TOKEN to use this connector"
            )
        params = urllib.parse.parse_qs(urllib.parse.urlparse(callback).query)
        code = (params.get("code") or [None])[0]
        if not code:
            raise StoreError(f"Lidl callback had no code: {callback}")

        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            }
        )
        self._store_tokens(payload)
        return {"store": self.name, "via": "password"}

    def status(self) -> dict:
        tokens = self._tokens().load()
        return {"store": self.name, "country": _country(), **self._base_status(tokens)}

    def logout(self) -> Path | None:
        return self._tokens().delete()

    # -- receipts -------------------------------------------------------

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "App-Version": APP_VERSION,
            "Operating-System": OPERATING_SYSTEM,
            "App-Country": _country(),
            "Accept-Language": f"{_language()}-{_country()}",
        }

    def _get(self, path: str) -> tuple[int, Any]:
        token = self._access_token()
        status, payload = http.request_json("GET", TICKETS_BASE + path, headers=self._headers(token))
        if status == 401:
            token = self._access_token(force_refresh=True)
            status, payload = http.request_json("GET", TICKETS_BASE + path, headers=self._headers(token))
        return status, payload

    @staticmethod
    def _tickets_of(payload: Any) -> list[dict]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("tickets", "records", "items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return []

    def fetch(self, limit: int = 20, *, fetch_all: bool = False) -> list[dict]:
        collected: list[dict] = []
        page = 1
        while True:
            status, payload = self._get(f"/api/v2/{_country()}/tickets?pageNumber={page}")
            if status != 200:
                raise StoreError(f"tickets endpoint returned HTTP {status}: {payload}")
            batch = self._tickets_of(payload)
            if not batch:
                break
            collected.extend(batch)
            if not fetch_all and len(collected) >= limit:
                break
            page += 1
            total = payload.get("totalCount") if isinstance(payload, dict) else None
            if isinstance(total, int) and len(collected) >= total:
                break
        return collected if fetch_all else collected[:limit]

    def _detail(self, ticket_id: str) -> dict:
        status, payload = self._get(f"/api/v3/{_country()}/tickets/{urllib.parse.quote(str(ticket_id), safe='')}")
        if status != 200 or not isinstance(payload, dict):
            raise StoreError(f"ticket detail returned HTTP {status}: {payload}")
        return payload

    @staticmethod
    def _ticket_id(ticket: dict) -> str | None:
        return ticket.get("id") or ticket.get("ticketId") or ticket.get("sequenceNumber")

    def line_items(self, receipts: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for ticket in receipts:
            ticket_id = self._ticket_id(ticket)
            detail = self._detail(ticket_id) if ticket_id else ticket
            lines = detail.get("itemsLine") or detail.get("items") or []
            store = detail.get("store") if isinstance(detail.get("store"), dict) else {}
            currency = detail.get("currency") or (detail.get("totalAmount") or {})
            for line in lines:
                if not isinstance(line, dict):
                    continue
                rows.append(
                    {
                        "receipt_id": ticket_id,
                        "timestamp": detail.get("date") or ticket.get("date"),
                        "store": store.get("name") or store.get("schwarzStoreId"),
                        "name": line.get("name"),
                        "quantity": _decimal(line.get("quantity", 1)),
                        "unit_price": _decimal(line.get("currentUnitPrice")),
                        "total": _decimal(line.get("originalAmount")),
                        "is_weight": line.get("isWeight"),
                        "tax_group": line.get("taxGroupName"),
                        "currency": currency if isinstance(currency, str) else None,
                    }
                )
        return rows

    def summaries(self, receipts: list[dict]) -> list[dict]:
        rows = []
        for ticket in receipts:
            store = ticket.get("store") if isinstance(ticket.get("store"), dict) else {}
            rows.append(
                {
                    "receipt_id": self._ticket_id(ticket),
                    "timestamp": ticket.get("date"),
                    "store": store.get("name") or store.get("schwarzStoreId"),
                    "total": _decimal(ticket.get("totalAmount")),
                    "currency": ticket.get("currency"),
                    "items": ticket.get("itemsCount") or ticket.get("articlesCount"),
                }
            )
        return rows
