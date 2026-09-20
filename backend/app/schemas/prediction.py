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
    market_quality: str | None = None  # "HIGH" | "MEDIUM" | "LOW" (ver prediction/market_quality.py)
    odds_age_minutes: float | None = None
    is_stale_odds: bool = False

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


class BookmakerOddsOut(BaseModel):
    bookmaker: str
    price: float
    snapshot_type: str
    diff_from_consensus: float | None  # price - market_odds_median (positivo = mejor que el consenso)
    is_outlier: bool  # descartada del consenso por market/consensus.py (MAD), ver Prediction.market_probability_source


class SignalDetailOut(PredictionOut):
    """Extiende `PredictionOut` con el desglose bookmaker-por-bookmaker y
    una explicacion en lenguaje llano de por que esta senhal aparece
    (seccion 16/17 del pedido de revision integral) -- nunca lenguaje de
    certeza ("apuesta segura", "ganadora", "100%"), solo los numeros."""

    bookmaker_odds: list[BookmakerOddsOut]
    explanation_summary: list[str]
