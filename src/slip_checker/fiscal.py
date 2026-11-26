"""Croatian fiscal receipts (fiskalni račun).

Every fiscalised receipt in Croatia carries a QR code linking to the Tax Administration
checker, for example::

    https://porezna.gov.hr/rn?jir=<uuid>&datv=20250905_1230&izn=12,34

``jir`` (36-char UUID) or ``zki`` (32 hex chars) identifies the receipt, ``datv`` is the
issue time as ``YYYYMMDD_HHMM`` and ``izn`` is the total, either as a decimal with a comma
or dot, or as whole cents with no separator. This parsing mirrors the official checker page.

The checker itself requires a reCAPTCHA per lookup, so this module does not query it.
It parses slips, stores them locally and builds the link to open in a browser.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .config import SlipError

VERIFY_URL = "https://porezna.gov.hr/rn"
EURO_SINCE = datetime(2023, 1, 1)

_JIR = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ZKI = re.compile(r"^[0-9a-fA-F]{32}$")
_NUMBER = re.compile(r"-?\d+([.,]\d*)?")


@dataclass(frozen=True)
class FiscalSlip:
    issued_at: datetime
    amount: Decimal
    jir: str | None = None
    zki: str | None = None

    @property
    def code(self) -> str:
        return (self.jir or self.zki or "").lower()

    @property
    def currency(self) -> str:
        return "EUR" if self.issued_at >= EURO_SINCE else "HRK"

    @property
    def verify_url(self) -> str:
        params = {"jir": self.jir} if self.jir else {"zki": self.zki}
        params["datv"] = self.issued_at.strftime("%Y%m%d_%H%M")
        params["izn"] = f"{self.amount:.2f}".replace(".", ",")
        return VERIFY_URL + "?" + urllib.parse.urlencode(params, safe=",")


def parse_amount(raw: str) -> Decimal:
    text = urllib.parse.unquote(raw or "").strip()
    match = _NUMBER.search(text)
    if not match:
        raise SlipError(f"invalid amount: {raw!r}")
    number = match.group(0)
    try:
        if "," in number or "." in number:
            whole, _, fraction = number.replace(",", ".").partition(".")
            if len(fraction) > 2:
                raise SlipError(f"amount has more than two decimals: {raw!r}")
            return Decimal(f"{whole}.{fraction or '0'}").quantize(Decimal("0.01"))
        return (Decimal(number) / 100).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise SlipError(f"invalid amount: {raw!r}") from exc


def parse_datetime(raw: str) -> datetime:
    try:
        return datetime.strptime(raw.strip(), "%Y%m%d_%H%M")
    except ValueError as exc:
        raise SlipError(f"invalid datv {raw!r}; expected YYYYMMDD_HHMM") from exc


def _check_code(jir: str | None, zki: str | None) -> tuple[str | None, str | None]:
    jir = (jir or "").strip() or None
    zki = (zki or "").strip() or None
    if jir and not _JIR.match(jir):
        raise SlipError(f"invalid JIR {jir!r}; expected 8-4-4-4-12 hex digits")
    if zki and not _ZKI.match(zki):
        raise SlipError(f"invalid ZKI {zki!r}; expected 32 hex digits")
    if not jir and not zki:
        raise SlipError("a fiscal slip needs a JIR or a ZKI")
    return (jir.lower() if jir else None), (zki.lower() if zki else None)


def parse_qr(text: str) -> FiscalSlip:
    """Parse the text of a fiscal QR code (the porezna.gov.hr link or just its query)."""
    text = text.strip()
    query = urllib.parse.urlparse(text).query if "://" in text else text.lstrip("?")
    params = {k.lower(): v[0] for k, v in urllib.parse.parse_qs(query, keep_blank_values=True).items()}
    if "datv" not in params or "izn" not in params:
        raise SlipError("QR text must contain jir or zki, datv and izn")
    jir, zki = _check_code(params.get("jir"), params.get("zki"))
    return FiscalSlip(
        issued_at=parse_datetime(params["datv"]),
        amount=parse_amount(params["izn"]),
        jir=jir,
        zki=zki,
    )


def from_fields(*, jir: str | None, zki: str | None, date: str, time: str, amount: str) -> FiscalSlip:
    """Build a slip from values typed off a paper receipt."""
    jir, zki = _check_code(jir, zki)
    try:
        issued_at = datetime.strptime(f"{date.strip()} {time.strip()}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise SlipError("use --date YYYY-MM-DD and --time HH:MM") from exc
    text = amount.strip()
    if not re.search(r"[.,]", text):
        text += ".00"  # typed amounts are in euros, not cents
    return FiscalSlip(issued_at=issued_at, amount=parse_amount(text), jir=jir, zki=zki)
