"""Non-UI helpers: settings, receipt cache, parsing and aggregation."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kaufland_cli import receipts
from kaufland_cli.config import data_dir

COUNTRIES = {
    "PL": "Polska",
    "DE": "Deutschland",
    "CZ": "Česko",
    "SK": "Slovensko",
    "RO": "România",
    "BG": "България",
    "HR": "Hrvatska",
    "MD": "Moldova",
}


@dataclass
class Settings:
    country: str = "PL"
    theme: str = "system"  # system | light | dark
    limit: int = 50
    fetch_all: bool = False

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
        path.write_text(json.dumps(asdict(self), indent=2))

    def apply(self) -> None:
        os.environ["KAUFLAND_COUNTRY"] = self.country


def cache_path() -> Path:
    return data_dir() / "receipts-cache.json"


def load_cache() -> list[dict]:
    try:
        payload = json.loads(cache_path().read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def save_cache(rows: list[dict]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False))
    os.chmod(path, 0o600)


def clear_cache() -> None:
    cache_path().unlink(missing_ok=True)


def parse_time(value: Any) -> datetime | None:
    if value is None:
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


def fmt_date(value: Any, *, with_time: bool = True) -> str:
    parsed = parse_time(value)
    if not parsed:
        return str(value or "—")
    return parsed.strftime("%d %b %Y, %H:%M" if with_time else "%d %b %Y")


def fmt_money(amount: float, currency: str | None) -> str:
    text = f"{amount:,.2f}".replace(",", " ")
    return f"{text} {currency}" if currency else text


@dataclass
class Overview:
    count: int
    total: float
    saving: float
    average: float
    items: int
    currency: str | None
    months: list[tuple[str, float]]
    stores: list[tuple[str, float, int]]


def overview(raw: list[dict]) -> Overview:
    rows = receipts.summaries(raw)
    total = sum(r["total"] for r in rows)
    saving = sum(r["saving"] for r in rows)
    currency = next((r["currency"] for r in rows if r["currency"]), None)

    by_month: dict[str, float] = defaultdict(float)
    by_store: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for r in rows:
        parsed = parse_time(r["timestamp"])
        if parsed:
            by_month[parsed.strftime("%Y-%m")] += r["total"]
        label = ", ".join(p for p in (r["store"], r["city"]) if p) or "Unknown store"
        by_store[label][0] += r["total"]
        by_store[label][1] += 1

    months = sorted(by_month.items())[-6:]
    month_labels = [(datetime.strptime(k, "%Y-%m").strftime("%b"), v) for k, v in months]
    stores = sorted(((k, v[0], int(v[1])) for k, v in by_store.items()), key=lambda x: -x[1])[:5]
    return Overview(
        count=len(rows),
        total=total,
        saving=saving,
        average=total / len(rows) if rows else 0.0,
        items=sum(r["items"] for r in rows),
        currency=currency,
        months=month_labels,
        stores=stores,
    )


@dataclass
class Product:
    name: str
    count: int
    quantity: float
    spent: float
    last_price: float
    last_seen: Any
    currency: str | None


def products(raw: list[dict]) -> list[Product]:
    grouped: dict[str, Product] = {}
    for item in receipts.line_items(raw):
        name = (item["name"] or "Unnamed item").strip()
        product = grouped.get(name)
        if product is None:
            product = grouped[name] = Product(
                name, 0, 0.0, 0.0, item["unit_price"], item["timestamp"], item["currency"]
            )
        product.count += 1
        with contextlib.suppress(TypeError, ValueError):
            product.quantity += float(item["quantity"] or 0)
        product.spent += item["total"]
        newer = parse_time(item["timestamp"])
        older = parse_time(product.last_seen)
        if newer and (not older or newer >= older):
            product.last_seen = item["timestamp"]
            product.last_price = item["unit_price"]
    return sorted(grouped.values(), key=lambda p: -p.spent)


def export_text(raw: list[dict], kind: str) -> str:
    """kind: receipts-json | items-csv | list-csv"""
    if kind == "receipts-json":
        return json.dumps(raw, ensure_ascii=False, indent=2)
    rows = receipts.line_items(raw) if kind == "items-csv" else receipts.summaries(raw)
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def demo_receipts(n: int = 36) -> list[dict]:
    """Synthetic receipts so the interface can be explored without an account."""
    rng = random.Random(7)
    catalog = [
        ("Mleko 3,2% 1L", 349),
        ("Chleb pszenny 500g", 499),
        ("Masło extra 200g", 799),
        ("Jaja M 10 szt.", 1199),
        ("Banany luz", 599),
        ("Pomidory malinowe", 1299),
        ("Ser gouda plastry", 699),
        ("Kawa ziarnista 1kg", 5499),
        ("Woda mineralna 1,5L", 199),
        ("Jogurt naturalny", 249),
        ("Makaron spaghetti", 449),
        ("Pierś z kurczaka", 2199),
        ("Papier toaletowy 8 rolek", 1599),
        ("Płyn do naczyń", 899),
        ("Czekolada gorzka", 549),
        ("Jabłka Ligol", 399),
        ("Sok pomarańczowy 1L", 699),
        ("Ryż basmati 1kg", 899),
    ]
    stores = [
        ("Kaufland Kraków", "Kraków"),
        ("Kaufland Wieliczka", "Wieliczka"),
        ("Kaufland Nowa Huta", "Kraków"),
    ]
    now = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        when = now - timedelta(days=i * 4 + rng.randint(0, 3), hours=rng.randint(0, 10))
        positions = []
        for name, price in rng.sample(catalog, rng.randint(3, 9)):
            qty = rng.choice([1, 1, 1, 2, 3])
            positions.append(
                {
                    "name": name,
                    "quantity": qty,
                    "quantityUnit": "szt",
                    "unitPrice": price,
                    "total": price * qty,
                    "gtin": f"590{rng.randint(10**9, 10**10 - 1)}",
                }
            )
        total = sum(p["total"] for p in positions)
        name, city = rng.choice(stores)
        out.append(
            {
                "id": f"demo-{i:04d}",
                "timestamp": when.isoformat(),
                "store": {"name": name, "city": city},
                "currency": "PLN",
                "sum": total,
                "saving": int(total * rng.uniform(0, 0.12)),
                "bonusPoints": total // 500,
                "positions": positions,
                "refundPositions": [],
            }
        )
    return out
