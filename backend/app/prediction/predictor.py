"""Orquesta: features -> modelo(s) -> probabilidad -> mercado -> edge -> confianza.

Funcion pura (no toca la BD) para poder testear sin infra y para que
entrenamiento/backtesting/prediccion en produccion compartan exactamente la
misma logica.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.app.models.base import GoalsModel
from backend.app.models.goals.ml_classifier import MarketClassifierModel
from backend.app.prediction.confidence import ConfidenceInputs, confidence_score
from backend.app.prediction.data_quality import compute_data_quality
from backend.app.prediction.edge import compute_edge, compute_expected_value
from backend.app.prediction.explanation import explain_logistic_pipeline, explain_statistical_inputs
from backend.app.prediction.fair_odds import fair_odds
from backend.app.prediction.market_labels import MARKET_DEFINITIONS
from backend.app.prediction.probability import market_probabilities
from backend.app.prediction.secondary_markets import (
    SECONDARY_MARKET_DEFINITIONS,
    TotalCountPoissonModel,
)

KEY_FEATURES_FOR_QUALITY = [
    "home_goals_for_avg_last5", "away_goals_for_avg_last5",
    "home_xg_for_avg_last5", "away_xg_for_avg_last5",
    "home_attack_strength", "away_attack_strength",
]


@dataclass
class MarketPredictionOutput:
    match_id: int
    market: str
    model_probability: float
    fair_odds: float
    market_probability: float | None
    market_odds: float | None
    vig_removed: bool | None
    edge: float | None
    expected_value: float | None
    confidence: float
    data_quality: float
    explanation: list[dict]


def predict_markets_for_table(
    table: pd.DataFrame,
    statistical_model: GoalsModel,
    ml_model: MarketClassifierModel | None = None,
    ensemble_weights: dict[str, float] | None = None,
    calibration_errors: dict[str, float] | None = None,
    market_quotes: dict[tuple[int, str], dict] | None = None,
) -> list[MarketPredictionOutput]:
    """`market_quotes`: opcional, {(match_id, market_key): {"market_probability", "market_odds", "vig_removed"}}."""
    stat_probs = market_probabilities(statistical_model, table)
    ml_probs = ml_model.predict_market_probabilities(table) if ml_model is not None else {}

    outputs: list[MarketPredictionOutput] = []
    for market_key in MARKET_DEFINITIONS:
        p_stat = stat_probs[market_key]
        p_ml = ml_probs.get(market_key)

        if p_ml is not None and ensemble_weights and market_key in ensemble_weights:
            w = ensemble_weights[market_key]
            p_model = w * p_stat + (1 - w) * p_ml
            model_agreement = 1.0 - np.abs(p_stat - p_ml)
        else:
            p_model = p_stat
            model_agreement = np.full(len(table), np.nan)

        calibration_error = (calibration_errors or {}).get(market_key)

        for i, (_, row) in enumerate(table.iterrows()):
            quote = (market_quotes or {}).get((row["match_id"], market_key))
            market_probability = quote["market_probability"] if quote else None
            market_odds = quote["market_odds"] if quote else None
            vig_removed = quote.get("vig_removed") if quote else None

            data_quality = compute_data_quality(row, KEY_FEATURES_FOR_QUALITY)
            agreement = model_agreement[i] if not np.isnan(model_agreement[i]) else None
            n_prior_avg = np.nanmean(
                [row.get("home_goals_for_n_prior", np.nan), row.get("away_goals_for_n_prior", np.nan)]
            )
            sample_size_score = float(np.clip((n_prior_avg or 0) / 10.0, 0.0, 1.0))

            confidence = confidence_score(
                ConfidenceInputs(
                    calibration_error=calibration_error,
                    sample_size_score=sample_size_score,
                    model_agreement=agreement,
                    data_quality=data_quality,
                )
            )

            if ml_model is not None and market_key in ml_model.pipelines_:
                explanation = explain_logistic_pipeline(
                    ml_model.pipelines_[market_key], ml_model.feature_cols_, row
                )
            else:
                explanation = explain_statistical_inputs(row)

            outputs.append(
                MarketPredictionOutput(
                    match_id=int(row["match_id"]),
                    market=market_key,
                    model_probability=float(p_model[i]),
                    fair_odds=float(fair_odds(p_model[i])),
                    market_probability=market_probability,
                    market_odds=market_odds,
                    vig_removed=vig_removed,
                    edge=compute_edge(float(p_model[i]), market_probability),
                    expected_value=compute_expected_value(float(p_model[i]), market_odds),
                    confidence=confidence,
                    data_quality=data_quality,
                    explanation=explanation,
                )
            )
    return outputs


def predict_secondary_markets_for_table(
    table: pd.DataFrame,
    cards_model: TotalCountPoissonModel,
    corners_model: TotalCountPoissonModel,
) -> list[MarketPredictionOutput]:
    """Mercados de tarjetas/corners (seccion 18/19). Sin mercado/edge: no hay
    cuotas reales de estos mercados en las fuentes de datos usadas (ver
    docs/data_sources.md), asi que `market_probability`/`edge` quedan `None`
    explicitamente en vez de inventar un valor.
    """
    outputs: list[MarketPredictionOutput] = []
    for model in (cards_model, corners_model):
        probs_by_market = model.predict_market_probabilities(table)
        for market_key, probs in probs_by_market.items():
            spec = SECONDARY_MARKET_DEFINITIONS[market_key]
            for i, (_, row) in enumerate(table.iterrows()):
                data_quality = compute_data_quality(row, KEY_FEATURES_FOR_QUALITY)
                n_prior_avg = np.nanmean(
                    [row.get("home_goals_for_n_prior", np.nan), row.get("away_goals_for_n_prior", np.nan)]
                )
                sample_size_score = float(np.clip((n_prior_avg or 0) / 10.0, 0.0, 1.0))
                confidence = confidence_score(
                    ConfidenceInputs(
                        calibration_error=None,
                        sample_size_score=sample_size_score,
                        model_agreement=None,
                        data_quality=data_quality,
                    )
                )
                explanation = explain_logistic_pipeline(model.pipeline_, model.feature_cols_, row)
                outputs.append(
                    MarketPredictionOutput(
                        match_id=int(row["match_id"]),
                        market=market_key,
                        model_probability=float(probs[i]),
                        fair_odds=float(fair_odds(probs[i])),
                        market_probability=None,
                        market_odds=None,
                        vig_removed=None,
                        edge=None,
                        expected_value=None,
                        confidence=confidence,
                        data_quality=data_quality,
                        explanation=explanation,
                    )
                )
    return outputs
