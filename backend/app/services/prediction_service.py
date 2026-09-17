"""Genera y persiste predicciones para partidos programados de una competicion."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.features.goals import build_match_feature_table
from backend.app.market.odds import best_available_quote
from backend.app.prediction.predictor import predict_markets_for_table
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
            quote = best_available_quote(odds_rows, market, line, selection)
            if quote:
                market_quotes[(match_id, market_key)] = {
                    "market_probability": quote.market_probability,
                    "market_odds": quote.market_odds,
                    "vig_removed": quote.vig_removed,
                }

    outputs = predict_markets_for_table(
        target_matches,
        statistical_model=artifact["dixon_coles"],
        ml_model=artifact["ml_classifier"],
        ensemble_weights=artifact.get("ensemble_weights"),
        market_quotes=market_quotes,
    )

    predictions = []
    for output in outputs:
        prediction = Prediction(
            match_id=output.match_id,
            market=output.market,
            line=MARKET_TO_ODDS_LOOKUP[output.market][1],
            selection=MARKET_TO_ODDS_LOOKUP[output.market][2],
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
            features_used={"feature_names": artifact["ml_classifier"].feature_cols_},
        )
        db.add(prediction)
        predictions.append(prediction)

    db.commit()
    return predictions
