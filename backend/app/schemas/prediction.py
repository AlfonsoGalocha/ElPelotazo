from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from backend.app.schemas.match import MatchOut, RoundOut


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
    has_market: bool = False
    market_probability_source: str | None = None
    bookmakers_count: int | None = None
    bookmakers_used: int | None = None
    market_odds_min: float | None = None
    market_odds_max: float | None = None
    market_odds_median: float | None = None
    market_odds_average: float | None = None

    model_config = {"from_attributes": True}


class ExcludedSignalOut(BaseModel):
    prediction_id: int
    match: MatchOut
    market: str
    reason: str
    model_probability: float
    market_probability: float | None
    market_odds: float | None
    edge: float | None
    bookmakers_used: int | None


class CurrentRoundPredictionsOut(BaseModel):
    round: RoundOut | None
    predictions: list[PredictionOut]
