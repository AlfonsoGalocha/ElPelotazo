"""Ranking de senhales, en dos fases separadas a proposito (seccion 7/8 de
la revision de arquitectura):

1. FILTRO DURO (quality gate): una prediccion sin mercado valido, con cuota
   invalida, o sin evidencia de mercado suficiente NO ENTRA en el ranking,
   punto. Los umbrales son configurables (`Settings`, nunca hardcodeados
   en un componente de React) y deliberadamente permisivos por defecto: no
   se hardcodea "cuota < 1.50 => fuera" sin estudiar su efecto; el filtro
   solo exige que los DATOS sean validos, no que la senhal sea "buena".
2. SCORING (soft ranking): entre las que pasan el filtro, se puntuan
   combinando edge, probabilidad, confianza y calidad de datos — nunca
   solo `model_probability` ni solo `edge` en crudo.

Cada exclusion se puede explicar (`ExclusionReason`), para poder responder
"por que esta senhal no aparece" sin adivinar (seccion 13).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.market_quality import market_quality_tier


class ExclusionReason(str, Enum):
    NO_MARKET = "sin_mercado"  # no hay market_probability/market_odds en absoluto
    INVALID_ODDS = "cuota_invalida"  # cuota <= 1.0 o por debajo del minimo configurado
    ODDS_TOO_HIGH = (
        "cuota_demasiado_alta"  # cuota por encima del maximo configurado (ver max_signal_odds)
    )
    INVALID_EDGE = "edge_invalido"  # edge ausente o por debajo del minimo configurado
    INSUFFICIENT_BOOKMAKERS = "pocas_casas"  # menos casas que el minimo configurado
    LOW_DATA_QUALITY = "calidad_datos_baja"  # data_quality por debajo del minimo configurado
    STALE_ODDS = (
        "cuota_desactualizada"  # mas antigua que max_odds_age_minutes (desactivado por defecto)
    )


@dataclass
class RankedSignal:
    prediction: Prediction
    score: float


@dataclass
class ExcludedSignal:
    prediction: Prediction
    reason: ExclusionReason


def evaluate_quality_gate(
    prediction: Prediction, settings: Settings | None = None
) -> ExclusionReason | None:
    """Devuelve la razon de exclusion si la prediccion NO deberia entrar al
    ranking de senhales, o `None` si pasa el filtro."""
    settings = settings or get_settings()

    if prediction.market_odds is None or prediction.market_probability is None:
        return ExclusionReason.NO_MARKET
    if prediction.market_odds <= 1.0 or prediction.market_odds < settings.min_signal_odds:
        return ExclusionReason.INVALID_ODDS
    if settings.max_signal_odds is not None and prediction.market_odds > settings.max_signal_odds:
        # Un "edge" grande en un resultado muy improbable (p.ej. modelo ~12%
        # / cuota justa 8, mercado ofrece cuota 15) es matematicamente un
        # edge real, pero no es una PREDICCION util para destacar: sigue
        # siendo mas probable que falle que que acierte. Se descarta ANTES
        # del scoring (no basta con que el cuadrado de la probabilidad lo
        # penalice en el ranking): si un dia hay pocas senhales candidatas,
        # un tiro muy largo como este podria colarse igualmente en el top-N
        # por pura falta de competencia, aunque su score absoluto sea bajo.
        return ExclusionReason.ODDS_TOO_HIGH
    if prediction.edge is None or prediction.edge < settings.min_edge_pp:
        return ExclusionReason.INVALID_EDGE
    if (prediction.bookmakers_used or 0) < settings.min_bookmakers:
        return ExclusionReason.INSUFFICIENT_BOOKMAKERS
    if prediction.data_quality < settings.min_data_quality:
        return ExclusionReason.LOW_DATA_QUALITY
    if settings.max_odds_age_minutes is not None:
        # `created_at` es cuando se genero ESTA prediccion, capturando el
        # `market_probability`/`market_odds` vigentes en ese momento (ver
        # services/prediction_service.py) -- es la marca de frescura real
        # de la cuota usada, no una fecha inventada.
        age_minutes = (dt.datetime.utcnow() - prediction.created_at).total_seconds() / 60.0
        if age_minutes > settings.max_odds_age_minutes:
            return ExclusionReason.STALE_ODDS
    return None


def odds_age_minutes(prediction: Prediction) -> float:
    """Antigueedad en minutos de la cuota usada por esta prediccion (ver
    `created_at` en evaluate_quality_gate). Siempre visible en la UI
    (seccion 12: "cuotas actualizadas hace X minutos"), independientemente
    de si `max_odds_age_minutes` esta activado para excluir senhales."""
    return (dt.datetime.utcnow() - prediction.created_at).total_seconds() / 60.0


# Penaliza la "MEJOR PREDICCION"/"MEJOR SENHAL" cuando la cuota de consenso
# esta respaldada por pocas casas o con mucha dispersion entre ellas (ver
# market_quality.py), ADEMAS de lo que ya aporta `confidence` (que solo mira
# cuantas casas hay, no si estan de acuerdo entre si). Documentado aqui, no
# arbitrario: HIGH no penaliza; MEDIUM/LOW SI reducen el score aunque
# probabilidad/edge/confidence sean identicos, porque la evidencia de
# mercado detras de la cuota es mas debil.
MARKET_QUALITY_MULTIPLIER = {"HIGH": 1.0, "MEDIUM": 0.85, "LOW": 0.65}


def signal_score(
    prediction: Prediction,
    settings: Settings | None = None,
    market_track_record: float | None = None,
) -> float:
    """Puntuacion transparente para una senhal QUE YA PASO el filtro duro
    (siempre tiene mercado/cuota/edge validos en este punto). Es la unica
    fuente de "MEJOR PREDICCION" (seccion 1 del brief): nunca se elige por
    mayor probabilidad/edge/cuota en crudo.

        score = model_probability^2 * edge * confidence * data_quality
                * market_quality_multiplier * market_track_record

    Factores y de donde sale cada uno (para poder responder "por que esta
    senhal fue elegida" sin adivinar, seccion 29):

    - `model_probability^2` (no lineal): sin elevarlo al cuadrado, una
      jugada mediocre con mucho edge en puntos porcentuales (p.ej. 55% a
      cuota 3.0, edge~22pp) puede puntuar por encima de una jugada solida
      de alta probabilidad (p.ej. 80% a cuota 1.35, edge~10pp) solo por el
      tamanho bruto del edge — justo lo contrario de "el modelo alto junto
      a la cuota alta" que se busca. El cuadrado castiga mas la
      probabilidad baja. (Se usa `model_probability`, ya renormalizado
      para consistencia entre mercados excluyentes -- ver market_labels.py
      -- y NO `calibrated_probability`: adoptar la probabilidad calibrada
      como la que dirige edge/ranking es una decision que requiere validar
      out-of-sample que calibrar mejora el ranking, no solo el Brier score;
      queda como trabajo futuro documentado en
      docs/architecture_audit.md, no se hace a ciegas aqui.)
    - `edge`: ya filtrado a >= 0 por el quality gate; una cuota 1.02 con
      edge ~0 puntua cerca de cero aunque la probabilidad sea altisima
      (98%): 0.98^2 * ~0 = ~0.
    - `confidence`: ya incorpora `market_coverage` (cuantas casas
      respaldan la cuota, ver confidence.py) ademas de calibracion,
      tamanho de muestra y acuerdo entre modelos.
    - `data_quality`: informacion disponible sobre los propios equipos.
    - `market_quality_multiplier`: HIGH/MEDIUM/LOW (ver market_quality.py)
      segun numero de casas Y DISPERSION entre ellas -- una cuota outlier
      de una sola casa discrepante no debe destacarse como "mejor senhal"
      (seccion 18: anomalia OUTLIER) aunque su edge nominal sea grande.
    - `market_track_record`: opcional, en [0,1], el rendimiento historico
      observado de ESTE tipo de mercado (p.ej. 1 - error de calibracion
      historico de "over_2_5" en backtesting/evaluation_service.py). Por
      defecto 1.0 (neutro): un mercado sin historico suficiente TODAVIA no
      se penaliza por falta de dato, pero un mercado con historico de mala
      calibracion si reduce su score. No esta cableado automaticamente en
      el pipeline de generacion (Fase 4/5, requiere el pipeline de
      settlement) -- el parametro existe para que backtesting/servicios de
      historico puedan pasarlo en cuanto haya datos suficientes.
    """
    settings = settings or get_settings()
    quality_tier = market_quality_tier(
        prediction.bookmakers_used,
        prediction.market_odds_min,
        prediction.market_odds_max,
        prediction.market_odds_median,
        settings,
    )
    quality_multiplier = MARKET_QUALITY_MULTIPLIER[quality_tier]
    track_record = market_track_record if market_track_record is not None else 1.0

    return (
        (prediction.model_probability**2)
        * max(prediction.edge or 0.0, 0.0)
        * prediction.confidence
        * prediction.data_quality
        * quality_multiplier
        * track_record
    )


def rank_signals(
    predictions: list[Prediction],
    settings: Settings | None = None,
    market_track_records: dict[tuple[str, str], float] | None = None,
) -> tuple[list[RankedSignal], list[ExcludedSignal]]:
    """Aplica el filtro de calidad y ordena las que pasan por `signal_score`
    descendente. Devuelve tambien las excluidas con su motivo, para
    depuracion (seccion 13: nunca "desaparecen" en silencio).

    `market_track_records`: opcional, {(competition_code, market): valor en
    [0,1]} -- ver services/evaluation_service.py::market_track_records(),
    que calcula el valor real a partir de predicciones ya liquidadas. Esta
    funcion sigue siendo PURA (sin acceso a BD): el llamador calcula el
    dict una vez por request y lo pasa aqui. Un mercado/competicion ausente
    del dict (historico insuficiente, o `None` cuando no se calculo en
    absoluto) usa el valor neutro por defecto de `signal_score` (1.0),
    nunca un numero inventado.
    """
    settings = settings or get_settings()
    included: list[RankedSignal] = []
    excluded: list[ExcludedSignal] = []

    for prediction in predictions:
        reason = evaluate_quality_gate(prediction, settings)
        if reason is not None:
            excluded.append(ExcludedSignal(prediction=prediction, reason=reason))
            continue
        track_record = None
        if market_track_records and prediction.match is not None:
            key = (prediction.match.competition.code, prediction.market)
            track_record = market_track_records.get(key)
        score = signal_score(prediction, market_track_record=track_record)
        included.append(RankedSignal(prediction=prediction, score=score))

    included.sort(key=lambda s: s.score, reverse=True)
    return included, excluded


# ---------------------------------------------------------------------------
# signal_score v2 (revision "motor profesional 2.0", 2026-11): formula
# ALTERNATIVA a la de arriba, pensada para no premiar un edge enorme cuando
# la propia probabilidad del modelo esta poco respaldada. NUNCA sustituye a
# `signal_score`/`rank_signals` en produccion por si sola (eso seria
# "aplicar una mezcla sin validar", justo lo que el usuario pidio evitar) --
# es la candidata que `scripts/compare_ranking_strategies.py` (ver ahi)
# backtestea contra la v1 sobre predicciones YA LIQUIDADAS, con ROI/yield/
# win rate reales. Si demuestra mejores resultados ajustados al riesgo de
# forma consistente, esa evidencia es la que justificaria cambiar el
# default -- nunca se asume de entrada.
#
# Diferencias deliberadas frente a v1:
#   1. Usa SIEMPRE `model_probability` (la cruda), igual que v1 -- NUNCA
#      `calibrated_probability`. Esto parecia lo contrario de lo pedido en
#      un primer diseno ("preferir la calibrada cuando exista"), pero se
#      corrigio con evidencia real: `football-edge ensemble-calibration-report`
#      corrido contra LaLiga para home_win/draw/away_win/double_chance_1x
#      (los 4 mercados centrales del caso que motivo esta revision) muestra
#      que TANTO isotonic COMO Platt scaling empeoran Brier/log-loss/ECE
#      frente a la cruda en los 4 mercados, de forma consistente -- no es
#      ruido de un mercado aislado. Preferir una probabilidad que
#      demostradamente calibra peor seria exactamente la "mezcla sin
#      validar" que el usuario pidio evitar. Si en el futuro un mercado o
#      liga distinta demuestra lo contrario con el mismo comando, esto
#      deberia revisarse CON ESA EVIDENCIA, nunca a priori.
#   2. Usa `risk_adjusted_expected_value` (EV con el extremo conservador del
#      intervalo de incertidumbre, ver prediction/edge.py y
#      prediction/uncertainty.py) en vez de `edge` en bruto: mide
#      rentabilidad esperada, no solo diferencia de probabilidad, y ya
#      incorpora la incertidumbre en el propio numero.
#   3. Anhade un factor CONTINUO de incertidumbre (nunca solo el corte
#      binario de `prediction/anomaly.py::HIGH_UNCERTAINTY`): un intervalo
#      mas ancho reduce el score de forma gradual, no solo en el extremo.
#      Escala elegida (0.70 de penalizacion maxima a ancho=1.0, suelo en
#      0.30) para que incluso el peor caso observado nunca colapse el score
#      a practicamente cero (eso equivaldria a una exclusion dura, que ya
#      cubre la flag binaria) -- es una preferencia suave, no un filtro.
UNCERTAINTY_PENALTY_SLOPE = 0.70  # cuanto cae el factor por cada punto de ancho de intervalo
UNCERTAINTY_PENALTY_FLOOR = 0.30  # el factor nunca baja de aqui (ancho=1.0 => factor=0.30)


def uncertainty_factor(uncertainty_low: float | None, uncertainty_high: float | None) -> float:
    """1.0 sin informacion de incertidumbre (nunca penaliza por falta de
    dato -- el filtro duro/la flag binaria ya cubren ese caso aparte)."""
    if uncertainty_low is None or uncertainty_high is None:
        return 1.0
    width = max(0.0, uncertainty_high - uncertainty_low)
    return max(UNCERTAINTY_PENALTY_FLOOR, 1.0 - UNCERTAINTY_PENALTY_SLOPE * width)


def signal_score_v2(
    prediction: Prediction,
    settings: Settings | None = None,
    market_track_record: float | None = None,
) -> float:
    """score_v2 = model_probability^2 * max(EV_ajustado_al_riesgo, 0) * confidence
                  * data_quality * market_quality_multiplier * market_track_record
                  * factor_incertidumbre

    Usa `model_probability` (CRUDA), nunca `calibrated_probability` --
    ver el bloque de comentarios justo arriba de este modulo para la
    evidencia real (isotonic y Platt empeoran Brier/log-loss/ECE en los 4
    mercados centrales de LaLiga, medido con
    `ensemble_calibration_report`).
    """
    settings = settings or get_settings()
    quality_tier = market_quality_tier(
        prediction.bookmakers_used,
        prediction.market_odds_min,
        prediction.market_odds_max,
        prediction.market_odds_median,
        settings,
    )
    quality_multiplier = MARKET_QUALITY_MULTIPLIER[quality_tier]
    track_record = market_track_record if market_track_record is not None else 1.0
    risk_adjusted_ev = max(prediction.risk_adjusted_expected_value or 0.0, 0.0)

    return (
        (prediction.model_probability**2)
        * risk_adjusted_ev
        * prediction.confidence
        * prediction.data_quality
        * quality_multiplier
        * track_record
        * uncertainty_factor(prediction.uncertainty_low, prediction.uncertainty_high)
    )


def rank_signals_v2(
    predictions: list[Prediction],
    settings: Settings | None = None,
    market_track_records: dict[tuple[str, str], float] | None = None,
) -> tuple[list[RankedSignal], list[ExcludedSignal]]:
    """Igual que `rank_signals`, pero puntuando con `signal_score_v2`. Usa
    el MISMO filtro duro (`evaluate_quality_gate`) -- la diferencia es solo
    el ORDEN entre las que ya pasaron, nunca cuales pasan."""
    settings = settings or get_settings()
    included: list[RankedSignal] = []
    excluded: list[ExcludedSignal] = []

    for prediction in predictions:
        reason = evaluate_quality_gate(prediction, settings)
        if reason is not None:
            excluded.append(ExcludedSignal(prediction=prediction, reason=reason))
            continue
        track_record = None
        if market_track_records and prediction.match is not None:
            key = (prediction.match.competition.code, prediction.market)
            track_record = market_track_records.get(key)
        score = signal_score_v2(prediction, market_track_record=track_record)
        included.append(RankedSignal(prediction=prediction, score=score))

    included.sort(key=lambda s: s.score, reverse=True)
    return included, excluded
