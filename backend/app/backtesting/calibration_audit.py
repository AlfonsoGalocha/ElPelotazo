"""Auditoria de sobreconfianza/infraconfianza (v2.8.2, seccion 2 del pedido).

Motivado por un hallazgo real: el ECE ponderado sobre las 1.203
predicciones pre-partido genuinas liquidadas (`/models/performance`) es
0,245 -- alto. Esta auditoria desglosa POR RANGO de probabilidad
(50-60/60-70/70-80/80-90/90-100%), por mercado/competicion/version de
modelo, y compara `model_probability` (original) contra
`calibrated_probability` (calibrada) por separado -- nunca mezcladas,
nunca se asume cual es mejor sin medirlo.

Reutiliza deliberadamente lo que ya existe, en vez de reimplementarlo:
- `historical_evidence`/`stored_pre_kickoff` (`prediction/eligibility.py`):
  el MISMO filtro que ya usa `/models/performance` para excluir
  predicciones reconstruidas despues del partido -- esta auditoria nunca
  mezcla reconstruidas con genuinas, exactamente igual que el dashboard.
- `brier_score`/`log_loss_score`/`expected_calibration_error`
  (`models/calibration/metrics.py`).
- `wilson_interval` (`prediction/uncertainty.py`) para el intervalo de
  incertidumbre de la frecuencia real observada en cada bucket.

Pieza nueva de verdad: el flag de "violacion de monotonicidad entre
lineas anidadas" (causa raiz confirmada del caso Stuttgart-Frankfurt,
ver `docs`/informe v2.8.2) -- permite separar cuanto de la sobreconfianza
medida se explica por esa causa concreta y cuanto queda sin explicar.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import numpy as np
from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.models.calibration.metrics import brier_score, log_loss_score
from backend.app.prediction.eligibility import historical_evidence
from backend.app.prediction.uncertainty import wilson_interval

# Rangos pedidos explicitamente por el usuario -- nunca un binning
# arbitrario de n_bins=10 como el de `reliability_curve` (ese ya existe
# para otro proposito: aqui se quieren EXACTAMENTE estos 5 tramos).
PROBABILITY_BUCKET_EDGES: list[tuple[float, float]] = [
    (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0),
]

# Mismo orden de magnitud que el resto del proyecto
# (MIN_CALIBRATION_MATCHES=60, MIN_SETTLED_FOR_TRACK_RECORD=30,
# MIN_SAMPLE_SIZE_FOR_RELIABLE_STATS=30 en evaluation_service.py) --
# nunca se clasifica sobre/infraconfianza con una muestra mas chica que
# esto, se marca "muestra_insuficiente" en vez de forzar una conclusion.
MIN_BUCKET_SAMPLE = 20

# Lineas anidadas de la MISMA familia (el total del partido supera un
# umbral creciente) -- la probabilidad calibrada de una linea mas alta
# NUNCA puede superar la de una linea mas baja de la misma familia/
# partido. Documentado aqui porque es la causa raiz confirmada del caso
# Stuttgart-Frankfurt (goles) y, estructuralmente, nunca debería ocurrir
# en corners (un unico lambda de Poisson compartido -- ver
# `prediction/secondary_markets.py`), aunque SI puede ocurrir ahi tambien
# si el calibrador isotonico (independiente por linea) gana la
# comparacion de la Fase 1 en vez de `corner_survival`.
NESTED_OVER_LINE_FAMILIES: dict[str, list[str]] = {
    "goals_total": ["over_1_5", "over_2_5", "over_3_5"],
    "corners_total": ["corners_over_8_5", "corners_over_9_5", "corners_over_10_5"],
}


@dataclass
class CalibrationBucket:
    probability_range: str
    probability_column: str  # "model_probability" | "calibrated_probability"
    n: int
    mean_predicted_probability: float
    empirical_frequency: float
    difference: float  # media predicha - frecuencia real (>0 = sobreconfianza)
    empirical_frequency_ci95: tuple[float, float]
    brier_score: float
    log_loss: float
    n_with_monotonicity_violation: int
    classification: str  # "sobreconfianza" | "infraconfianza" | "calibrado" | "muestra_insuficiente"


@dataclass
class CalibrationAuditSegment:
    competition_code: str
    market: str
    model_version_id: int
    buckets: list[CalibrationBucket] = field(default_factory=list)


@dataclass
class CalibrationAuditReport:
    generated_at: str
    cohort: str  # igual etiqueta que real_performance_report, mismo filtro
    n_predictions_considered: int
    segments: list[CalibrationAuditSegment] = field(default_factory=list)


def _classify(difference: float, ci_low: float, ci_high: float, mean_predicted: float, n: int) -> str:
    if n < MIN_BUCKET_SAMPLE:
        return "muestra_insuficiente"
    # Sobreconfianza/infraconfianza solo se declara si la media predicha
    # cae FUERA del intervalo de confianza del 95% de la frecuencia real
    # observada -- nunca por una diferencia de signo sin respaldo
    # estadistico (mismo criterio que el resto del proyecto: no declarar
    # una mejora/empeoramiento sin evidencia).
    if mean_predicted > ci_high:
        return "sobreconfianza"
    if mean_predicted < ci_low:
        return "infraconfianza"
    return "calibrado"


def _nested_line_violation_prediction_ids(rows: list[tuple[Prediction, PredictionResult, Competition, Match]]) -> set[int]:
    """IDs de `Prediction` cuya `calibrated_probability` (o, a falta de
    ella, `model_probability`) es mayor que la de un hermano de linea mas
    alta DEL MISMO partido y familia -- la violacion encontrada de verdad
    en produccion (Stuttgart-Frankfurt, `over_3_5` > `over_2_5`)."""
    by_match: dict[int, dict[str, tuple[int, float]]] = defaultdict(dict)
    for prediction, _result, _competition, match in rows:
        probability = (
            prediction.calibrated_probability
            if prediction.calibrated_probability is not None
            else prediction.model_probability
        )
        by_match[match.id][prediction.market] = (prediction.id, probability)

    violating: set[int] = set()
    for markets in by_match.values():
        for family in NESTED_OVER_LINE_FAMILIES.values():
            present = [markets[m] for m in family if m in markets]
            for i in range(len(present) - 1):
                (id_low, p_low), (id_high, p_high) = present[i], present[i + 1]
                if p_high > p_low + 1e-9:
                    violating.add(id_low)
                    violating.add(id_high)
    return violating


def calibration_audit_report(
    db: Session,
    *,
    competition_code: str | None = None,
    market: str | None = None,
    model_version_id: int | None = None,
    as_of: dt.datetime | None = None,
) -> CalibrationAuditReport:
    """Mismo filtro/cohorte que `evaluation_service.real_performance_report`
    (`historical_evidence`, `cohort="pre_kickoff_timestamp_screened"`):
    nunca mezcla predicciones reconstruidas despues del partido."""
    query = (
        db.query(Prediction, PredictionResult, Competition, Match)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .join(Match, Match.id == Prediction.match_id)
        .join(Competition, Competition.id == Match.competition_id)
    )
    if competition_code:
        query = query.filter(Competition.code == competition_code)
    if market:
        query = query.filter(Prediction.market == market)
    if model_version_id:
        query = query.filter(Prediction.model_version_id == model_version_id)
    if as_of is not None:
        query = query.filter(PredictionResult.settled_at <= as_of, Match.kickoff_utc < as_of)

    rows = historical_evidence(query).all()
    violating_ids = _nested_line_violation_prediction_ids(rows)

    segments: dict[tuple[str, str, int], list[tuple[Prediction, PredictionResult]]] = defaultdict(list)
    for prediction, result, competition, _match in rows:
        if result.outcome is None or (result.settlement_status or "").lower() == "void":
            continue
        segments[(competition.code, prediction.market, prediction.model_version_id)].append((prediction, result))

    report = CalibrationAuditReport(
        generated_at=dt.datetime.utcnow().isoformat() + "Z",
        cohort="pre_kickoff_timestamp_screened",
        n_predictions_considered=sum(len(v) for v in segments.values()),
    )
    for (comp_code, market_key, version_id), items in segments.items():
        segment = CalibrationAuditSegment(competition_code=comp_code, market=market_key, model_version_id=version_id)
        for column in ("model_probability", "calibrated_probability"):
            values = [
                (getattr(prediction, column), bool(result.outcome), prediction.id)
                for prediction, result in items
                if getattr(prediction, column) is not None
            ]
            for low, high in PROBABILITY_BUCKET_EDGES:
                in_range = [
                    (p, y, pid) for p, y, pid in values
                    if (low <= p < high) or (high == 1.0 and p == 1.0)
                ]
                n = len(in_range)
                if n == 0:
                    continue
                probs = np.array([p for p, _y, _pid in in_range])
                outcomes = np.array([y for _p, y, _pid in in_range])
                mean_predicted = float(np.mean(probs))
                empirical_frequency = float(np.mean(outcomes))
                ci_low, ci_high = wilson_interval(empirical_frequency, n)
                n_violating = sum(1 for _p, _y, pid in in_range if pid in violating_ids)
                segment.buckets.append(
                    CalibrationBucket(
                        probability_range=f"{int(low * 100)}-{int(high * 100)}%",
                        probability_column=column,
                        n=n,
                        mean_predicted_probability=mean_predicted,
                        empirical_frequency=empirical_frequency,
                        difference=mean_predicted - empirical_frequency,
                        empirical_frequency_ci95=(ci_low, ci_high),
                        brier_score=brier_score(outcomes, probs),
                        log_loss=log_loss_score(outcomes, probs),
                        n_with_monotonicity_violation=n_violating,
                        classification=_classify(mean_predicted - empirical_frequency, ci_low, ci_high, mean_predicted, n),
                    )
                )
        if segment.buckets:
            report.segments.append(segment)
    return report


def calibration_audit_report_as_dict(db: Session, **kwargs) -> dict:
    report = calibration_audit_report(db, **kwargs)
    return {
        "generated_at": report.generated_at,
        "cohort": report.cohort,
        "n_predictions_considered": report.n_predictions_considered,
        "segments": [
            {
                "competition_code": s.competition_code,
                "market": s.market,
                "model_version_id": s.model_version_id,
                "buckets": [asdict(b) for b in s.buckets],
            }
            for s in report.segments
        ],
    }
