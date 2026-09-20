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
from backend.app.prediction.market_labels import (
    MARKET_DEFINITIONS,
    renormalize_mutually_exclusive_groups,
)
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
    market_probability_source: str | None = None
    bookmakers_count: int | None = None
    bookmakers_used: int | None = None
    market_odds_min: float | None = None
    market_odds_max: float | None = None
    market_odds_median: float | None = None
    market_odds_average: float | None = None
    calibrated_probability: float | None = None


def predict_markets_for_table(
    table: pd.DataFrame,
    statistical_model: GoalsModel,
    ml_model: MarketClassifierModel | None = None,
    ensemble_weights: dict[str, float] | None = None,
    calibration_errors: dict[str, float] | None = None,
    market_quotes: dict[tuple[int, str], dict] | None = None,
    calibrators: dict[str, object] | None = None,
) -> list[MarketPredictionOutput]:
    """`market_quotes`: opcional, {(match_id, market_key): {"market_probability", "market_odds", "vig_removed"}}.

    `calibrators`: opcional, {market_key: objeto con `.transform(np.ndarray) -> np.ndarray`}
    (ver models/calibration/calibrators.py), ajustado en el holdout de
    entrenamiento (nunca sobre estos mismos datos). Si no se provee para un
    mercado, `calibrated_probability` queda `None` en vez de inventarse.
    """
    stat_probs = market_probabilities(statistical_model, table)
    ml_probs = ml_model.predict_market_probabilities(table) if ml_model is not None else {}

    # PASO 1: probabilidad "cruda" del ensemble por mercado (posiblemente
    # inconsistente entre mercados mutuamente excluyentes -- ver
    # market_labels.py::MUTUALLY_EXCLUSIVE_GROUPS para la raiz del problema).
    p_model_by_market: dict[str, np.ndarray] = {}
    model_agreement_by_market: dict[str, np.ndarray] = {}
    for market_key in MARKET_DEFINITIONS:
        p_stat = stat_probs[market_key]
        p_ml = ml_probs.get(market_key)

        if p_ml is not None and ensemble_weights and market_key in ensemble_weights:
            w = ensemble_weights[market_key]
            p_model_by_market[market_key] = w * p_stat + (1 - w) * p_ml
            model_agreement_by_market[market_key] = 1.0 - np.abs(p_stat - p_ml)
        else:
            p_model_by_market[market_key] = p_stat
            model_agreement_by_market[market_key] = np.full(len(table), np.nan)

    # PASO 2: forzar que los grupos mutuamente excluyentes vuelvan a sumar 1
    # (correccion de raiz documentada en market_labels.py, no un parche de UI).
    p_model_by_market = renormalize_mutually_exclusive_groups(p_model_by_market)

    outputs: list[MarketPredictionOutput] = []
    for market_key in MARKET_DEFINITIONS:
        p_model = p_model_by_market[market_key]
        model_agreement = model_agreement_by_market[market_key]
        calibration_error = (calibration_errors or {}).get(market_key)
        calibrator = (calibrators or {}).get(market_key)
        calibrated_probs = calibrator.transform(np.asarray(p_model, dtype=float)) if calibrator is not None else None

        for i, (_, row) in enumerate(table.iterrows()):
            quote = (market_quotes or {}).get((row["match_id"], market_key))
            market_probability = quote["market_probability"] if quote else None
            market_odds = quote["market_odds"] if quote else None
            vig_removed = quote.get("vig_removed") if quote else None
            bookmakers_used = quote.get("bookmakers_used") if quote else None

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
                    bookmakers_used=bookmakers_used,
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
                    market_probability_source=quote.get("market_probability_source") if quote else None,
                    bookmakers_count=quote.get("bookmakers_count") if quote else None,
                    bookmakers_used=bookmakers_used,
                    market_odds_min=quote.get("market_odds_min") if quote else None,
                    market_odds_max=quote.get("market_odds_max") if quote else None,
                    market_odds_median=quote.get("market_odds_median") if quote else None,
                    market_odds_average=quote.get("market_odds_average") if quote else None,
                    calibrated_probability=float(calibrated_probs[i]) if calibrated_probs is not None else None,
                )
            )
    return outputs


def predict_secondary_markets_for_table(
    table: pd.DataFrame,
    cards_model: TotalCountPoissonModel,
    corners_model: TotalCountPoissonModel,
    market_quotes: dict[tuple[int, str], dict] | None = None,
) -> list[MarketPredictionOutput]:
    """Mercados de tarjetas/corners (seccion 18/19). `market_quotes`: igual
    forma que en `predict_markets_for_table` — opcional, porque las fuentes
    principales de datos no traen estas cuotas (The Odds API no las
    ofrece, ver docs/data_sources.md); si se proveen (p.ej. via
    API-Football), se usan igual que en goles. Sin ellas,
    `market_probability`/`edge` quedan `None` explicitamente en vez de
    inventar un valor.
    """
    outputs: list[MarketPredictionOutput] = []
    for model in (cards_model, corners_model):
        probs_by_market = model.predict_market_probabilities(table)
        for market_key, probs in probs_by_market.items():
            spec = SECONDARY_MARKET_DEFINITIONS[market_key]
            for i, (_, row) in enumerate(table.iterrows()):
                quote = (market_quotes or {}).get((row["match_id"], market_key))
                market_probability = quote["market_probability"] if quote else None
                market_odds = quote["market_odds"] if quote else None
                bookmakers_used = quote.get("bookmakers_used") if quote else None

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
                        bookmakers_used=bookmakers_used,
                    )
                )
                explanation = explain_logistic_pipeline(model.pipeline_, model.feature_cols_, row)
                outputs.append(
                    MarketPredictionOutput(
                        match_id=int(row["match_id"]),
                        market=market_key,
                        model_probability=float(probs[i]),
                        fair_odds=float(fair_odds(probs[i])),
                        market_probability=market_probability,
                        market_odds=market_odds,
                        vig_removed=quote.get("market_probability_source", "").endswith("no_vig")
                        if quote
                        else None,
                        edge=compute_edge(float(probs[i]), market_probability),
                        expected_value=compute_expected_value(float(probs[i]), market_odds),
                        confidence=confidence,
                        data_quality=data_quality,
                        explanation=explanation,
                        market_probability_source=quote.get("market_probability_source") if quote else None,
                        bookmakers_count=quote.get("bookmakers_count") if quote else None,
                        bookmakers_used=bookmakers_used,
                        market_odds_min=quote.get("market_odds_min") if quote else None,
                        market_odds_max=quote.get("market_odds_max") if quote else None,
                        market_odds_median=quote.get("market_odds_median") if quote else None,
                        market_odds_average=quote.get("market_odds_average") if quote else None,
                    )
                )
    return outputs
