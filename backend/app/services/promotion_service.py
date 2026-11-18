"""Gate de promocion de `ModelVersion` (seccion "Rendimiento del modelo",
pedido real del usuario).

Antes de esto, cada `football-edge train` generaba una `ModelVersion`
nueva que `generate_predictions_for_competition` empezaba a usar de
inmediato (la mas reciente por `created_at`, sin comparar nunca con la
anterior) -- exactamente lo que el usuario pidio evitar: "Solo se activa
una nueva version cuando demuestra mejoras fuera de muestra... No quiero
reentrenamientos automaticos que puedan empeorar el sistema sin
validacion."

Comparacion walk-forward REAL (nunca con datos ya vistos en el
entrenamiento): se evaluan candidato y version activa anterior sobre los
MISMOS partidos finalizados, estrictamente POSTERIORES al `training_end`
del candidato -- la version anterior, al haberse entrenado antes en el
tiempo, tampoco pudo haberlos usado para entrenar. Metrica de decision:
Brier score (nunca accuracy -- regla fija del proyecto, ver
sports-prediction-research y docs/architecture_audit.md), ponderado por
numero de observaciones validas de cada mercado en `MARKET_DEFINITIONS`
(1X2/over-under/BTTS/doble oportunidad -- los mercados "centrales" con
los que siempre se puede evaluar, a diferencia de corners que depende de
que la fuente traiga ese dato). Solo se activa el candidato si su Brier
agregado NO es peor (<=) que el de la version anterior.

Esta funcion NUNCA borra una `ModelVersion`: la que no se activa se queda
en la BD con `is_active=False`, visible para auditoria y para la
subseccion "Versiones del modelo" del frontend -- nunca descartada. Y
NUNCA lanza: si algo impide decidir (sin version anterior, sin datos
suficientes, sin metrica comparable), se documenta la razon en el
resultado y se deja al candidato sin activar (fail-safe: ante la duda, se
queda la version que ya estaba en produccion).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import ModelVersion
from backend.app.features.goals import build_match_feature_table
from backend.app.models.calibration.metrics import brier_score
from backend.app.prediction.market_labels import label_for_market
from backend.app.prediction.predictor import predict_markets_for_table
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import load_model_artifact
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

# Menos partidos fuera de muestra que esto: la comparacion es ruido, no
# evidencia (mismo criterio de fondo que MIN_SETTLED_FOR_* en
# evaluation_service.py, aunque el numero concreto es mas pequenho porque
# aqui solo hace falta UNA comparacion agregada, no ajustar una curva).
MIN_OUT_OF_SAMPLE_MATCHES = 20


def _aggregate_brier(artifact: dict, table: pd.DataFrame) -> float | None:
    """Brier score agregado (ponderado por tamanho de muestra) sobre todos
    los mercados de `MARKET_DEFINITIONS` que `predict_markets_for_table`
    sepa predecir con este artefacto. `None` si ningun mercado tuvo
    ninguna etiqueta real calculable (nunca se inventa una metrica)."""
    outputs = predict_markets_for_table(
        table,
        statistical_model=artifact["dixon_coles"],
        ml_model=artifact.get("ml_classifier"),
        ensemble_weights=artifact.get("ensemble_weights"),
    )
    by_market: dict[str, list[float]] = {}
    for output in outputs:
        by_market.setdefault(output.market, []).append(output.model_probability)

    weighted_sum = 0.0
    total_n = 0
    for market_key, probs in by_market.items():
        y_true = label_for_market(table, market_key).to_numpy()
        p = np.asarray(probs, dtype=float)
        valid = ~pd.isna(y_true)
        if not valid.any():
            continue
        score = brier_score(y_true[valid].astype(bool), p[valid])
        n_valid = int(valid.sum())
        weighted_sum += score * n_valid
        total_n += n_valid
    if total_n == 0:
        return None
    return weighted_sum / total_n


def evaluate_and_promote(db: Session, competition_code: str, candidate: ModelVersion) -> dict:
    """Decide si `candidate` (recien entrenado via `train_competition_models`,
    con `is_active` todavia sin tocar) sustituye a la version activa
    anterior de esta competicion. Deja `candidate.is_active` fijado en
    `True` o `False` segun la decision (hace `db.flush()`, nunca
    `db.commit()` -- el llamador decide cuando confirmar la transaccion,
    igual que el resto de `services/*.py`)."""
    previous_active = (
        db.query(ModelVersion)
        .filter(ModelVersion.dataset_version.like(f"{competition_code}:%"))
        .filter(ModelVersion.id != candidate.id)
        .filter(ModelVersion.is_active.isnot(False))
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    if previous_active is None:
        # Primera ModelVersion de esta competicion (o todas las anteriores
        # ya estaban desactivadas): nada con lo que comparar, se activa
        # directamente -- no tiene sentido exigir "mejoras fuera de
        # muestra" contra la nada.
        candidate.is_active = True
        db.flush()
        return {"competition": competition_code, "promoted": True, "reason": "no_previous_version"}

    competition = db.query(Competition).filter_by(code=competition_code).one()
    matches = load_matches_dataframe(db, competition.id)
    table = build_match_feature_table(matches)
    finished = table.dropna(subset=["home_goals", "away_goals"])

    cutoff = candidate.training_end
    out_of_sample = finished[finished["date"].dt.date > cutoff] if cutoff is not None else finished

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

    candidate_artifact = load_model_artifact(candidate)
    previous_artifact = load_model_artifact(previous_active)
    candidate_brier = _aggregate_brier(candidate_artifact, out_of_sample)
    previous_brier = _aggregate_brier(previous_artifact, out_of_sample)

    if candidate_brier is None or previous_brier is None:
        candidate.is_active = False
        db.flush()
        return {
            "competition": competition_code,
            "promoted": False,
            "reason": "no_comparable_metric",
            "n_out_of_sample": int(len(out_of_sample)),
            "previous_model_version_id": previous_active.id,
        }

    promoted = candidate_brier <= previous_brier
    if promoted:
        previous_active.is_active = False
        candidate.is_active = True
    else:
        candidate.is_active = False
    db.flush()

    logger.info(
        "promotion_service.decision: competition=%s candidate_id=%d previous_id=%d "
        "candidate_brier=%.4f previous_brier=%.4f promoted=%s",
        competition_code,
        candidate.id,
        previous_active.id,
        candidate_brier,
        previous_brier,
        promoted,
    )
    return {
        "competition": competition_code,
        "promoted": promoted,
        "reason": "walk_forward_comparison",
        "n_out_of_sample": int(len(out_of_sample)),
        "candidate_brier": candidate_brier,
        "previous_brier": previous_brier,
        "previous_model_version_id": previous_active.id,
    }
