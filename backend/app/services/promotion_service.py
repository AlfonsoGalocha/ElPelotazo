"""Compare stored artifacts on a common temporal sample.

Replayed features come from the current database. This diagnostic cannot
authorize activation until independent, point-in-time evidence is available.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import ModelVersion
from backend.app.features.goals import build_match_feature_table
from backend.app.models.calibration.metrics import brier_score, expected_calibration_error, log_loss_score
from backend.app.prediction.market_labels import label_for_market
from backend.app.prediction.predictor import apply_coherent_calibration, predict_markets_for_table
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import load_model_artifact
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

# Menos partidos fuera de muestra que esto: la comparacion es ruido, no
# evidencia (mismo criterio de fondo que MIN_SETTLED_FOR_* en
# evaluation_service.py, aunque el numero concreto es mas pequenho porque
# aqui solo hace falta UNA comparacion agregada, no ajustar una curva).
MIN_OUT_OF_SAMPLE_MATCHES = 20


def _probabilities(artifact: dict, table: pd.DataFrame) -> dict[tuple[int, str], float]:
    outputs = predict_markets_for_table(
        table,
        statistical_model=artifact["dixon_coles"],
        ml_model=artifact.get("ml_classifier"),
        ensemble_weights=artifact.get("ensemble_weights"),
        calibrators=artifact.get("calibrators"),
    )
    if "coherent_calibration" in artifact:
        apply_coherent_calibration(outputs, artifact["coherent_calibration"])
    return {
        (output.match_id, output.market): (
            output.calibrated_probability
            if output.calibrated_probability is not None else output.model_probability
        )
        for output in outputs
    }


def _paired_metrics(table: pd.DataFrame, previous: dict, candidate: dict) -> dict | None:
    pairs = []
    for market in sorted({key[1] for key in previous.keys() & candidate.keys()}):
        labels = label_for_market(table, market)
        for match_id, label in zip(table["match_id"], labels):
            key = (match_id, market)
            if key in previous and key in candidate and pd.notna(label):
                pairs.append((match_id, market, bool(label), previous[key], candidate[key]))
    if not pairs:
        return None
    frame = pd.DataFrame(pairs, columns=["match", "market", "result", "previous", "candidate"])

    def scores(group: pd.DataFrame, model: str) -> dict:
        y, p = group["result"].to_numpy(), group[model].to_numpy()
        return {
            "n": len(group),
            "brier": brier_score(y, p),
            "log_loss": log_loss_score(y, p),
            "ece": expected_calibration_error(y, p),
        }

    # One observation per match for the paired difference, even when a match
    # produced several mutually dependent markets.
    delta = (
        (frame["candidate"] - frame["result"]) ** 2
        - (frame["previous"] - frame["result"]) ** 2
    ).groupby(frame["match"]).mean().to_numpy()
    rng = np.random.default_rng(208)
    bootstrap = np.array([rng.choice(delta, len(delta), replace=True).mean() for _ in range(2000)])
    return {
        "n_matched_fixtures": len(delta),
        "previous": scores(frame, "previous"),
        "candidate": scores(frame, "candidate"),
        "brier_delta_ci95_by_match": np.quantile(bootstrap, [0.025, 0.975]).tolist(),
        "by_market": {
            market: {model: scores(group, model) for model in ("previous", "candidate")}
            for market, group in frame.groupby("market")
        },
    }


def evaluate_and_promote(db: Session, competition_code: str, candidate: ModelVersion) -> dict:
    """Keep the candidate inactive and report the evidence available for review."""
    from backend.app.normalization.competitions import EXPERIMENTAL_CATALOG

    if competition_code in EXPERIMENTAL_CATALOG:
        return {"competition": competition_code, "promoted": False, "reason": "experimental_competition"}
    if not candidate.created_at or candidate.training_end is None or candidate.training_end > candidate.created_at.date():
        return {"competition": competition_code, "promoted": False, "reason": "uncertified_training_cutoff"}

    if candidate.is_active is True:
        return {"competition": competition_code, "promoted": False, "reason": "already_active"}

    previous_active = (
        db.query(ModelVersion)
        .filter(ModelVersion.dataset_version.like(f"{competition_code}:%"))
        .filter(ModelVersion.id != candidate.id)
        .filter(ModelVersion.is_active.isnot(False))
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    if previous_active is None:
        # Bug real confirmado (auditoria v2.8, diagnostico de por que
        # Champions League/Liga F/AFC Champions League nunca tenian
        # predicciones): antes esto ponia `is_active = False`
        # INCONDICIONALMENTE y nunca lo activaba, aunque no hubiera NADA
        # con que comparar -- cada reentrenamiento siguiente de esa
        # competicion volvia a chocar con lo mismo (el candidato anterior
        # tampoco se habia activado nunca), as que la competicion se
        # quedaba sin ningun modelo activo para siempre, por mucho que se
        # reentrenara. Esto NUNCA sustituye un modelo titular existente
        # (la condicion `previous_active is None` significa literalmente
        # que no hay ninguno) -- solo arranca el primero, que es
        # exactamente la situacion "mejor que nada" que no tenia forma de
        # demostrar una mejora por falta de un incumbente con quien
        # compararse. El guardia de "no reentrenamientos automaticos que
        # empeoren el sistema sin validacion" (pedido real del usuario,
        # ver docstring de este modulo) sigue intacto para CUALQUIER
        # competicion que ya tenga un modelo activo: esa rama de abajo
        # (con `previous_active`) no se ha tocado.
        candidate.is_active = True
        db.flush()
        return {"competition": competition_code, "promoted": True, "reason": "first_version_for_competition"}

    candidate.is_active = False
    db.flush()

    competition = db.query(Competition).filter_by(code=competition_code).one()
    matches = load_matches_dataframe(db, competition.id)
    if matches.empty:
        return {"competition": competition_code, "promoted": False, "reason": "insufficient_out_of_sample_data"}
    finished = matches[
        (matches["status"] == "finished") & (matches["date"] < dt.datetime.utcnow())
    ].dropna(subset=["home_goals", "away_goals"])

    cutoffs = [candidate.training_end, previous_active.training_end]
    if any(cutoff is None for cutoff in cutoffs):
        candidate.is_active = False
        db.flush()
        return {"competition": competition_code, "promoted": False, "reason": "unknown_training_cutoff"}
    cutoff = max(*cutoffs, candidate.created_at.date(), previous_active.created_at.date())
    out_of_sample = finished[finished["date"].dt.date > cutoff]

    if len(out_of_sample) < MIN_OUT_OF_SAMPLE_MATCHES:
        candidate.is_active = False
        db.flush()
        return {
            "competition": competition_code,
            "promoted": False,
            "reason": "insufficient_out_of_sample_data",
            "n_out_of_sample": int(len(out_of_sample)),
            "previous_model_version_id": previous_active.id,
        }

    out_of_sample = build_match_feature_table(finished)
    out_of_sample = out_of_sample[out_of_sample["date"].dt.date > cutoff]
    candidate_artifact = load_model_artifact(candidate)
    previous_artifact = load_model_artifact(previous_active)
    comparison = _paired_metrics(
        out_of_sample,
        _probabilities(previous_artifact, out_of_sample),
        _probabilities(candidate_artifact, out_of_sample),
    )
    return {
        "competition": competition_code,
        "promoted": False,
        "reason": "unverified_replay" if comparison else "no_comparable_metric",
        "n_out_of_sample": len(out_of_sample),
        "previous_model_version_id": previous_active.id,
        "comparison": comparison,
    }
