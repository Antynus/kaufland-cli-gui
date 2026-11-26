from __future__ import annotations

import csv
import json
import sys
from typing import Any


def emit(rows: Any, output: str = "json") -> None:
    if output == "json" or not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    fieldnames: list[str] = []
    for row in rows:
        fieldnames.extend(key for key in row if key not in fieldnames)
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def to_float(value: Any, *, scale: float = 1.0) -> float | None:
    """Parse numbers like 12.5, "12,50" or "1250" (with scale=100) into a float."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip().replace(" ", "").replace(",", ".")
    try:
        return round(float(value) / scale, 2)
    except (TypeError, ValueError):
        return None
