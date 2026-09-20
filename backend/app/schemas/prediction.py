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
    # Seccion 18: etiquetas de anomalia calculadas contra las demas
    # predicciones del MISMO partido (ver prediction/anomaly.py). Vacio =
    # sin anomalias detectadas, nunca `None`.
    anomaly_flags: list[str] = []
    # Seccion 1: True cuando esta es la prediccion con mayor `signal_score`
    # (entre las que pasan el filtro duro y no tienen CONTRADICCION) para
    # su partido, en el contexto del endpoint que la devolvio.
    is_best_prediction: bool = False

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


class BestOfDayOut(BaseModel):
    """Respuesta de `/predictions/best-of-day` (seccion 16): `prediction`
    es `None` cuando ninguna candidata cumple TODOS los requisitos minimos
    -- nunca se rebaja el filtro para forzar un resultado (seccion 1 del
    brief: "menos señales bien justificadas" antes que volumen)."""

    prediction: PredictionOut | None
    explanation: list[str]


class HistoryPredictionOut(PredictionOut):
    """Prediccion de un partido YA FINALIZADO, con el resultado real
    (seccion 7/8 del brief). `actual_result`/`is_correct`/`settled_at` son
    `None` mientras el partido no se haya liquidado todavia (settlement
    pipeline, `services/evaluation_service.py`) -- nunca se infieren de
    otra forma."""

    actual_result: str | None = None
    is_correct: bool | None = None
    settled_at: dt.datetime | None = None


class MatchHistoryOut(BaseModel):
    match: MatchOut
    predictions: list[HistoryPredictionOut]
    best_prediction_id: int | None
