#!/usr/bin/env python3
"""Wrapper fino sobre `football-edge update-odds`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.cli import app  # noqa: E402

if __name__ == "__main__":
    app(["update-odds", *sys.argv[1:]])
