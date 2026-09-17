#!/usr/bin/env python3
"""Wrapper fino sobre `football-edge update` (ver seccion 69 del brief:
`python scripts/update_data.py` debe funcionar sin instalar el paquete)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.cli import app  # noqa: E402

if __name__ == "__main__":
    sys.argv[0] = "update_data.py"
    app(["update", *sys.argv[1:]])
