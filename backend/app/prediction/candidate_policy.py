"""Politica CANDIDATA consciente de contradicciones (v2.8.2, seccion 4).

`rank_signals_v3`/`ranking.py` NUNCA se toca ni se sustituye -- sigue
siendo la referencia con la que esta politica se compara siempre (v3
"gana" por defecto hasta que `selector_decision_service.py`/Fase 8
demuestre con datos reales, fuera de muestra, que esta politica elige
mejor). Motivado por un caso real: Stuttgart-Frankfurt `over_3_5`
(categoria D, contradiccion FUERTE segun `prediction/anomaly.py`) fue la
UNICA oportunidad que v3 encontro entre 25 partidos capturados, porque v3
nunca mira contradiccion/categoria -- solo informa.

Diferencia explicita (pedido real del usuario, "no bloquees
indiscriminadamente"):
- `ContradictionLevel.FUERTE`: excluye (nueva evidencia de que el ranking
  probabilidad-primero esta premiando justo el tipo de ruido -- violacion
  de monotonicidad entre lineas, calibrador sobreajustado -- que ya se
  audito en `backtesting/calibration_audit.py`).
- `ContradictionLevel.MODERADA`/`EVIDENCIA_INSUFICIENTE`: NUNCA excluyen
  por si solas -- "ausencia de evidencia" (pocos partidos previos) no es
  lo mismo que "evidencia que contradice la prediccion", y una exclusion
  en bloque de cualquiera de las dos dejaria la app vacia otra vez (ya
  ocurrio con los umbrales viejos, ver `ranking.py` seccion 1).
- `LEVE`/`SIN_CONTRADICCION`: nunca excluyen, solo desempatan.
"""

from __future__ import annotations

from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import ContradictionLevel
from backend.app.prediction.ranking import (
    ExcludedSignal,
    ExclusionReason,
    RankedSignal,
    calibrated_probability_or_fallback,
    conservative_probability,
    ev_with_probability,
    evaluate_quality_gate_v3,
    select_one_per_match,
)

# Orden de severidad para desempatar (seccion 5 del pedido original de
# v3, extendido con UN criterio mas): `SIN_CONTRADICCION` es lo mejor,
# `MODERADA` lo peor que todavia se permite (no excluye, pero desempata
# peor que evidencia insuficiente -- hay evidencia real y SI contradice,
# a diferencia de simplemente no haber evidencia). `FUERTE` nunca llega
# aqui (se excluye en el filtro duro), pero se documenta su peso igual
# por completitud/tests.
_CONTRADICTION_SEVERITY: dict[ContradictionLevel, int] = {
    ContradictionLevel.SIN_CONTRADICCION: 0,
    ContradictionLevel.LEVE: 1,
    ContradictionLevel.EVIDENCIA_INSUFICIENTE: 2,
    ContradictionLevel.MODERADA: 3,
    ContradictionLevel.FUERTE: 4,
}


def evaluate_quality_gate_candidate(
    prediction: Prediction,
    contradiction_level: ContradictionLevel,
    settings: Settings | None = None,
    market_track_record: float | None = None,
    now=None,
) -> ExclusionReason | None:
    """Mismo filtro duro que v3 (`evaluate_quality_gate_v3`, sin tocarlo
    ni duplicar su logica) + UNA exclusion adicional: contradiccion
    `FUERTE`. Se evalua la contradiccion DESPUES del filtro de v3 (nunca
    antes): si la prediccion ya esta excluida por v3 por otro motivo, ese
    motivo se reporta tal cual -- la contradiccion solo decide algo nuevo
    para las que SI pasarian v3."""
    settings = settings or get_settings()
    reason = evaluate_quality_gate_v3(prediction, settings, market_track_record, now=now)
    if reason is not None:
        return reason
    if contradiction_level == ContradictionLevel.FUERTE:
        return ExclusionReason.STRONG_CONTRADICTION
    return None


def ranking_key_candidate(
    prediction: Prediction, contradiction_level: ContradictionLevel, market_track_record: float
) -> tuple[float, float, float, int, float, float, float]:
    """Misma tupla lexicografica que `ranking_key_v3` (probabilidad
    calibrada, EV conservador, fiabilidad historica, incertidumbre,
    calidad de datos, edge) con UN criterio nuevo insertado justo
    DESPUES de la fiabilidad historica y ANTES de la incertidumbre:
    severidad de contradiccion invertida (menos severidad = mejor, se
    guarda NEGATIVA para que el sort descendente la prefiera). Nunca
    sustituye los criterios de v3 -- solo anhade un desempate mas fino
    entre candidatas que ya pasaron el filtro duro."""
    calibrated = calibrated_probability_or_fallback(prediction)
    conservative = conservative_probability(prediction)
    ev_conservative = ev_with_probability(conservative, prediction.market_odds) or 0.0
    if prediction.uncertainty_low is not None and prediction.uncertainty_high is not None:
        width = max(0.0, prediction.uncertainty_high - prediction.uncertainty_low)
    else:
        width = 1.0
    severity = _CONTRADICTION_SEVERITY.get(contradiction_level, max(_CONTRADICTION_SEVERITY.values()))
    return (
        calibrated,
        ev_conservative,
        market_track_record,
        -severity,
        -width,
        prediction.data_quality,
        prediction.edge or 0.0,
    )


def rank_candidates(
    predictions: list[Prediction],
    contradiction_levels: dict[int, ContradictionLevel],
    settings: Settings | None = None,
    market_track_records: dict[tuple[str, str], float] | None = None,
    one_per_match: bool = True,
    now=None,
) -> tuple[list[RankedSignal], list[ExcludedSignal]]:
    """Mismo patron exacto que `rank_signals_v3`: filtra con
    `evaluate_quality_gate_candidate`, ordena con `ranking_key_candidate`.
    `contradiction_levels`: {prediction.id: ContradictionLevel}, calculado
    por el llamador (ver `selector_decision_service.py`) reusando
    `classify_contradiction`/`build_market_evidence` -- esta funcion nunca
    calcula evidencia por si misma, solo consume el resultado."""
    settings = settings or get_settings()
    included: list[RankedSignal] = []
    excluded: list[ExcludedSignal] = []

    for prediction in predictions:
        track_record = None
        if market_track_records and prediction.match is not None:
            key = (prediction.match.competition.code, prediction.market)
            track_record = market_track_records.get(key)
        level = contradiction_levels.get(prediction.id, ContradictionLevel.EVIDENCIA_INSUFICIENTE)

        reason = evaluate_quality_gate_candidate(prediction, level, settings, track_record, now=now)
        if reason is not None:
            excluded.append(ExcludedSignal(prediction=prediction, reason=reason))
            continue
        key_tuple = ranking_key_candidate(prediction, level, track_record)
        included.append(RankedSignal(prediction=prediction, score=key_tuple))

    included.sort(key=lambda s: s.score, reverse=True)
    if one_per_match:
        included = select_one_per_match(included)
    return included, excluded
