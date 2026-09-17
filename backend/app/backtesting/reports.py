"""Serializa resultados de backtesting a JSON reproducible (scripts/run_backtest.py)."""

from __future__ import annotations

import json
from pathlib import Path


def write_backtest_report(summary: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return output_path
