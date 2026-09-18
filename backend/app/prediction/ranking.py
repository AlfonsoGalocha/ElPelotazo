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

from dataclasses import dataclass
from enum import Enum

from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.modeling import Prediction


class ExclusionReason(str, Enum):
    NO_MARKET = "sin_mercado"  # no hay market_probability/market_odds en absoluto
    INVALID_ODDS = "cuota_invalida"  # cuota <= 1.0 o por debajo del minimo configurado
    ODDS_TOO_HIGH = "cuota_demasiado_alta"  # cuota por encima del maximo configurado (ver max_signal_odds)
    INVALID_EDGE = "edge_invalido"  # edge ausente o por debajo del minimo configurado
    INSUFFICIENT_BOOKMAKERS = "pocas_casas"  # menos casas que el minimo configurado
    LOW_DATA_QUALITY = "calidad_datos_baja"  # data_quality por debajo del minimo configurado


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
    return None


def signal_score(prediction: Prediction) -> float:
    """Puntuacion transparente para una senhal QUE YA PASO el filtro duro
    (siempre tiene mercado/cuota/edge validos en este punto).

        score = model_probability^2 * edge * confidence * data_quality

    - `model_probability^2` (no lineal): sin elevarlo al cuadrado, una
      jugada mediocre con mucho edge en puntos porcentuales (p.ej. 55% a
      cuota 3.0, edge~22pp) puede puntuar por encima de una jugada solida
      de alta probabilidad (p.ej. 80% a cuota 1.35, edge~10pp) solo por el
      tamanho bruto del edge — justo lo contrario de "el modelo alto junto
      a la cuota alta" que se busca. El cuadrado castiga mas la
      probabilidad baja.
    - `edge`: ya filtrado a >= 0 por el quality gate; una cuota 1.02 con
      edge ~0 puntua cerca de cero aunque la probabilidad sea altisima
      (98%): 0.98^2 * ~0 = ~0.
    - `confidence`: ya incorpora `market_coverage` (cuantas casas
      respaldan la cuota, ver confidence.py) ademas de calibracion,
      tamanho de muestra y acuerdo entre modelos.
    - `data_quality`: informacion disponible sobre los propios equipos.
    """
    return (
        (prediction.model_probability**2)
        * max(prediction.edge or 0.0, 0.0)
        * prediction.confidence
        * prediction.data_quality
    )


def rank_signals(
    predictions: list[Prediction], settings: Settings | None = None
) -> tuple[list[RankedSignal], list[ExcludedSignal]]:
    """Aplica el filtro de calidad y ordena las que pasan por `signal_score`
    descendente. Devuelve tambien las excluidas con su motivo, para
    depuracion (seccion 13: nunca "desaparecen" en silencio)."""
    settings = settings or get_settings()
    included: list[RankedSignal] = []
    excluded: list[ExcludedSignal] = []

    for prediction in predictions:
        reason = evaluate_quality_gate(prediction, settings)
        if reason is not None:
            excluded.append(ExcludedSignal(prediction=prediction, reason=reason))
            continue
        included.append(RankedSignal(prediction=prediction, score=signal_score(prediction)))

    included.sort(key=lambda s: s.score, reverse=True)
    return included, excluded
