"""Read Croatian fiscal receipt QR codes.

Every fiscalised receipt in Croatia prints a QR code that links to the Tax
Administration's check page, for example::

    https://porezna.gov.hr/rn?jir=0a1b2c3d-...-...&datv=20261120_1703&izn=2345

``jir`` (or ``zki`` when the receipt was issued offline) identifies the receipt,
``datv`` is the issue date and time, and ``izn`` is the total. Without a decimal
separator ``izn`` is in cents (2345 = 23,45 EUR), which is how the Tax
Administration's own page reads it. The QR holds no shop name or items.
"""

from __future__ import annotations

import io
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime

JIR = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ZKI = re.compile(r"^[0-9a-fA-F]{32}$")
CHECK_URL = "https://porezna.gov.hr/rn"


class QrError(ValueError):
    pass


@dataclass
class FiscalReceipt:
    jir: str | None
    zki: str | None
    issued: datetime
    total: float
    url: str

    @property
    def key(self) -> str:
        return (self.jir or self.zki or "").lower()


def parse(text: str) -> FiscalReceipt:
    """Parse the text of a fiscal QR code (the check URL, or just its query string)."""
    text = (text or "").strip()
    if not text:
        raise QrError("The QR code is empty.")
    query = urllib.parse.urlparse(text).query if "://" in text else text.lstrip("?")
    params = {k.lower(): v[0].strip() for k, v in urllib.parse.parse_qs(query).items() if v}
    jir, zki = params.get("jir") or None, params.get("zki") or None
    if not jir and not zki:
        raise QrError("This is not a Croatian fiscal receipt QR code (no JIR or ZKI).")
    if jir and not JIR.match(jir):
        raise QrError("The receipt's JIR looks damaged. Try scanning again.")
    if zki and not ZKI.match(zki):
        raise QrError("The receipt's ZKI looks damaged. Try scanning again.")
    try:
        issued = datetime.strptime(params.get("datv", ""), "%Y%m%d_%H%M")
    except ValueError as exc:
        raise QrError("The QR code has no valid date (datv).") from exc
    total = _amount(params.get("izn", ""))
    url = f"{CHECK_URL}?" + urllib.parse.urlencode(
        {
            k: v
            for k, v in (("jir", jir), ("zki", zki), ("datv", params["datv"]), ("izn", params.get("izn", "")))
            if v
        }
    )
    return FiscalReceipt(jir=jir, zki=zki, issued=issued, total=total, url=url)


def _amount(raw: str) -> float:
    raw = urllib.parse.unquote(raw).strip()
    match = re.search(r"-?\d+([.,]\d*)?", raw)
    if not match:
        raise QrError("The QR code has no total (izn).")
    value = match.group(0)
    if "," in value or "." in value:
        return round(float(value.replace(",", ".")), 2)
    return round(int(value) / 100, 2)


def decode_image(data: bytes) -> list[str]:
    """Return the text of every QR/barcode found in an image (PNG/JPEG bytes)."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise QrError("Image support (Pillow) is missing from this build.") from exc
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception as exc:  # noqa: BLE001 - any decoder failure means "not an image we can read"
        raise QrError("That file is not an image this app can read.") from exc
    image = image.convert("RGB")
    if max(image.size) > 2000:  # phone photos: smaller is faster and decodes as well
        image.thumbnail((2000, 2000))

    errors = []
    for decoder in (_zxing, _pyzbar, _opencv):
        try:
            found = decoder(image)
        except ImportError:
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            continue
        if found:
            return found
        # Try once more in high contrast greyscale; helps with thermal paper photos.
        try:
            found = decoder(ImageOps.autocontrast(ImageOps.grayscale(image)).convert("RGB"))
        except Exception:  # noqa: BLE001
            found = []
        if found:
            return found
        return []
    if errors:
        raise QrError(f"Could not read the image: {errors[0]}")
    raise QrError("No QR decoder is available in this build. Paste the QR link instead.")


def _zxing(image) -> list[str]:
    import zxingcpp

    return [r.text for r in zxingcpp.read_barcodes(image) if r.text]


def _pyzbar(image) -> list[str]:
    from pyzbar import pyzbar

    return [r.data.decode("utf-8", "replace") for r in pyzbar.decode(image) if r.data]


def _opencv(image) -> list[str]:
    import cv2
    import numpy

    ok, texts, _, _ = cv2.QRCodeDetector().detectAndDecodeMulti(numpy.array(image)[:, :, ::-1])
    return [t for t in texts if t] if ok else []
