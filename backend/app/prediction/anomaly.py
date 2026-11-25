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

from enum import Enum
from typing import TYPE_CHECKING

from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.evidence import MarketEvidence
from backend.app.prediction.market_labels import MUTUALLY_EXCLUSIVE_GROUPS
from backend.app.prediction.market_quality import market_quality_tier
from backend.app.prediction.ranking import calibrated_probability_or_fallback

if TYPE_CHECKING:
    from sklearn.isotonic import IsotonicRegression

CONTRADICTION = "CONTRADICCION"
LOW_RELIABILITY = "SENAL_BAJA_FIABILIDAD"
OUTLIER = "OUTLIER"
HIGH_PROBABILITY_LOW_VALUE = "HIGH_PROBABILITY_LOW_VALUE"
# Pedido real del usuario ("revision motor 2.0", Fase 3/5/8): un edge
# enorme (modelo 80% vs mercado 40%) NO es automaticamente una oportunidad
# excelente -- puede ser el modelo sobreestimando. `HIGH_PROBABILITY_LOW_VALUE`
# (arriba) cubre el caso contrario (probabilidad alta, edge CHICO); esta
# flag cubre el caso que de verdad motivo la revision: la probabilidad en
# si misma esta poco respaldada (intervalo de incertidumbre ancho, ver
# prediction/uncertainty.py), independientemente de lo grande que sea el
# edge resultante -- justo lo que Fase 8 pide mostrar como "Alta
# incertidumbre" y Fase 6 pide que NUNCA entre en "mejores recomendaciones"
# por mucho edge bruto que tenga (ver `is_strong_signal` mas abajo).
HIGH_UNCERTAINTY = "ALTA_INCERTIDUMBRE"

# Umbrales documentados aqui (no en el codigo de forma dispersa), pensados
# para ser conservadores: senhalar de mas erosiona confianza en las
# etiquetas tanto como no senhalar nada.
LOW_RELIABILITY_CONFIDENCE_THRESHOLD = 0.4  # confidence/data_quality por debajo => "baja fiabilidad"
HIGH_PROBABILITY_THRESHOLD = 0.75  # a partir de aqui se considera "alta probabilidad"
LOW_VALUE_EDGE_THRESHOLD = 0.03  # edge < 3pp junto a alta probabilidad => "poco valor" (cuota ya refleja lo obvio)
# Ancho del intervalo de incertidumbre (uncertainty_high - uncertainty_low)
# a partir del cual se considera "poco respaldada". PRIMER INTENTO con un
# razonamiento puramente teorico sobre la formula de Wilson (una cifra
# "redonda" como 0.30) resulto ser demasiado agresivo: medido de verdad
# sobre un dataset sintetico realista (5 temporadas, 10 equipos,
# `predict_markets_for_table` end-to-end con ensemble DC+ML), la MEDIANA
# del ancho de intervalo de TODAS las predicciones ya es ~0.32 -- un
# umbral de 0.30 marcaria mas de la mitad de las predicciones como
# "alta incertidumbre", vaciando de golpe "mejores senhales"/"mejor
# prediccion" en la suite de tests (regresion real detectada: 12 tests
# que antes pasaban empezaron a fallar). Percentiles reales medidos en ese
# mismo dataset: p50=0.32, p75=0.43, p90=0.59, p95=0.68, p99=0.81.
#
# 0.60 se eligio para marcar solo la cola realmente ancha (~p90 de ese
# dataset sintetico), no la mitad de las predicciones -- pero sigue siendo
# una PRIMERA aproximacion: la distribucion real en produccion (datos
# reales, no Poisson sintetico) puede ser distinta. Si en la app real esta
# flag aparece demasiado poco o demasiado (nunca se ve, o se ve en casi
# todo), hay que remedir sobre predicciones reales ya liquidadas y ajustar
# este numero con esa evidencia, no a ojo otra vez.
HIGH_UNCERTAINTY_WIDTH_THRESHOLD = 0.60


class ContradictionLevel(str, Enum):
    """Fase C de la revision v2.8: detector de contradicciones GRADUADO
    (antes solo existia `CONTRADICTION` binaria, section arriba -- entre
    predicciones del MISMO partido). Esto es distinto: compara la propia
    probabilidad calibrada de UNA prediccion contra la frecuencia historica
    REAL de su mercado (`prediction/evidence.py`, Fase B) -- el caso real
    que motivo la revision ("Over 9.5 corners pese a baja frecuencia
    historica", "doble oportunidad con buena cuota pese a baja probabilidad
    real"). Ver `classify_contradiction`. Al igual que `compute_anomaly_
    flags`, esto es POST-HOC: nunca excluye nada por si solo, solo etiqueta
    (alimenta la categoria de oportunidad, Fase D)."""

    SIN_CONTRADICCION = "SIN_CONTRADICCION"
    LEVE = "LEVE"
    MODERADA = "MODERADA"
    FUERTE = "FUERTE"
    EVIDENCIA_INSUFICIENTE = "EVIDENCIA_INSUFICIENTE"


