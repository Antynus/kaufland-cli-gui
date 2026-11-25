"""Spending categories, assigned per shop name.

Well-known chains get a sensible default; the user can override any shop in the
Stores tab, and the choice is saved in the settings.
"""

from __future__ import annotations

import unicodedata

CATEGORIES = {
    "groceries": ("Groceries", "#16A34A"),
    "drugstore": ("Drugstore & beauty", "#DB2777"),
    "fuel": ("Fuel & car", "#EA580C"),
    "home": ("Home & furniture", "#2563EB"),
    "sport": ("Sport & outdoor", "#0891B2"),
    "electronics": ("Electronics", "#7C3AED"),
    "clothing": ("Clothing", "#C026D3"),
    "food_out": ("Cafés & restaurants", "#CA8A04"),
    "health": ("Pharmacy & health", "#DC2626"),
    "other": ("Other", "#64748B"),
}

# Matched against the normalised shop name: lowercase, no accents.
KNOWN = {
    "kaufland": "groceries",
    "lidl": "groceries",
    "spar": "groceries",
    "interspar": "groceries",
    "konzum": "groceries",
    "studenac": "groceries",
    "plodine": "groceries",
    "tommy": "groceries",
    "ribola": "groceries",
    "eurospin": "groceries",
    "offertissima": "groceries",
    "ktc": "groceries",
    "boso": "groceries",
    "metro": "groceries",
    "billa": "groceries",
    "aldi": "groceries",
    "biedronka": "groceries",
    "zabka": "groceries",
    "auchan": "groceries",
    "carrefour": "groceries",
    "muller": "drugstore",
    "dm": "drugstore",
    "rossmann": "drugstore",
    "bipa": "drugstore",
    "douglas": "drugstore",
    "hebe": "drugstore",
    "shell": "fuel",
    "ina": "fuel",
    "tifon": "fuel",
    "petrol": "fuel",
    "lukoil": "fuel",
    "omv": "fuel",
    "crodux": "fuel",
    "mol": "fuel",
    "orlen": "fuel",
    "bp": "fuel",
    "eni": "fuel",
    "ikea": "home",
    "jysk": "home",
    "pevex": "home",
    "bauhaus": "home",
    "emmezeta": "home",
    "lesnina": "home",
    "obi": "home",
    "hornbach": "home",
    "leroy merlin": "home",
    "harvey norman": "home",
    "decathlon": "sport",
    "intersport": "sport",
    "sport vision": "sport",
    "hervis": "sport",
    "links": "electronics",
    "sancta domenica": "electronics",
    "mediamarkt": "electronics",
    "media markt": "electronics",
    "instar": "electronics",
    "hgspot": "electronics",
    "zara": "clothing",
    "h&m": "clothing",
    "reserved": "clothing",
    "pepco": "clothing",
    "kik": "clothing",
    "c&a": "clothing",
    "primark": "clothing",
    "ljekarna": "health",
    "apoteka": "health",
    "pharmacy": "health",
    "apteka": "health",
    "mcdonald": "food_out",
    "kfc": "food_out",
    "burger king": "food_out",
    "caffe": "food_out",
    "cafe": "food_out",
    "restoran": "food_out",
    "pizzeria": "food_out",
}


def normalise(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def default_category(merchant: str) -> str:
    name = normalise(merchant)
    words = set(name.replace("-", " ").replace(".", " ").split())
    for key, category in sorted(KNOWN.items(), key=lambda kv: -len(kv[0])):
        if (" " in key and key in name) or key in words or name.startswith(key):
            return category
    return "other"


def category_for(merchant: str, overrides: dict[str, str]) -> str:
    return overrides.get(normalise(merchant)) or default_category(merchant)


def label(category: str) -> str:
    return CATEGORIES.get(category, CATEGORIES["other"])[0]


def color(category: str) -> str:
    return CATEGORIES.get(category, CATEGORIES["other"])[1]
