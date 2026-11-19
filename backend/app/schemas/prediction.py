from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from backend.app.schemas.match import MatchOut, RoundOut, UtcDatetime


class PredictionFactorOut(BaseModel):
    feature: str
    display_name: str
    contribution: float | None
    direction: str
    raw_value: float | None


class ReliabilityOut(BaseModel):
    """Ver services/evaluation_service.py::TrackRecordEstimate."""

    value: float
    # "competition_market" | "competition_market_shrunk" | "market_tier" | "market_global"
    source_level: str
    n_observations: int


class PredictionOut(BaseModel):
    id: int
    match: MatchOut
    market: str
    line: float | None
    selection: str
    model_probability: float
    # Probabilidad post-calibracion (IsotonicRegression sobre el holdout de
    # entrenamiento, ver services/model_service.py) -- distinta de
    # `model_probability` a proposito (seccion 9 del brief): se exponen
    # AMBAS para poder mostrar cuanto corrigio la calibracion. `None`
    # cuando no hubo holdout suficiente para calibrar ese mercado, nunca
    # inventada.
    calibrated_probability: float | None = None
    market_probability: float | None
    market_odds: float | None
    fair_odds: float
    edge: float | None
    expected_value: float | None
    # EV calculado con `uncertainty_low` (extremo conservador del
    # intervalo, ver uncertainty_low/high mas abajo) en vez de
    # `model_probability` -- pedido real del usuario ("revision motor
    # 2.0"): nunca tratar la probabilidad puntual como cifra fiable al
    # 100% para estimar rentabilidad esperada. `None` solo cuando no hay
    # cuota de mercado (igual que `expected_value`).
    risk_adjusted_expected_value: float | None = None
    # "Mejores oportunidades" (cambio de filosofia del ranking, pedido
    # real del usuario): EV_estimado con la probabilidad CALIBRADA
    # (`ev_calibrated`) y EV_conservador con la probabilidad conservadora
    # (`ev_conservative`, min(calibrada, uncertainty_low)) -- ver
    # api/routes/predictions.py::serialize_prediction y
    # prediction/ranking.py. Distintos de `expected_value`/
    # `risk_adjusted_expected_value` (que usan `model_probability` CRUDA,
    # formula de v1/v2): se calculan solo para mostrar, nunca se guardan
    # en `Prediction` -- esta implementacion no toca los modelos
    # predictivos ni sus probabilidades.
    ev_calibrated: float | None = None
    ev_conservative: float | None = None
    confidence: float
    data_quality: float
    uncertainty_low: float | None = None
    uncertainty_high: float | None = None
    signal_tier: str
    explanation: list[PredictionFactorOut]
    model_version_id: int
    created_at: UtcDatetime
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
    # Fiabilidad historica JERARQUICA (mision "oportunidades" seccion 2.2,
    # ver services/evaluation_service.py::market_track_records_with_provenance):
    # `None` sin historico suficiente en NINGUN nivel. Cuando existe,
    # `source_level` dice de donde sale -- "competition_market" (especifica
    # de esta competicion y mercado), "competition_market_shrunk" (mezcla
    # de la exacta, con poca muestra, y un respaldo fiable -- shrinkage
    # bayesiano, ver TRACK_RECORD_SHRINKAGE_K), "market_tier" (heredada de
    # otras competiciones del mismo tier, CORE/EXPERIMENTAL) o
    # "market_global" (de todas las competiciones). NUNCA mostrar esto
    # como si fuera siempre especifico de la competicion exacta -- pedido
    # real del
    # usuario ("nunca presentar una fiabilidad global como si fuera
    # especifica").
    reliability: ReliabilityOut | None = None

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
    # `None` mientras no haya PredictionResult (pendiente) O cuando la
    # liquidacion es "void" (partido cancelado) -- en ambos casos no hay
    # un acierto/fallo real que mostrar, ver api/routes/history.py.
    is_correct: bool | None = None
    settled_at: UtcDatetime | None = None
    # Closing Line Value (ver market/clv.py): `None` hasta que haya
    # historico de cuotas suficiente (MatchOddsHistory, solo desde que se
    # anhadio esa tabla -- nunca retroactivo).
    closing_line_value: float | None = None
    # "won"|"lost"|"void"|"half_won"|"half_lost", o `None` para
    # predicciones pendientes O liquidadas ANTES de que existiera esta
    # columna (mismo patron aditivo que Competition.tier) -- el frontend
    # cae a `is_correct` en ese caso. Ver db/models/modeling.py.
    settlement_status: str | None = None


class MatchHistoryOut(BaseModel):
    match: MatchOut
    predictions: list[HistoryPredictionOut]
    best_prediction_id: int | None


class SummaryOut(BaseModel):
    """"Resumen de jornada" del dashboard (seccion de rediseño)."""

    date: dt.date
    matches_pending: int
    competitions_count: int
    opportunities_count: int
    high_quality_count: int
