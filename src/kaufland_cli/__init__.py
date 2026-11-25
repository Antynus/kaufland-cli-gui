from .config import KauflandError, StoreError
from .stores import get_store, names

__all__ = ["StoreError", "KauflandError", "get_store", "names"]
__version__ = "0.2.0"
