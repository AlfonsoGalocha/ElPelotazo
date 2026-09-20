"""Entrenamiento y versionado de modelos (seccion 34: nunca sobrescribir en silencio)."""

from __future__ import annotations

import datetime as dt

import joblib
import numpy as np
from sqlalchemy.orm import Session

from backend.app.config.settings import get_settings
from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import ModelVersion
from backend.app.features.goals import build_match_feature_table, feature_columns
from backend.app.models.calibration.calibrators import IsotonicCalibrator
from backend.app.models.calibration.metrics import brier_score, log_loss_score
from backend.app.models.ensemble.ensemble import learn_ensemble_weight
from backend.app.models.goals.baseline import LeagueAverageBaseline
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.models.goals.ml_classifier import MarketClassifierModel
from backend.app.prediction.market_labels import (
    MARKET_DEFINITIONS,
    label_for_market,
    renormalize_mutually_exclusive_groups,
)
from backend.app.prediction.probability import market_probabilities
from backend.app.prediction.secondary_markets import (
    TotalCountPoissonModel,
    label_for_secondary_market,
)
from backend.app.services.match_service import load_matches_dataframe
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

MIN_HOLDOUT_MATCHES = 100  # una temporada en curso con pocos partidos jugados
# no es un holdout fiable (demasiado ruido estadistico); se usa la ultima
# temporada COMPLETA en su lugar y la temporada en curso se deja dentro del
# train (son datos reales validos, solo no sirven para evaluar).

MIN_CALIBRATION_MATCHES = 60  # ajustar un IsotonicRegression con menos
# puntos que esto sobreajusta al ruido del holdout (seccion 11 del brief):
# se prefiere NO calibrar (calibrated_probability queda None) a calibrar mal
# con una muestra insuficiente.


def _pick_holdout_season(table) -> str | None:
    finished = table.dropna(subset=["home_goals", "away_goals"])
    if finished.empty:
        return None
    counts = finished.sort_values("date")["season_label"].value_counts()
    seasons_in_order = list(dict.fromkeys(finished.sort_values("date")["season_label"]))
    if len(seasons_in_order) < 2:
        return None

    for candidate in reversed(seasons_in_order):
        if counts[candidate] >= MIN_HOLDOUT_MATCHES:
            return candidate
    return seasons_in_order[-1]