# Minimo de partidos reales (nunca liquidaciones -- `evidence.py` cuenta
# partidos de forma/historial, no apuestas resueltas) en la ventana elegida
# para considerar que hay evidencia suficiente. Derivado del error estandar
# de una proporcion binomial (SE = sqrt(p(1-p)/n), peor caso p=0.5): para
# que SE no supere 0.20 (una diferencia menor a 20pp entre la probabilidad
# del modelo y la frecuencia historica seria ruido, no una contradiccion
# real) se necesita n >= 0.25/0.04 = 6.25 -> 7. No es un numero redondo
# elegido a ojo, es la cota que hace que el propio z-score de abajo sea
# informativo en vez de puro ruido de muestra pequenha.
MIN_SAMPLE_FOR_CONTRADICTION_EVIDENCE = 7
# Si menos de la mitad de los partidos que se intentaron usar en la
# ventana elegida tenian el dato real disponible (ver `MarketEvidence.
# data_coverage`), no se confia en esa ventana por mucho que su `n` ya
# filtrado parezca suficiente -- demasiados partidos de los que se
# intentaron consultar no tenian el dato.
MIN_DATA_COVERAGE_FOR_CONTRADICTION_EVIDENCE = 0.5
# Varianza minima usada al calcular el error estandar de la frecuencia
# historica (ver `_standard_error` mas abajo): sin esto, una ventana
# perfectamente consistente (frecuencia exactamente 0 o 1, p.ej. "ganaron
# los 7 ultimos partidos de local") daria SE=0 y transformaria cualquier
# diferencia, por pequenha que sea, en una contradiccion "FUERTE" infinita
# -- un artefacto de la formula, no una señal real. 0.01 equivale a tratar
# la resolucion efectiva de la frecuencia historica como de unos 10 puntos
# porcentuales incluso en el caso mas consistente, conservador pero no
# arbitrario (misma idea que la correccion de continuidad de Wilson, ya
# usada en prediction/uncertainty.py).
MIN_VARIANCE_FLOOR = 0.01
# Bandas de z-score (desviaciones del error estandar) para LEVE/MODERADA/
# FUERTE: convencion estadistica estandar (regla 68/95/99.7), no un corte
# inventado para este modulo -- y resuelve exactamente lo que pedia la
# revision ("un hueco pequenho con muestra grande debe pesar mas que un
# hueco grande con muestra chica"): el z-score ya divide por el error
# estandar, que decrece con `n`, asi que la MISMA diferencia en puntos
# porcentuales produce un z mayor (mas contradiccion) cuanto mayor es la
# muestra que la respalda.
CONTRADICTION_Z_LEVE = 1.0
CONTRADICTION_Z_MODERADA = 2.0
CONTRADICTION_Z_FUERTE = 3.0

# Ventanas de `MarketEvidence.windows`/`line_hit_frequency` candidatas,
# en orden de preferencia cuando varias cumplen el minimo de muestra --
# "season" y "last20" priorizadas por ser tipicamente las de mayor `n`
# real (ver docstring de evidence.py); "home"/"away" y "last5" solo como
# ultimo recurso (menos partidos o menos recientes). El criterio real de
# seleccion sigue siendo el `n` de cada ventana (ver `_most_reliable_window`
# mas abajo): este orden solo rompe empates.
_CONTRADICTION_WINDOW_PRIORITY = ("season", "last20", "last10", "home", "away", "last5")


def _standard_error(frequency: float, n: int) -> float:
    variance = max(frequency * (1.0 - frequency), MIN_VARIANCE_FLOOR)
    return (variance / n) ** 0.5


def _most_reliable_window(evidence: MarketEvidence) -> str | None:
    """La ventana con mas `n` entre las que cumplen el minimo de muestra
    (ver `MIN_SAMPLE_FOR_CONTRADICTION_EVIDENCE`), con `_CONTRADICTION_
    WINDOW_PRIORITY` solo para romper empates. `None` si ninguna cumple."""
    eligible = [
        name
        for name in _CONTRADICTION_WINDOW_PRIORITY
        if name in evidence.windows
        and evidence.windows[name].n >= MIN_SAMPLE_FOR_CONTRADICTION_EVIDENCE
        and evidence.line_hit_frequency.get(name) is not None
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda name: evidence.windows[name].n)


