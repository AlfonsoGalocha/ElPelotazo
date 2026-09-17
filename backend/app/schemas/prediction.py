from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from backend.app.schemas.match import MatchOut


class PredictionFactorOut(BaseModel):
    feature: str
    display_name: str
    contribution: float | None
    direction: str
    raw_value: float | None


class PredictionOut(BaseModel):
    id: int
    match: MatchOut
    market: str
    line: float | None
    selection: str
    model_probability: float
    market_probability: float | None
    market_odds: float | None
    fair_odds: float
    edge: float | None
    expected_value: float | None
    confidence: float
    data_quality: float
    signal_tier: str
    explanation: list[PredictionFactorOut]
    model_version_id: int
    created_at: dt.datetime

    model_config = {"from_attributes": True}
