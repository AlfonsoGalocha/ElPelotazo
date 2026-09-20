"""Deteccion de anomalias/contradicciones entre predicciones del MISMO
partido (seccion 18 del brief). Todo esto es POST-HOC sobre predicciones ya
generadas y ya persistidas (nunca cambia `model_probability`/`edge`/etc.,
solo anhade etiquetas para no MOSTRAR una senhal como fuerte cuando algo no
cuadra).

Tras el fix de `market_labels.renormalize_mutually_exclusive_groups`
(Fase 2) una CONTRADICCION real (dos predicciones del mismo grupo
mutuamente excluyente ambas con `model_probability > 0.5`) deberia ser
practicamente imposible -- pero se comprueba igualmente como red de
seguridad barata (seccion 6 de `docs/architecture_audit.md`, punto 1): si
alguna vez ocurre (p.ej. una prediccion vieja generada ANTES del fix,
todavia en la BD), esa senhal se excluye de "mejor prediccion" y de
"señales fuertes" en vez de mostrar Over y Under como fuertes a la vez.
"""

from __future__ import annotations

from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.market_labels import MUTUALLY_EXCLUSIVE_GROUPS
from backend.app.prediction.market_quality import market_quality_tier

CONTRADICTION = "CONTRADICCION"
LOW_RELIABILITY = "SENAL_BAJA_FIABILIDAD"
OUTLIER = "OUTLIER"
HIGH_PROBABILITY_LOW_VALUE = "HIGH_PROBABILITY_LOW_VALUE"

# Umbrales documentados aqui (no en el codigo de forma dispersa), pensados
# para ser conservadores: senhalar de mas erosiona confianza en las
# etiquetas tanto como no senhalar nada.
LOW_RELIABILITY_CONFIDENCE_THRESHOLD = 0.4  # confidence/data_quality por debajo => "baja fiabilidad"
HIGH_PROBABILITY_THRESHOLD = 0.75  # a partir de aqui se considera "alta probabilidad"
LOW_VALUE_EDGE_THRESHOLD = 0.03  # edge < 3pp junto a alta probabilidad => "poco valor" (cuota ya refleja lo obvio)


def _group_for_market(market: str) -> tuple[str, ...] | None:
    for group in MUTUALLY_EXCLUSIVE_GROUPS:
        if market in group:
            return group
    return None


def compute_anomaly_flags(
    prediction: Prediction,
    match_predictions: list[Prediction],
    settings: Settings | None = None,
) -> list[str]:
    """Etiquetas de anomalia para `prediction`, en el contexto de TODAS las
    predicciones del mismo partido (`match_predictions`, incluyendo a
    `prediction` misma). No excluye nada por si sola -- ver `is_strong_signal`
    y `best_prediction_for_match` para donde se usa cada etiqueta."""
    settings = settings or get_settings()
    flags: list[str] = []

    group = _group_for_market(prediction.market)
    if group is not None and prediction.model_probability > 0.5:
        for sibling in match_predictions:
            if sibling.id == prediction.id or sibling.market not in group:
                continue
            if sibling.model_probability > 0.5:
                flags.append(CONTRADICTION)
                break

    if prediction.bookmakers_used is not None:
        tier = market_quality_tier(
            prediction.bookmakers_used,
            prediction.market_odds_min,
            prediction.market_odds_max,
            prediction.market_odds_median,
            settings,
        )
        if tier == "LOW":
            flags.append(OUTLIER)

    if (
        prediction.confidence < LOW_RELIABILITY_CONFIDENCE_THRESHOLD
        or prediction.data_quality < LOW_RELIABILITY_CONFIDENCE_THRESHOLD
    ):
        flags.append(LOW_RELIABILITY)

    if prediction.model_probability >= HIGH_PROBABILITY_THRESHOLD and (
        prediction.edge or 0.0
    ) < LOW_VALUE_EDGE_THRESHOLD:
        flags.append(HIGH_PROBABILITY_LOW_VALUE)

    return flags


def is_strong_signal(flags: list[str]) -> bool:
    """Una senhal con CONTRADICCION nunca se muestra como "señal fuerte"
    (seccion 2 del brief: nunca Over y Under como fuertes a la vez)."""
    return CONTRADICTION not in flags


def best_prediction_per_match(
    predictions_by_match: dict[int, list[Prediction]],
    settings: Settings | None = None,
) -> dict[int, Prediction | None]:
    """Para cada `match_id`, la `Prediction` con mayor `signal_score` entre
    las que pasan `evaluate_quality_gate` Y no tienen CONTRADICCION (seccion
    1 del brief: "mejor prediccion" nunca es una senhal contradictoria)."""
    from backend.app.prediction.ranking import rank_signals

    settings = settings or get_settings()
    result: dict[int, Prediction | None] = {}
    for match_id, predictions in predictions_by_match.items():
        included, _ = rank_signals(predictions, settings)
        best = None
        for ranked in included:
            flags = compute_anomaly_flags(ranked.prediction, predictions, settings)
            if is_strong_signal(flags):
                best = ranked.prediction
                break
        result[match_id] = best
    return result
