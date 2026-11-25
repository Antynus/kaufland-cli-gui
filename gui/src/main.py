import sys
from pathlib import Path

try:
    import kaufland_cli  # noqa: F401  (bundled next to this file in packaged builds)
except ImportError:  # running from a git checkout: use the CLI package from ../../src
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import flet as ft  # noqa: E402
from kaufland_gui import main  # noqa: E402

if __name__ == "__main__":
    ft.run(main)
