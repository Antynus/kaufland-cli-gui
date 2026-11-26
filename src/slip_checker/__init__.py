from .config import SlipError
from .fiscal import FiscalSlip, parse_qr
from .stores import STORES

__all__ = ["STORES", "FiscalSlip", "SlipError", "parse_qr"]
__version__ = "0.2.0"
