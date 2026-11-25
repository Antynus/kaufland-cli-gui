"""Kaufland Card digital receipts.

OAuth2 + PKCE (browserless) against cidaas at account.kaufland.com; receipts
from the Schwarz CRM transactions API. Ported unchanged from the original
single-store implementation, now scoped to a per-store token file.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
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

CIDAAS_BASE = "https://account.kaufland.com"
API_BASE = "https://p.crm-dynamics.schwarz"
USERINFO_URL = f"{CIDAAS_BASE}/users-srv/userinfo"
AUTHZ_PATH = "/authz-srv/authz"
TOKEN_PATH = "/token-srv/token"
LOGIN_PATH = "/pages-app/login"
LOGIN_ROUTE = "routes/$pages/login"

CIDAAS_CLIENT_ID = "fb1b425b-ab2f-4140-aef9-20263b6cfa49"
LOYALTY_CLIENT_ID = "88207bfc-780b-400d-92ee-893ae72dab40"
REDIRECT_URI = "com.kaufland.kaufland://oauth/callback"
CALLBACK_SCHEME = "com.kaufland.kaufland://"
OAUTH_VERSION = "1.5.22"
APP_VERSION = "6.17.1"

MAX_PAGE_SIZE = 50
MAX_REDIRECT_HOPS = 10


def _country() -> str:
    value = os.getenv("KAUFLAND_COUNTRY", "PL").upper()
    if len(value) != 2 or not value.isalpha():
        raise StoreError("KAUFLAND_COUNTRY must be a two-letter country code")
    return value


def _ui_locales() -> str:
    code = _country()
    return os.getenv("KAUFLAND_UI_LOCALES", f"{code.lower()}-{code}")


def _minor(value: Any) -> float:
    if isinstance(value, str):
        value = value.replace(",", ".")
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return 0.0


class KauflandStore(Store):
    name = "kaufland"
    display_name = "Kaufland"

    # -- token helpers --------------------------------------------------

    def _store_tokens(
        self,
        payload: Mapping[str, Any],
        *,
        previous_refresh: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        tokens = self._tokens()
        existing = tokens.load_or_empty()
        record = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or previous_refresh,
            "expires_at": time.time() + float(payload.get("expires_in", 3600)),
            "user_id": user_id or existing.get("user_id"),
        }
        tokens.save(record)
        return record

    def _token_request(self, form: Mapping[str, str]) -> dict:
        status, payload = http.request_json(
            "POST",
            CIDAAS_BASE + TOKEN_PATH,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            body=urllib.parse.urlencode(form).encode(),
        )
        if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
            raise StoreError(f"token endpoint returned HTTP {status}: {payload}")
        return payload

    def _access_token(self, *, force_refresh: bool = False) -> tuple[str, str]:
        tokens = self._tokens().load()
        if not tokens.get("user_id"):
            raise StoreError("saved login has no user id; run: receipts login --store kaufland")
        if not force_refresh and tokens.get("expires_at", 0) > time.time() + 60:
            return tokens["access_token"], tokens["user_id"]
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise StoreError("access token expired and no refresh token; run: receipts login --store kaufland")
        payload = self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": CIDAAS_CLIENT_ID,
                "v": OAUTH_VERSION,
            }
        )
        record = self._store_tokens(payload, previous_refresh=refresh)
        return record["access_token"], record["user_id"]

    # -- login flow -----------------------------------------------------

    @staticmethod
    def _pkce() -> tuple[str, str]:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return verifier, challenge

    def _authorize(self, challenge: str, state: str) -> tuple[str, str]:
        url = (
            CIDAAS_BASE
            + AUTHZ_PATH
            + "?"
            + urllib.parse.urlencode(
                {
                    "client_id": CIDAAS_CLIENT_ID,
                    "response_type": "code",
                    "redirect_uri": REDIRECT_URI,
                    "ui_locales": _ui_locales(),
                    "v": OAUTH_VERSION,
                    "view_type": "login",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                }
            )
        )
        status, _, headers = http.request("GET", url, headers={"User-Agent": BROWSER_UA})
        location = http.header(headers, "Location") or ""
        if status != 302 or "requestId=" not in location:
            raise StoreError(f"authorize endpoint returned HTTP {status}; expected a login redirect")
        request_id = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["requestId"][0]
        return url, request_id

    @staticmethod
    def _login_page_url(request_id: str) -> str:
        return (
            CIDAAS_BASE
            + LOGIN_PATH
            + "?"
            + urllib.parse.urlencode(
                {
                    "requestId": request_id,
                    "ui_locales": _ui_locales(),
                    "v": OAUTH_VERSION,
                    "view_type": "login",
                    "username_type": "email",
                }
            )
        )

    @staticmethod
    def _post_credentials(page_url: str, form: Mapping[str, str]) -> tuple[dict | None, str | None]:
        status, raw, headers = http.request(
            "POST",
            page_url + "&_data=" + urllib.parse.quote(LOGIN_ROUTE, safe=""),
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": CIDAAS_BASE,
                "Referer": page_url,
            },
            body=urllib.parse.urlencode(form).encode(),
        )
        redirect = http.header(headers, "X-Remix-Redirect") or http.header(headers, "Location")
        if redirect:
            return None, urllib.parse.urljoin(CIDAAS_BASE, redirect)
        payload = http.decode_json(raw)
        if status not in (200, 204) or not isinstance(payload, dict):
            raise StoreError(f"login route returned HTTP {status} with no redirect: {raw[:300]!r}")
        return payload, None

    @staticmethod
    def _follow_to_callback(url: str) -> str | None:
        for _ in range(MAX_REDIRECT_HOPS):
            if url.startswith(CALLBACK_SCHEME):
                return url
            _, _, headers = http.request("GET", url, headers={"User-Agent": BROWSER_UA})
            location = http.header(headers, "Location") or http.header(headers, "X-Remix-Redirect")
            if not location:
                return None
            url = urllib.parse.urljoin(url, location)
        return None

    def _authorization_code(self, authorize_url: str, state: str, post_login_redirect: str | None) -> str:
        callback = self._follow_to_callback(post_login_redirect) if post_login_redirect else None
        if not callback:
            callback = self._follow_to_callback(authorize_url)
        if not callback:
            raise StoreError(
                "login succeeded but no callback redirect was issued; "
                f"last tried: {post_login_redirect or authorize_url}"
            )
        params = urllib.parse.parse_qs(urllib.parse.urlparse(callback).query)
        if "error" in params:
            raise StoreError(f"authorization failed: {params['error'][0]}")
        code = (params.get("code") or [None])[0]
        if not code:
            raise StoreError(f"callback had no code: {callback}")
        returned_state = (params.get("state") or [None])[0]
        if returned_state and returned_state != state:
            raise StoreError("state mismatch; run login again")
        return code

    @staticmethod
    def _userinfo(token: str) -> dict:
        status, info = http.request_json(
            "GET",
            USERINFO_URL,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        )
        if status != 200 or not isinstance(info, dict) or not info.get("sub"):
            raise StoreError(f"userinfo returned HTTP {status}: {info}")
        return info

    def login(self, email: str | None = None, password: str | None = None) -> dict:
        email = email or os.getenv("KAUFLAND_EMAIL") or input("Kaufland email: ").strip()
        password = password or os.getenv("KAUFLAND_PASSWORD") or getpass.getpass("Password: ")

        verifier, challenge = self._pkce()
        state = secrets.token_urlsafe(16)
        authorize_url, request_id = self._authorize(challenge, state)
        page_url = self._login_page_url(request_id)
        http.request("GET", page_url, headers={"User-Agent": BROWSER_UA})

        form = {
            "username": email,
            "password": password,
            "requestId": request_id,
            "usernameType": "email",
            "verificationType": "",
            "exchangeId": "",
            "resetRequestId": "",
            "action": "login",
            "code": "",
        }
        result, redirect = self._post_credentials(page_url, form)
        if result is not None and not result.get("success"):
            detail = result.get("error") or json.dumps(result, ensure_ascii=False)
            raise StoreError(f"login rejected: {detail}")
        result = result or {}

        needs_otp = result.get("otpFields") or result.get("exchangeId") or result.get("verificationType")
        if redirect is None and needs_otp:
            values = result.get("values") or {}
            form.update(
                {
                    "code": input("One-time code: ").strip(),
                    "action": values.get("action", "verify"),
                    "exchangeId": result.get("exchangeId") or values.get("exchangeId", ""),
                    "verificationType": result.get("verificationType") or values.get("verificationType", ""),
                }
            )
            result, redirect = self._post_credentials(page_url, form)
            if result is not None and not result.get("success"):
                raise StoreError(f"verification rejected: {result.get('error') or result}")

        code = self._authorization_code(authorize_url, state, redirect)
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": CIDAAS_CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
                "v": OAUTH_VERSION,
            }
        )
        info = self._userinfo(payload["access_token"])
        self._store_tokens(payload, user_id=str(info["sub"]))
        return info

    def status(self) -> dict:
        tokens = self._tokens().load()
        return {
            "store": self.name,
            "user_id": tokens.get("user_id"),
            **self._base_status(tokens),
        }

    def logout(self) -> Path | None:
        return self._tokens().delete()

    # -- receipts -------------------------------------------------------

    def _page_url(self, user_id: str, start: int, limit: int) -> str:
        query = urllib.parse.urlencode(
            {"start": start, "limit": limit, "country": _country(), "version": 2}
        )
        return f"{API_BASE}/api/v2/customers/{urllib.parse.quote(user_id, safe='')}/transactions?{query}"

    def _fetch_page(self, token: str, user_id: str, start: int, limit: int) -> Any:
        url = self._page_url(user_id, start, limit)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "client-id": LOYALTY_CLIENT_ID,
            "app-platform": "Android",
            "app-version": APP_VERSION,
        }
        status, payload = http.request_json("GET", url, headers=headers)
        if status == 401:
            token, _ = self._access_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            status, payload = http.request_json("GET", url, headers=headers)
        if status != 200:
            raise StoreError(f"transactions endpoint returned HTTP {status}: {payload}")
        return payload

    @staticmethod
    def _extract(payload: Any) -> list[dict]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("transactions", "items", "receipts", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    return KauflandStore._extract(value)
        raise StoreError(f"unexpected response shape: {payload!r:.300}")

    def fetch(self, limit: int = 20, *, fetch_all: bool = False) -> list[dict]:
        token, user_id = self._access_token()
        page_size = MAX_PAGE_SIZE if fetch_all else min(limit, MAX_PAGE_SIZE)
        collected: list[dict] = []
        start = 0
        while True:
            batch = self._extract(self._fetch_page(token, user_id, start, page_size))
            collected.extend(batch)
            if len(batch) < page_size:
                break
            if not fetch_all and len(collected) >= limit:
                break
            start += len(batch)
        return collected if fetch_all else collected[:limit]

    def line_items(self, receipts: list[dict]) -> list[dict]:
        rows = []
        for receipt in receipts:
            store = receipt.get("store") if isinstance(receipt.get("store"), dict) else {}
            for refund, key in ((False, "positions"), (True, "refundPositions")):
                for position in receipt.get(key) or []:
                    if not isinstance(position, dict):
                        continue
                    sign = -1 if refund else 1
                    rows.append(
                        {
                            "receipt_id": receipt.get("id"),
                            "timestamp": receipt.get("timestamp"),
                            "store": store.get("name"),
                            "city": store.get("city"),
                            "name": position.get("name"),
                            "quantity": position.get("quantity", 1),
                            "unit": position.get("quantityUnit"),
                            "unit_price": round(_minor(position.get("unitPrice")) * sign, 2),
                            "total": round(_minor(position.get("total")) * sign, 2),
                            "currency": receipt.get("currency"),
                            "gtin": position.get("gtin"),
                            "refund": refund,
                        }
                    )
        return rows

    def summaries(self, receipts: list[dict]) -> list[dict]:
        rows = []
        for receipt in receipts:
            store = receipt.get("store") if isinstance(receipt.get("store"), dict) else {}
            rows.append(
                {
                    "receipt_id": receipt.get("id"),
                    "timestamp": receipt.get("timestamp"),
                    "store": store.get("name"),
                    "city": store.get("city"),
                    "total": round(_minor(receipt.get("sum")), 2),
                    "saving": round(_minor(receipt.get("saving")), 2),
                    "currency": receipt.get("currency"),
                    "items": len(receipt.get("positions") or []),
                    "bonus_points": receipt.get("bonusPoints"),
                }
            )
        return rows
