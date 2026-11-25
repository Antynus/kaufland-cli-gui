"""Non-UI helpers: settings, aggregation over normalized receipts, export and demo data."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kaufland_cli.config import data_dir

from . import categories

COUNTRIES = {
    "HR": "Hrvatska",
    "PL": "Polska",
    "DE": "Deutschland",
    "AT": "Österreich",
    "SI": "Slovenija",
    "CZ": "Česko",
    "SK": "Slovensko",
    "RO": "România",
    "BG": "България",
    "MD": "Moldova",
}


@dataclass
class Settings:
    country: str = "HR"
    theme: str = "system"  # system | light | dark
    limit: int = 50
    categories: dict[str, str] = field(default_factory=dict)  # normalised merchant -> category id

    @staticmethod
    def path() -> Path:
        return data_dir() / "gui-settings.json"

    @classmethod
    def load(cls) -> Settings:
        try:
            raw = json.loads(cls.path().read_text())
        except (OSError, json.JSONDecodeError):
            raw = {}
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        settings = cls(**known)
        settings.apply()
        return settings

    def save(self) -> None:
        self.apply()
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    def apply(self) -> None:
        os.environ["KAUFLAND_COUNTRY"] = self.country

    def category(self, merchant: str) -> str:
        return categories.category_for(merchant, self.categories)


def parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone() if parsed.tzinfo else parsed
    return None


def sort_key(receipt: dict) -> float:
    parsed = parse_time(receipt.get("timestamp"))
    if not parsed:
        return 0.0
    return parsed.timestamp() if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc).timestamp()


def fmt_date(value: Any, *, with_time: bool = True) -> str:
    parsed = parse_time(value)
    if not parsed:
        return str(value or "—")
    return parsed.strftime("%d %b %Y, %H:%M" if with_time else "%d %b %Y")


def fmt_money(amount: float, currency: str | None) -> str:
    text = f"{amount:,.2f}".replace(",", " ")
    symbol = {"EUR": "€", "PLN": "zł"}.get(currency or "", currency or "")
    return f"{text} {symbol}".strip()


def fmt_qty(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "")
    return str(int(number)) if number == int(number) else f"{number:g}"


def main_currency(rows: list[dict]) -> str | None:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("currency"):
            counts[r["currency"]] += 1
    return max(counts, key=counts.get) if counts else None


@dataclass
class Overview:
    count: int
    total: float
    saving: float
    average: float
    items: int
    currency: str | None
    months: list[tuple[str, float]]
    merchants: list[tuple[str, float, int]]
    categories: list[tuple[str, float]]


def overview(rows: list[dict], settings: Settings) -> Overview:
    total = sum(r.get("total") or 0 for r in rows)
    saving = sum(r.get("saving") or 0 for r in rows)
    by_month: dict[str, float] = defaultdict(float)
    by_merchant: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    by_category: dict[str, float] = defaultdict(float)
    for r in rows:
        parsed = parse_time(r.get("timestamp"))
        if parsed:
            by_month[parsed.strftime("%Y-%m")] += r.get("total") or 0
        merchant = r.get("merchant") or "Unknown shop"
        by_merchant[merchant][0] += r.get("total") or 0
        by_merchant[merchant][1] += 1
        by_category[settings.category(merchant)] += r.get("total") or 0

    months = sorted(by_month.items())[-6:]
    return Overview(
        count=len(rows),
        total=total,
        saving=saving,
        average=total / len(rows) if rows else 0.0,
        items=sum(len(r.get("items") or []) for r in rows),
        currency=main_currency(rows),
        months=[(datetime.strptime(k, "%Y-%m").strftime("%b"), v) for k, v in months],
        merchants=sorted(((k, v[0], int(v[1])) for k, v in by_merchant.items()), key=lambda x: -x[1]),
        categories=sorted(by_category.items(), key=lambda x: -x[1]),
    )


@dataclass
class Product:
    name: str
    merchants: set[str]
    count: int
    quantity: float
    spent: float
    last_price: float
    last_seen: Any
    currency: str | None


def products(rows: list[dict]) -> list[Product]:
    grouped: dict[str, Product] = {}
    for r in rows:
        for item in r.get("items") or []:
            name = (item.get("name") or "Unnamed item").strip()
            key = name.lower()
            p = grouped.get(key)
            if p is None:
                p = grouped[key] = Product(
                    name,
                    set(),
                    0,
                    0.0,
                    0.0,
                    item.get("unit_price") or 0,
                    r.get("timestamp"),
                    r.get("currency"),
                )
            p.merchants.add(r.get("merchant") or "")
            p.count += 1
            with contextlib.suppress(TypeError, ValueError):
                p.quantity += float(item.get("quantity") or 0)
            p.spent += item.get("total") or 0
            newer, older = parse_time(r.get("timestamp")), parse_time(p.last_seen)
            if newer and (not older or newer >= older):
                p.last_seen = r.get("timestamp")
                p.last_price = item.get("unit_price") or 0
    return sorted(grouped.values(), key=lambda p: -p.spent)


EXPORTS = {
    "items-csv": ("receipt-items", "csv"),
    "list-csv": ("receipts", "csv"),
    "receipts-json": ("receipts", "json"),
}


def export_text(rows: list[dict], kind: str, settings: Settings) -> str:
    if kind == "receipts-json":
        return json.dumps(rows, ensure_ascii=False, indent=2)
    if kind == "items-csv":
        table = [
            {
                "receipt": r.get("uid"),
                "timestamp": r.get("timestamp"),
                "shop": r.get("merchant"),
                "branch": r.get("branch"),
                "name": i.get("name"),
                "quantity": i.get("quantity"),
                "unit": i.get("unit"),
                "unit_price": i.get("unit_price"),
                "total": i.get("total"),
                "currency": r.get("currency"),
                "refund": i.get("refund", False),
            }
            for r in rows
            for i in r.get("items") or []
        ]
    else:
        table = [
            {
                "receipt": r.get("uid"),
                "timestamp": r.get("timestamp"),
                "shop": r.get("merchant"),
                "branch": r.get("branch"),
                "city": r.get("city"),
                "category": categories.label(settings.category(r.get("merchant") or "")),
                "total": r.get("total"),
                "saving": r.get("saving"),
                "currency": r.get("currency"),
                "items": len(r.get("items") or []),
                "source": r.get("source"),
                "note": r.get("note", ""),
            }
            for r in rows
        ]
    if not table:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows(table)
    return buffer.getvalue()


def demo_receipts() -> list[dict]:
    """Synthetic receipts from several shops, for exploring the app without accounts."""
    rng = random.Random(7)
    catalog = {
        "Kaufland": (
            "kaufland",
            [
                "Mlijeko 2,8% 1L",
                "Kruh polubijeli",
                "Maslac 250g",
                "Jaja M 10 kom",
                "Banane",
                "Rajčica",
                "Gauda narezana",
                "Kava 500g",
                "Jogurt",
                "Tjestenina",
            ],
        ),
        "Lidl": (
            "lidl",
            [
                "Voda 1,5L",
                "Pileća prsa",
                "Čokolada",
                "Jabuke",
                "Sok naranča 1L",
                "Riža 1kg",
                "Deterdžent za suđe",
                "Toaletni papir 10/1",
            ],
        ),
        "Konzum": ("scanned", []),
        "Studenac": ("scanned", []),
        "Shell": ("scanned", []),
        "Müller": ("scanned", []),
        "IKEA": ("scanned", []),
        "Decathlon": ("scanned", []),
    }
    weights = {
        "Kaufland": 9,
        "Lidl": 8,
        "Konzum": 5,
        "Studenac": 6,
        "Shell": 3,
        "Müller": 2,
        "IKEA": 1,
        "Decathlon": 1,
    }
    cities = ["Zagreb", "Velika Gorica", "Samobor"]
    now = datetime.now(timezone.utc)
    out = []
    for i in range(60):
        merchant = rng.choices(list(weights), list(weights.values()))[0]
        source, goods = catalog[merchant]
        when = now - timedelta(days=i * 3 + rng.randint(0, 2), hours=rng.randint(0, 10))
        items = []
        for name in rng.sample(goods, min(len(goods), rng.randint(3, 8))) if goods else []:
            price = round(rng.uniform(0.6, 9.5), 2)
            qty = rng.choice([1, 1, 1, 2, 3])
            items.append(
                {
                    "name": name,
                    "quantity": qty,
                    "unit": "kom",
                    "unit_price": price,
                    "total": round(price * qty, 2),
                    "refund": False,
                }
            )
        total = (
            round(sum(x["total"] for x in items), 2)
            if items
            else round(
                {
                    "Shell": rng.uniform(35, 75),
                    "IKEA": rng.uniform(40, 260),
                    "Decathlon": rng.uniform(20, 120),
                }.get(merchant, rng.uniform(4, 45)),
                2,
            )
        )
        city = rng.choice(cities)
        out.append(
            {
                "uid": f"demo:{i}",
                "source": source,
                "id": f"demo{i}",
                "merchant": merchant,
                "branch": f"{merchant} {city}",
                "city": city,
                "timestamp": when.isoformat(),
                "currency": "EUR",
                "total": total,
                "saving": round(total * rng.uniform(0, 0.1), 2) if items else 0.0,
                "items": items,
                "demo": True,
            }
        )
    return out
