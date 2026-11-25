"""Store registry: the built-in stores plus plug-ins from the plugins folder.

A plug-in is a single ``.py`` file in ``<data folder>/plugins``. It must define
``STORE``, an instance of a ``Store`` subclass (see base.py and gui/PLUGINS.md)::

    from kaufland_gui.stores.base import Store

    class MyShop(Store):
        id = "myshop"
        name = "My Shop"
        auth = "token"
        ...

    STORE = MyShop()
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from kaufland_cli.config import data_dir

from .base import Store, StoreError
from .kaufland import KauflandStore
from .lidl import LidlStore
from .scanned import ScannedStore

__all__ = ["Registry", "Store", "StoreError", "plugins_dir"]


def plugins_dir() -> Path:
    return data_dir() / "plugins"


@dataclass
class PluginProblem:
    path: Path
    error: str


class Registry:
    def __init__(self) -> None:
        self.stores: dict[str, Store] = {}
        self.problems: list[PluginProblem] = []
        self.reload()

    def reload(self) -> None:
        self.stores = {s.id: s for s in (KauflandStore(), LidlStore(), ScannedStore())}
        self.problems = []
        folder = plugins_dir()
        for path in sorted(folder.glob("*.py")) if folder.exists() else []:
            try:
                store = _load_plugin(path)
            except Exception as exc:  # noqa: BLE001 - a broken plug-in must not take the app down
                self.problems.append(PluginProblem(path, f"{type(exc).__name__}: {exc}"))
                continue
            if store.id in self.stores:
                self.problems.append(PluginProblem(path, f"store id '{store.id}' is already used"))
                continue
            self.stores[store.id] = store

    def get(self, store_id: str) -> Store | None:
        return self.stores.get(store_id)

    @property
    def scanned(self) -> ScannedStore:
        return self.stores["scanned"]  # type: ignore[return-value]

    def install_plugin(self, filename: str, source: bytes) -> Path:
        if not filename.endswith(".py"):
            raise StoreError("A plug-in is a single .py file.")
        folder = plugins_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / Path(filename).name
        target.write_bytes(source)
        try:
            _load_plugin(target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise StoreError(f"The plug-in failed to load: {type(exc).__name__}: {exc}") from exc
        self.reload()
        return target

    def remove_plugin(self, store: Store) -> None:
        if store.plugin_path:
            store.plugin_path.unlink(missing_ok=True)
            shutil.rmtree(store.state_path("x").parent, ignore_errors=True)
        self.reload()


def _load_plugin(path: Path) -> Store:
    name = f"receipt_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("not a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    store = getattr(module, "STORE", None)
    if not isinstance(store, Store):
        raise TypeError("the file must define STORE = YourStore()")
    if not store.id or not store.name:
        raise ValueError("the store needs an id and a name")
    if store.auth not in ("password", "browser", "token", "none"):
        raise ValueError(f"unknown auth kind '{store.auth}'")
    store.plugin_path = path
    return store


# ---------- cache of fetched receipts, one file per store ----------


def cache_path(store_id: str) -> Path:
    return data_dir() / "cache" / f"{store_id}.json"


def load_cache(store_id: str) -> list[dict]:
    try:
        rows = json.loads(cache_path(store_id).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return rows if isinstance(rows, list) else []


def save_cache(store_id: str, rows: list[dict]) -> None:
    path = cache_path(store_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False))


def clear_cache(store_id: str) -> None:
    cache_path(store_id).unlink(missing_ok=True)
