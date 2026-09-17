"""Schemas laxos para reportes de backtesting/mercado (estructura variable por diseno)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BacktestSummaryOut(BaseModel):
    market: str
    n_folds: int
    test_seasons: list[str]
    model_quality: dict[str, Any]
    performance_by_bucket: list[dict[str, Any]]
    market_strategy: dict[str, Any] | None