def train_competition_models(db: Session, competition_code: str) -> dict:
    """Entrena baseline + Dixon-Coles + clasificador ML para una competicion,
    evalua en la ultima temporada disponible como holdout, y persiste
    artefactos + ModelVersion. Devuelve un resumen (dict) del entrenamiento.
    """
    settings = get_settings()
    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        raise ValueError(f"Competicion no encontrada: {competition_code}")

    matches = load_matches_dataframe(db, competition.id)
    finished = matches.dropna(subset=["home_goals", "away_goals"])
    if len(finished) < 50:
        logger.warning("model_service.insufficient_data", extra={"competition": competition_code, "n": len(finished)})
        return {"competition": competition_code, "status": "insufficient_data", "n_matches": len(finished)}

    table = build_match_feature_table(matches)

    holdout_season = _pick_holdout_season(table)
    if holdout_season:
        train_table = table[table["season_label"] != holdout_season]
        eval_table = table[table["season_label"] == holdout_season]
    else:
        train_table, eval_table = table, table

    baseline = LeagueAverageBaseline().fit(train_table)
    dixon_coles = DixonColesModel().fit(train_table)
    ml_classifier = MarketClassifierModel().fit(train_table)

    metrics: dict = {"markets": {}}
    ensemble_weights: dict[str, float] = {}
    p_ensemble_by_market: dict[str, object] = {}
    y_true_by_market: dict[str, object] = {}

    eval_finished = eval_table.dropna(subset=["home_goals", "away_goals"])
    for market_key in MARKET_DEFINITIONS:
        market_metrics = {}
        if len(eval_finished) > 0:
            y_true = label_for_market(eval_finished, market_key).to_numpy()
            y_true_by_market[market_key] = y_true

            p_baseline = market_probabilities(baseline, eval_finished)[market_key]
            p_dc = market_probabilities(dixon_coles, eval_finished)[market_key]
            p_ml = ml_classifier.predict_market_probabilities(eval_finished).get(market_key)

            market_metrics["baseline"] = {
                "brier_score": brier_score(y_true, p_baseline),
                "log_loss": log_loss_score(y_true, p_baseline),
            }
            market_metrics["dixon_coles"] = {
                "brier_score": brier_score(y_true, p_dc),
                "log_loss": log_loss_score(y_true, p_dc),
            }
            if p_ml is not None:
                market_metrics["ml_classifier"] = {
                    "brier_score": brier_score(y_true, p_ml),
                    "log_loss": log_loss_score(y_true, p_ml),
                }
                weight, ensemble_loss = learn_ensemble_weight(p_dc, p_ml, y_true)
                ensemble_weights[market_key] = weight
                market_metrics["ensemble"] = {"weight_statistical": weight, "log_loss": ensemble_loss}
                p_ensemble_by_market[market_key] = weight * p_dc + (1 - weight) * p_ml
            else:
                p_ensemble_by_market[market_key] = p_dc
        metrics["markets"][market_key] = market_metrics

    # Calibracion (seccion 9/11 del brief): se ajusta sobre la MISMA
    # probabilidad ensemble RENORMALIZADA (grupos mutuamente excluyentes ya
    # forzados a sumar 1, ver market_labels.py) que se servira en produccion
    # -- calibrar sobre la version cruda serviria un `calibrated_probability`
    # que no corresponde a la probabilidad realmente usada para el edge.
    # Ajustado SOLO en el holdout (nunca visto por dixon_coles/ml_classifier
    # durante el fit), igual que `learn_ensemble_weight` -- no hay leakage.
    calibrators: dict[str, IsotonicCalibrator] = {}
    if len(eval_finished) >= MIN_CALIBRATION_MATCHES:
        p_ensemble_by_market = renormalize_mutually_exclusive_groups(p_ensemble_by_market)
        for market_key, p_ensemble in p_ensemble_by_market.items():
            y_true = y_true_by_market.get(market_key)
            if y_true is None or len(set(y_true.tolist())) < 2:
                continue
            calibrators[market_key] = IsotonicCalibrator().fit(np.asarray(p_ensemble, dtype=float), y_true)
        metrics["calibration"] = {
            "method": "isotonic" if calibrators else None,
            "n_holdout_matches": len(eval_finished),
            "markets_calibrated": sorted(calibrators.keys()),
        }
    else:
        metrics["calibration"] = {
            "method": None,
            "n_holdout_matches": len(eval_finished),
            "reason": f"menos de {MIN_CALIBRATION_MATCHES} partidos en el holdout",
        }

    # Tarjetas y corners (seccion 18/19 del roadmap): un unico Poisson sobre
    # el TOTAL del partido por familia de estadistica. Se entrenan y evaluan
    # igual que los mercados de goles, y se empaquetan en el MISMO artefacto
    # (una unica ejecucion de entrenamiento por competicion cubre las 3
    # familias) para no multiplicar side idle ModelVersions casi identicos.
    secondary_models: dict[str, TotalCountPoissonModel] = {}
    for stat_family in ("cards", "corners"):
        model = TotalCountPoissonModel(stat_family).fit(train_table)
        secondary_models[stat_family] = model

        if len(eval_finished) > 0:
            probs = model.predict_market_probabilities(eval_finished)
            for market_key, p in probs.items():
                y_true = label_for_secondary_market(eval_finished, market_key).to_numpy()
                metrics["markets"][market_key] = {
                    "poisson_total": {
                        "brier_score": brier_score(y_true, p),
                        "log_loss": log_loss_score(y_true, p),
                    }
                }

    version = dt.datetime.utcnow().isoformat()
    artifact_dir = settings.model_artifacts_path / competition_code
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{version.replace(':', '-')}.joblib"
    joblib.dump(
        {
            "baseline": baseline,
            "dixon_coles": dixon_coles,
            "ml_classifier": ml_classifier,
            "ensemble_weights": ensemble_weights,
            "calibrators": calibrators,
            "cards_model": secondary_models["cards"],
            "corners_model": secondary_models["corners"],
        },
        artifact_path,
    )

    model_version = ModelVersion(
        name="dixon_coles_plus_logistic_ensemble",
        version=version,
        market_family="goals",
        training_start=train_table["date"].min().date() if len(train_table) else None,
        training_end=train_table["date"].max().date() if len(train_table) else None,
        features=feature_columns(table),
        hyperparameters={"holdout_season": holdout_season},
        metrics=metrics,
        dataset_version=f"{competition_code}:{len(finished)}_matches",
        calibration_method="isotonic" if calibrators else None,
        artifact_path=str(artifact_path),
    )
    db.add(model_version)
    db.commit()
    db.refresh(model_version)

    logger.info(
        "model_service.trained",
        extra={"competition": competition_code, "model_version_id": model_version.id, "holdout_season": holdout_season},
    )
    return {
        "competition": competition_code,
        "status": "trained",
        "model_version_id": model_version.id,
        "n_matches": len(finished),
        "holdout_season": holdout_season,
        "metrics": metrics,
    }


def load_latest_model_version(db: Session, market_family: str = "goals") -> ModelVersion | None:
    return (
        db.query(ModelVersion)
        .filter_by(market_family=market_family)
        .order_by(ModelVersion.created_at.desc())
        .first()
    )


def load_model_artifact(model_version: ModelVersion) -> dict:
    return joblib.load(model_version.artifact_path)