def classify_contradiction(prediction: Prediction, evidence: MarketEvidence) -> ContradictionLevel:
    """Nivel de contradiccion entre la probabilidad calibrada de
    `prediction` y la frecuencia historica REAL de su mercado (`evidence`,
    de `prediction/evidence.py::build_market_evidence`). Nunca excluye
    nada por si solo (ver docstring de `ContradictionLevel`)."""
    if evidence.data_coverage < MIN_DATA_COVERAGE_FOR_CONTRADICTION_EVIDENCE:
        return ContradictionLevel.EVIDENCIA_INSUFICIENTE

    window = _most_reliable_window(evidence)
    if window is None:
        return ContradictionLevel.EVIDENCIA_INSUFICIENTE

    frequency = evidence.line_hit_frequency[window]
    n = evidence.windows[window].n
    probability = calibrated_probability_or_fallback(prediction)

    z = abs(probability - frequency) / _standard_error(frequency, n)
    if z < CONTRADICTION_Z_LEVE:
        return ContradictionLevel.SIN_CONTRADICCION
    if z < CONTRADICTION_Z_MODERADA:
        return ContradictionLevel.LEVE
    if z < CONTRADICTION_Z_FUERTE:
        return ContradictionLevel.MODERADA
    return ContradictionLevel.FUERTE


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

    # getattr defensivo (no `prediction.uncertainty_low` directo): algunos
    # llamadores de este modulo pasan objetos duck-typed ligeros en vez de
    # un `Prediction` real (ver tests), que no siempre definen estos dos
    # atributos -- ausentes se trata igual que `None` (sin intervalo
    # calculado), nunca como un error.
    uncertainty_low = getattr(prediction, "uncertainty_low", None)
    uncertainty_high = getattr(prediction, "uncertainty_high", None)
    if (
        uncertainty_low is not None
        and uncertainty_high is not None
        and (uncertainty_high - uncertainty_low) >= HIGH_UNCERTAINTY_WIDTH_THRESHOLD
    ):
        flags.append(HIGH_UNCERTAINTY)

    return flags


def is_strong_signal(flags: list[str]) -> bool:
    """Una senhal con CONTRADICCION o ALTA_INCERTIDUMBRE nunca se muestra
    como "señal fuerte" (seccion 2 del brief: nunca Over y Under como
    fuertes a la vez; seccion 6/8 de la revision "motor 2.0": un edge
    bruto grande respaldado por una probabilidad de intervalo ancho tampoco
    es una "mejor recomendacion", por mucho que destaque en el ranking
    bruto)."""
    return CONTRADICTION not in flags and HIGH_UNCERTAINTY not in flags


def best_prediction_per_match(
    predictions_by_match: dict[int, list[Prediction]],
    settings: Settings | None = None,
    edge_reliability_curve: IsotonicRegression | None = None,
    odds_reliability_curve: IsotonicRegression | None = None,
) -> dict[int, Prediction | None]:
    """Para cada `match_id`, la `Prediction` con mayor `signal_score_v2`
    entre las que pasan `evaluate_quality_gate` Y no tienen CONTRADICCION
    (seccion 1 del brief: "mejor prediccion" nunca es una senhal
    contradictoria). `signal_score_v2` es la formula de PRODUCCION desde
    la revision "motor 2.0" (ver prediction/ranking.py) -- usa EV ajustado
    al riesgo en vez de edge bruto, para no premiar un edge enorme cuando
    la propia probabilidad esta poco respaldada.

    `edge_reliability_curve`/`odds_reliability_curve`: opcionales,
    pasados por el llamador (que SI tiene acceso a BD) via
    `services/evaluation_service.py::fit_edge_reliability_curve`/
    `fit_odds_reliability_curve` -- esta funcion se mantiene PURA
    (sin BD), igual que `rank_signals_v2`. `None` (por defecto) es
    neutro: nunca penaliza por falta de dato."""
    from backend.app.prediction.ranking import rank_signals_v2

    settings = settings or get_settings()
    result: dict[int, Prediction | None] = {}
    for match_id, predictions in predictions_by_match.items():
        included, _ = rank_signals_v2(
            predictions,
            settings,
            edge_reliability_curve=edge_reliability_curve,
            odds_reliability_curve=odds_reliability_curve,
        )
        best = None
        for ranked in included:
            flags = compute_anomaly_flags(ranked.prediction, predictions, settings)
            if is_strong_signal(flags):
                best = ranked.prediction
                break
        result[match_id] = best
    return result
