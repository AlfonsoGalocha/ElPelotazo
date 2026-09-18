"""Genera y persiste predicciones para partidos programados de una competicion."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.features.goals import build_match_feature_table
from backend.app.market.consensus import compute_market_consensus
from backend.app.prediction.predictor import (
    predict_markets_for_table,
    predict_secondary_markets_for_table,
)
from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import load_model_artifact
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

MARKET_TO_ODDS_LOOKUP = {
    # market_key -> (market en match_odds, line, selection "positiva")
    "over_1_5": ("over_under_goals", 1.5, "over"),
    "over_2_5": ("over_under_goals", 2.5, "over"),
    "under_2_5": ("over_under_goals", 2.5, "under"),
    "over_3_5": ("over_under_goals", 3.5, "over"),
    "btts": ("btts", None, "yes"),
}


def _market_line_and_selection(market_key: str) -> tuple[float | None, str]:
    if market_key in MARKET_TO_ODDS_LOOKUP:
        _, line, selection = MARKET_TO_ODDS_LOOKUP[market_key]
        return line, selection
    spec = SECONDARY_MARKET_DEFINITIONS[market_key]
    return spec.line, spec.kind


def generate_predictions_for_competition(
    db: Session, competition_code: str, target_date: dt.date | None = None
) -> list[Prediction]:
    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        raise ValueError(f"Competicion no encontrada: {competition_code}")

    model_versions = (
        db.query(ModelVersion)
        .filter(ModelVersion.dataset_version.like(f"{competition_code}:%"))
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    if model_versions is None:
        logger.warning("prediction_service.no_model", extra={"competition": competition_code})
        return []

    artifact = load_model_artifact(model_versions)
    matches = load_matches_dataframe(db, competition.id)
    table = build_match_feature_table(matches)

    target_matches = table[table["home_goals"].isna()]
    if target_date is not None:
        target_matches = target_matches[target_matches["date"].dt.date == target_date]
    if target_matches.empty:
        return []

    match_odds_rows = {
        m.id: m.odds for m in db.query(Match).filter(Match.id.in_(target_matches["match_id"].tolist()))
    }
    market_quotes = {}
    for match_id, odds_rows in match_odds_rows.items():
        for market_key, (market, line, selection) in MARKET_TO_ODDS_LOOKUP.items():
            consensus = compute_market_consensus(odds_rows, market, line, selection)
            if consensus.market_probability is None:
                continue
            market_quotes[(match_id, market_key)] = {
                "market_probability": consensus.market_probability,
                "market_odds": consensus.market_odds,
                "vig_removed": consensus.market_probability_source == "consensus_no_vig"
                or consensus.market_probability_source == "single_book_no_vig",
                "market_probability_source": consensus.market_probability_source,
                "bookmakers_count": consensus.bookmakers_count,
                "bookmakers_used": consensus.bookmakers_used,
                "market_odds_min": consensus.min_odds,
                "market_odds_max": consensus.max_odds,
                "market_odds_median": consensus.median_odds,
                "market_odds_average": consensus.average_odds,
            }
            if consensus.outliers_removed:
                logger.info(
                    "market_consensus.outliers_removed: match_id=%d market=%s bookmakers=%s",
                    match_id,
                    market_key,
                    consensus.outliers_removed,
                )

    outputs = predict_markets_for_table(
        target_matches,
        statistical_model=artifact["dixon_coles"],
        ml_model=artifact["ml_classifier"],
        ensemble_weights=artifact.get("ensemble_weights"),
        market_quotes=market_quotes,
    )
    if "cards_model" in artifact and "corners_model" in artifact:
        outputs += predict_secondary_markets_for_table(
            target_matches, artifact["cards_model"], artifact["corners_model"]
        )

    # Sin esto, cada `predict`/`predict-upcoming`/`refresh` que se ejecuta
    # sobre los MISMOS partidos programados (algo normal: es el flujo pensado
    # para refrescar el dashboard varias veces al dia) va ACUMULANDO filas
    # nuevas en vez de sustituir las anteriores, porque `Prediction` nunca
    # tenia una clave unica por (match, market). El sintoma visible es un
    # mismo partido apareciendo duplicado (a veces triplicado) en "Las 5
    # mejores predicciones" o en el detalle de partido. Como estos partidos
    # aun no se han jugado, no hay ninguna razon para conservar la
    # prediccion vieja: se borra y se sustituye siempre por la mas reciente.
    target_match_ids = target_matches["match_id"].tolist()
    if target_match_ids:
        db.query(Prediction).filter(Prediction.match_id.in_(target_match_ids)).delete(
            synchronize_session=False
        )

    predictions = []
    for output in outputs:
        line, selection = _market_line_and_selection(output.market)
        prediction = Prediction(
            match_id=output.match_id,
            market=output.market,
            line=line,
            selection=selection,
            model_version_id=model_versions.id,
            model_probability=output.model_probability,
            market_probability=output.market_probability,
            market_odds=output.market_odds,
            fair_odds=output.fair_odds,
            edge=output.edge,
            expected_value=output.expected_value,
            confidence=output.confidence,
            data_quality=output.data_quality,
            explanation={"factors": output.explanation},
            features_used={"feature_names": [f["feature"] for f in output.explanation]},
            market_probability_source=output.market_probability_source,
            bookmakers_count=output.bookmakers_count,
            bookmakers_used=output.bookmakers_used,
            market_odds_min=output.market_odds_min,
            market_odds_max=output.market_odds_max,
            market_odds_median=output.market_odds_median,
            market_odds_average=output.market_odds_average,
        )
        db.add(prediction)
        predictions.append(prediction)

    db.commit()
    return predictions
