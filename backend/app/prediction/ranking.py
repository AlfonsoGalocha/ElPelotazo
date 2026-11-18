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
from typing import TYPE_CHECKING

import numpy as np
from sklearn.isotonic import IsotonicRegression as _IsotonicRegressionImpl

from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.market_quality import market_quality_tier

if TYPE_CHECKING:
    from sklearn.isotonic import IsotonicRegression


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
    # --- v3 ("Mejores oportunidades", cambio de filosofia del ranking) ---
    ODDS_OUTSIDE_OPPORTUNITY_BAND = (
        "cuota_fuera_de_banda"  # fuera de [min_opportunity_odds, max_opportunity_odds], AMBOS inclusive
    )
    NON_POSITIVE_ESTIMATED_EV = "ev_estimado_no_positivo"  # EV con probabilidad calibrada <= 0
    NON_POSITIVE_CONSERVATIVE_EV = "ev_conservador_no_positivo"  # EV con probabilidad conservadora <= 0
    INSUFFICIENT_TRACK_RECORD = (
        "fiabilidad_historica_insuficiente"  # sin track record real para (competicion, mercado)
    )


@dataclass
class RankedSignal:
    prediction: Prediction
    # `float` para v1/v2 (un score combinado). v3 ("Mejores oportunidades",
    # cambio de filosofia del ranking) usa en cambio una TUPLA -- orden
    # lexicografico por prioridad (probabilidad calibrada, EV conservador,
    # fiabilidad historica, incertidumbre, calidad de datos, edge), nunca
    # un score combinado con pesos inventados. Python ordena tuplas
    # exactamente igual que floats (`sort(key=..., reverse=True)` funciona
    # sin cambios), asi que no hace falta un dataclass nuevo.
    score: float | tuple[float, ...]


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


# Hueco real detectado DESPUES de activar v2 en produccion (ver secciones
# 11/12 de docs/architecture_audit.md): el caso Getafe-Atletico
# (match_id=61, edge +39.6pp) seguia arriba del ranking con v2. Motivo
# real, verificado con datos: `uncertainty_low` mide ruido de MUESTRA
# (Wilson sobre partidos previos conocidos) -- con mucha muestra
# historica, el intervalo es ESTRECHO aunque la estimacion este muy
# alejada del mercado, asi que `risk_adjusted_expected_value` (que usa
# `uncertainty_low`) seguia siendo grande (+0.70 en el caso real). Esa
# incertidumbre nunca mide "cuanto discrepa el modelo de una fuente
# independiente fuerte (el mercado)" -- es un tipo de incertidumbre
# DISTINTO.
#
# Evidencia real que lo confirma (`scripts/debug_edge_vs_hitrate.py`,
# corrido por el usuario sobre 1133 predicciones liquidadas reales): el
# hit rate cae de forma practicamente monotona con el tamanho del edge
# (53.4% en 0-5pp -> 41.4% en 5-10pp -> 31.8% en 10-15pp; ROI real
# NEGATIVO a partir de 5pp, igual o peor con mercado bien respaldado).
# Un edge mas grande, en este sistema, es señal de que el modelo
# probablemente se equivoco, no de mejor oportunidad -- justo el
# diagnostico original del usuario.
#
# PRIMER INTENTO (insuficiente, verificado en vivo contra la BD real del
# usuario tras desplegarlo): un factor MULTIPLICATIVO sobre el score
# final (`observed_hit_rate / baseline_hit_rate`) descontaba el score de
# Getafe un 72% (de 0.2015 a 0.0561) -- pero seguia siendo el #1, porque
# `risk_adjusted_expected_value` (+0.70) nunca se corrigio: seguia
# calculado con `uncertainty_low=0.718`, un numero que la propia
# evidencia (hit rate real ~14% a ese tamanho de edge) dice que es
# irrealmente optimista. Descontar el score despues de calcularlo con un
# EV ya inflado no es suficiente correccion.
#
# CORRECCION REAL: en vez de un factor aparte, la curva se usa para
# poner un TECHO al EV mismo. `uncertainty_low` es una cota conservadora
# sobre el ruido de MUESTRA de este partido; `real_hit_rate` (via la
# curva) es una cota conservadora sobre el sesgo SISTEMATICO del
# ensemble para este TAMANO de edge -- dos fuentes de error
# independientes, el minimo de los dos EV que implican es la estimacion
# conservadora real (nunca un blend arbitrario: es un techo, igual
# principio que ya usa `uncertainty_low` frente a `model_probability`).
#
# SEGUNDA cota real, del mismo tipo (seccion 13 de docs/architecture_audit.md):
# tras corregir el edge grande, el usuario reporto un patron RELACIONADO
# pero distinto -- "victoria local" a cuota 5.50/6.00 (model_probability
# 33.5%/28.3%) seguia destacada como mejor oportunidad: "una cuota 5 o
# una cuota 6 no es una buena prediccion". `scripts/debug_odds_vs_hitrate.py`,
# corrido por el usuario sobre las mismas 1133 liquidaciones reales,
# confirma un "favorite-longshot bias" claro: el modelo SOBRESTIMA su
# propia probabilidad en la cola de cuota alta (cuota 4-6: dice 21.7%,
# acierta 13.9%, ROI -34.3%; cuota 6+: dice 15.8%, acierta 3.6%, ROI
# -77.9%) -- incluso con edge MODESTO, es un problema de la propia
# CUOTA, no solo de discrepancia con el mercado.
#
# IMPORTANTE -- bug real corregido antes de desplegar esto (verificado
# EN VIVO: el caso Getafe match_id=61, model_probability=0.335, cuota
# 5.50, seguia arriba del ranking casi sin penalizar): la PRIMERA
# version ajustaba/consultaba la curva por `model_probability`, pero la
# evidencia de arriba esta medida por CUOTA -- un `model_probability` de
# 33.5% aparece tanto en apuestas bien calibradas (cuota ~3) como mal
# calibradas (cuota ~5.5); agrupar/consultar por probabilidad MEZCLA
# ambos casos y diluye la correccion justo donde mas hace falta. Se usa
# `market_odds` como clave, la MISMA variable que agrupa
# `scripts/debug_odds_vs_hitrate.py`.
#
# Misma correccion, misma disciplina: una segunda curva independiente
# (`fit_odds_reliability_curve`, `market_odds -> hit rate real`) pone
# OTRO techo al EV, y se toma el minimo de TODAS las cotas disponibles
# -- nunca un blend, cualquier evidencia real que diga "este EV es mas
# optimista de lo que deberia" puede bajarlo, ninguna puede subirlo.
#
# ROLLBACK DE EMERGENCIA (seccion 14 de docs/architecture_audit.md,
# verificado EN VIVO contra produccion real): `odds_reliability_curve`
# colapsaba el ranking ENTERO a score=0 -- las 5 cuotas observadas
# tienen ROI real NEGATIVO (vig del mercado), asi que usar "el ROI
# promedio de esa cuota" como techo castiga CUALQUIER senhal a esa
# cuota, sin importar su calidad real (equivale a asumir que el modelo
# nunca puede superar al apostante medio, justo lo contrario de lo que
# un detector de edge intenta medir). `odds_reliability_curve` se deja
# de pasar en produccion (ver seccion 14.3) pero el parametro y el
# mecanismo NO se borran -- siguen aqui, disponibles para
# `backtesting/ranking_comparison.py`, nunca reactivados sin validacion
# walk-forward real.
#
# TERCERA cota, de tipo DISTINTO (seccion 15): en vez de un techo basado
# en ROI/hit-rate AGREGADO de un bucket entero (que hereda el vig y por
# eso colapsa todo), esta recalibra la PROBABILIDAD del modelo misma,
# restringida a la cola donde la seccion 13 encontro sobreconfianza real
# (cuota >= `LONGSHOT_ODDS_THRESHOLD`): `model_probability -> resultado
# real`, ajustada SOLO con liquidaciones de esa cola (nunca con el rango
# completo, que la seccion 11.5 ya mostro que empeora Brier/ECE). Una
# curva de calibracion de probabilidad (acotada en [0,1], sin vig
# mezclado) no hereda el sesgo negativo universal que colapsaba el techo
# de cuota -- pedido explicito del usuario: "quiero que el modelo sea
# realista tambien con las predicciones, no solo [que mida] mucha
# diferencia de edge". NUNCA activada en produccion todavia: solo
# construida y testeada, pendiente de validacion walk-forward real en
# `backtesting/ranking_comparison.py` antes de conectarse.
LONGSHOT_ODDS_THRESHOLD = 4.0  # seccion 13.2: cuota >=4 es donde la sobreconfianza se vuelve severa


def reality_capped_expected_value(
    risk_adjusted_ev: float,
    edge: float | None,
    market_odds: float | None,
    edge_reliability_curve: IsotonicRegression | None,
    odds_reliability_curve: IsotonicRegression | None = None,
    model_probability: float | None = None,
    longshot_calibration_curve: IsotonicRegression | None = None,
) -> float:
    """Nunca deja que el EV ajustado al riesgo supere lo que la evidencia
    empirica (`fit_edge_reliability_curve`/`fit_odds_reliability_curve`/
    `fit_longshot_calibration_curve`, todas ajustadas sobre liquidaciones
    reales) dice que realmente paga una senhal con este edge/esta
    cuota/esta probabilidad -- sin ninguna curva, o sin `market_odds`,
    cada techo concreto queda neutro (nunca penaliza por falta de
    dato)."""
    capped = risk_adjusted_ev
    if edge_reliability_curve is not None and edge is not None and market_odds is not None:
        real_hit_rate = float(edge_reliability_curve.predict([edge * 100.0])[0])
        capped = min(capped, real_hit_rate * market_odds - 1.0)
    if odds_reliability_curve is not None and market_odds is not None:
        real_hit_rate = float(odds_reliability_curve.predict([market_odds])[0])
        capped = min(capped, real_hit_rate * market_odds - 1.0)
    if (
        longshot_calibration_curve is not None
        and model_probability is not None
        and market_odds is not None
        and market_odds >= LONGSHOT_ODDS_THRESHOLD
    ):
        realistic_probability = float(longshot_calibration_curve.predict([model_probability])[0])
        capped = min(capped, realistic_probability * market_odds - 1.0)
    return capped


def fit_edge_reliability_curve_from_observations(
    edge_pp: np.ndarray, outcome: np.ndarray
) -> IsotonicRegression:
    """Ajuste puro (sin BD): el MISMO calculo que
    `services/evaluation_service.py::fit_edge_reliability_curve` hace
    tras consultar la BD -- extraido aqui para que
    `backtesting/ranking_comparison.py` pueda reproducirlo exactamente
    dentro de un walk-forward (ajustando solo con liquidaciones
    anteriores a cada punto evaluado), sin duplicar la logica."""
    model = _IsotonicRegressionImpl(y_min=0.0, y_max=1.0, increasing=False, out_of_bounds="clip")
    model.fit(edge_pp, outcome)
    return model


def fit_odds_reliability_curve_from_observations(
    market_odds: np.ndarray, outcome: np.ndarray
) -> IsotonicRegression:
    """Ajuste puro (sin BD), mismo motivo que
    `fit_edge_reliability_curve_from_observations`. NUNCA activada en
    produccion (seccion 14) -- se mantiene solo para que
    `ranking_comparison.py` pueda seguir comparandola."""
    model = _IsotonicRegressionImpl(y_min=0.0, y_max=1.0, increasing=False, out_of_bounds="clip")
    model.fit(market_odds, outcome)
    return model


def fit_longshot_calibration_curve_from_observations(
    model_probability: np.ndarray, outcome: np.ndarray
) -> IsotonicRegression:
    """Ajuste puro (sin BD), mismo motivo que las dos anteriores --
    `increasing=True` porque es una calibracion de probabilidad real
    (CRECIENTE: mas probabilidad dicha, mas aciertos reales), nunca un
    techo de ROI. El llamador filtra las observaciones a la cola de
    cuota alta (`LONGSHOT_ODDS_THRESHOLD`) ANTES de pasarlas aqui."""
    model = _IsotonicRegressionImpl(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    model.fit(model_probability, outcome)
    return model


def signal_score_v2(
    prediction: Prediction,
    settings: Settings | None = None,
    market_track_record: float | None = None,
    edge_reliability_curve: IsotonicRegression | None = None,
    odds_reliability_curve: IsotonicRegression | None = None,
    longshot_calibration_curve: IsotonicRegression | None = None,
) -> float:
    """score_v2 = model_probability^2 * max(EV_ajustado_al_riesgo_y_a_la_realidad, 0)
                  * confidence * data_quality * market_quality_multiplier
                  * market_track_record * factor_incertidumbre

    Usa `model_probability` (CRUDA) para elevar al cuadrado, nunca
    `calibrated_probability` -- ver el bloque de comentarios justo arriba
    de este modulo para la evidencia real (isotonic y Platt empeoran
    Brier/log-loss/ECE en los 4 mercados centrales de LaLiga, medido con
    `ensemble_calibration_report`). `odds_reliability_curve`/
    `longshot_calibration_curve` son DISTINTOS: no reemplazan
    `model_probability` para elevar al cuadrado, solo ponen un techo
    adicional al EV (ver `reality_capped_expected_value`).
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
    raw_risk_adjusted_ev = prediction.risk_adjusted_expected_value or 0.0
    capped_ev = reality_capped_expected_value(
        raw_risk_adjusted_ev,
        prediction.edge,
        prediction.market_odds,
        edge_reliability_curve,
        odds_reliability_curve,
        prediction.model_probability,
        longshot_calibration_curve,
    )
    risk_adjusted_ev = max(capped_ev, 0.0)

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
    edge_reliability_curve: IsotonicRegression | None = None,
    odds_reliability_curve: IsotonicRegression | None = None,
    longshot_calibration_curve: IsotonicRegression | None = None,
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
        score = signal_score_v2(
            prediction,
            market_track_record=track_record,
            edge_reliability_curve=edge_reliability_curve,
            odds_reliability_curve=odds_reliability_curve,
            longshot_calibration_curve=longshot_calibration_curve,
        )
        included.append(RankedSignal(prediction=prediction, score=score))

    included.sort(key=lambda s: s.score, reverse=True)
    return included, excluded


# ---------------------------------------------------------------------------
# signal ranking v3 ("Mejores oportunidades", cambio de filosofia del
# ranking -- pedido real del usuario, 2026-11): v1/v2 dejaban que un EV o
# un edge grande compensaran una probabilidad baja ("un 44% a cuota 3.50
# por delante de un 72% a cuota 1.70, solo porque su EV ajustado era
# mayor" -- diagnostico real del usuario, confirmado leyendo la formula:
# `model_probability^2` cae mucho mas despacio de lo que crece el EV con
# la cuota). v3 NO es una formula con pesos nuevos: es un ORDEN DE
# PRIORIDAD literal (seccion 5 del pedido), aplicado como sort
# LEXICOGRAFICO -- la probabilidad calibrada decide primero, el EV
# conservador solo rompe empates de probabilidad, etc. "No inventes
# pesos para construir un score" se cumple por construccion: no hay
# ningun score combinado que inventar.
#
# Requisitos DUROS para entrar (seccion 2/4 del pedido), antes de
# ordenar nada:
#   - Cuota de mercado REAL (nunca estimada) dentro de
#     [min_opportunity_odds, max_opportunity_odds], AMBOS inclusive.
#   - EV estimado (con probabilidad CALIBRADA) estrictamente positivo.
#   - EV conservador (con probabilidad conservadora) estrictamente positivo.
#   - Calidad de datos sobre el minimo ya configurado (`min_data_quality`).
#   - Fiabilidad historica (`market_track_record`) REAL y suficiente para
#     este (competicion, mercado) -- a diferencia de v1/v2 (que tratan la
#     ausencia de historico como neutro, 1.0), v3 EXIGE que exista: "la
#     fiabilidad historica del segmento debe ser suficiente" es un
#     requisito de entrada, no solo un factor de orden. Consecuencia
#     esperada y deliberada: en competiciones/mercados nuevos, sin
#     historico liquidado suficiente todavia (ver
#     evaluation_service.py::MIN_SETTLED_FOR_TRACK_RECORD), "Mejores
#     oportunidades" puede quedar vacio -- es el comportamiento CORRECTO
#     pedido explicitamente (seccion 7: "nunca rellenes el ranking con
#     predicciones que incumplan los filtros"), nunca un bug.
#
# Deliberadamente SIN exigir `edge >= min_edge_pp`: el edge es
# "informacion secundaria" (ultimo criterio de la seccion 5), nunca un
# sustituto de las condiciones de arriba -- un EV positivo con una cuota
# bien respaldada ya implica que hay valor real, exigir ademas un edge
# minimo en puntos porcentuales duplicaria el mismo criterio de otra
# forma.


def calibrated_probability_or_fallback(prediction: Prediction) -> float:
    """"Probabilidad calibrada" (seccion 3 del pedido): `calibrated_
    probability` cuando existe (ajustada en el holdout de entrenamiento,
    ver model_service.py); si no (mercado/competicion sin holdout
    suficiente, `MIN_CALIBRATION_MATCHES`), cae a `model_probability` --
    NUNCA se descarta la prediccion solo por falta de calibracion
    (eso excluiria sistematicamente ligas/mercados nuevos), pero tampoco
    se inventa una calibracion que no existe."""
    return prediction.calibrated_probability if prediction.calibrated_probability is not None else prediction.model_probability


def conservative_probability(prediction: Prediction) -> float:
    """"Probabilidad conservadora" (seccion 3): el MINIMO entre la
    probabilidad calibrada (o su fallback) y `uncertainty_low` (cota
    conservadora real sobre el ruido de MUESTRA, Wilson score interval ya
    validado -- ver prediction/uncertainty.py), cuando existe.

    Un minimo, nunca un blend ni una resta arbitraria -- mismo principio
    YA establecido en este modulo (`reality_capped_expected_value`: "el
    minimo de los dos EV que implican es la estimacion conservadora real,
    nunca un blend"). Aqui se aplica a probabilidad en vez de a EV: si la
    calibracion dice 70% pero el intervalo de incertidumbre de este
    partido concreto solo respalda un 55%, la estimacion conservadora
    real es 55%, nunca el 70% optimista. Sin dato de incertidumbre (raro,
    ver uncertainty_low: `None` cuando no se pudo calcular), se usa solo
    la calibrada/fallback -- nunca se inventa un intervalo."""
    calibrated = calibrated_probability_or_fallback(prediction)
    if prediction.uncertainty_low is None:
        return calibrated
    return min(calibrated, prediction.uncertainty_low)


def ev_with_probability(probability: float, market_odds: float | None) -> float | None:
    """EV = probabilidad * cuota - 1 (seccion 4), con la probabilidad que
    decida el llamador (calibrada o conservadora) -- `None` sin cuota
    real, nunca un EV calculado sobre una cuota inventada."""
    if market_odds is None:
        return None
    return probability * market_odds - 1.0


def evaluate_quality_gate_v3(
    prediction: Prediction,
    settings: Settings | None = None,
    market_track_record: float | None = None,
) -> ExclusionReason | None:
    """Filtro duro de "Mejores oportunidades" (v3) -- DELIBERADAMENTE
    INDEPENDIENTE de `evaluate_quality_gate` (v1/v2): reutilizar sus
    comprobaciones de cuota seria incorrecto aqui, porque
    `min_signal_odds`/`max_signal_odds` (1.45/4.0 por defecto) definen
    una banda DISTINTA (el filtro de sanidad general) de
    `min_opportunity_odds`/`max_opportunity_odds` (1.35/1.90): una cuota
    de 1.40 debe PASAR v3 (esta dentro de [1.35, 1.90]) aunque quede por
    debajo de `min_signal_odds` -- mezclar ambas comprobaciones
    rechazaria cuotas validas para esta estrategia por error.
    """
    settings = settings or get_settings()

    if prediction.market_odds is None or prediction.market_probability is None:
        return ExclusionReason.NO_MARKET
    # Ambos limites INCLUSIVOS (seccion 2 del pedido: "Ambos limites son
    # inclusivos") -- por eso `<`/`>` estrictos, nunca `<=`/`>=`.
    if (
        prediction.market_odds < settings.min_opportunity_odds
        or prediction.market_odds > settings.max_opportunity_odds
    ):
        return ExclusionReason.ODDS_OUTSIDE_OPPORTUNITY_BAND
    if (prediction.bookmakers_used or 0) < settings.min_bookmakers:
        return ExclusionReason.INSUFFICIENT_BOOKMAKERS
    if prediction.data_quality < settings.min_data_quality:
        return ExclusionReason.LOW_DATA_QUALITY
    if settings.max_odds_age_minutes is not None:
        age_minutes = (dt.datetime.utcnow() - prediction.created_at).total_seconds() / 60.0
        if age_minutes > settings.max_odds_age_minutes:
            return ExclusionReason.STALE_ODDS

    calibrated = calibrated_probability_or_fallback(prediction)
    ev_estimated = ev_with_probability(calibrated, prediction.market_odds)
    if ev_estimated is None or ev_estimated <= 0.0:
        return ExclusionReason.NON_POSITIVE_ESTIMATED_EV

    conservative = conservative_probability(prediction)
    ev_conservative = ev_with_probability(conservative, prediction.market_odds)
    if ev_conservative is None or ev_conservative <= 0.0:
        return ExclusionReason.NON_POSITIVE_CONSERVATIVE_EV

    if market_track_record is None:
        return ExclusionReason.INSUFFICIENT_TRACK_RECORD

    return None


def ranking_key_v3(prediction: Prediction, market_track_record: float) -> tuple[float, float, float, float, float, float]:
    """Orden de prioridad LITERAL de la seccion 5 del pedido, como tupla
    para sort lexicografico (nunca un score combinado -- ver el bloque de
    comentarios justo arriba de esta seccion):

        1. probabilidad calibrada
        2. EV conservador
        3. fiabilidad historica del mercado/competicion (market_track_record)
        4. incertidumbre (ancho del intervalo; MENOR ancho = mejor, se
           guarda como NEGATIVO para que el sort descendente lo prefiera)
        5. calidad de datos
        6. edge (informacion secundaria, ultimo criterio de desempate)

    `market_track_record` nunca es `None` aqui: el llamador solo invoca
    esto DESPUES de que `evaluate_quality_gate_v3` ya exigio que existiera.
    Ancho de incertidumbre ausente (raro) se trata como el PEOR caso
    posible (1.0) para el orden -- nunca se finge una certeza que no se
    pudo medir, aunque esto no sea un filtro duro (solo afecta el orden).
    """
    calibrated = calibrated_probability_or_fallback(prediction)
    conservative = conservative_probability(prediction)
    ev_conservative = ev_with_probability(conservative, prediction.market_odds) or 0.0
    if prediction.uncertainty_low is not None and prediction.uncertainty_high is not None:
        width = max(0.0, prediction.uncertainty_high - prediction.uncertainty_low)
    else:
        width = 1.0
    return (
        calibrated,
        ev_conservative,
        market_track_record,
        -width,
        prediction.data_quality,
        prediction.edge or 0.0,
    )


def select_one_per_match(ranked: list[RankedSignal]) -> list[RankedSignal]:
    """Seccion 6 del pedido: "evalua todos los mercados disponibles de
    cada partido... evita mostrar multiples apuestas redundantes del
    mismo partido". `ranked` debe venir YA ORDENADO (mejor primero) --
    se queda con la PRIMERA (mejor) aparicion de cada `match_id`,
    descartando el resto SOLO de esta lista de salida (las candidatas
    siguen todas en la BD, nunca se borran -- ver Prediction/
    PredictionResult, esto es puramente una decision de que mostrar)."""
    seen_match_ids: set[int] = set()
    selected: list[RankedSignal] = []
    for signal in ranked:
        match_id = signal.prediction.match_id
        if match_id in seen_match_ids:
            continue
        seen_match_ids.add(match_id)
        selected.append(signal)
    return selected


def rank_signals_v3(
    predictions: list[Prediction],
    settings: Settings | None = None,
    market_track_records: dict[tuple[str, str], float] | None = None,
    one_per_match: bool = True,
) -> tuple[list[RankedSignal], list[ExcludedSignal]]:
    """Filtra con `evaluate_quality_gate_v3` y ordena con `ranking_key_v3`
    -- "Mejores oportunidades" tal como lo pide el cambio de filosofia
    del ranking. `one_per_match=True` (por defecto) aplica
    `select_one_per_match` al final; se puede desactivar para depuracion
    (ver /best/debug) o para el comparador de backtesting, que SI quiere
    ver todas las candidatas liquidadas."""
    settings = settings or get_settings()
    included: list[RankedSignal] = []
    excluded: list[ExcludedSignal] = []

    for prediction in predictions:
        track_record = None
        if market_track_records and prediction.match is not None:
            key = (prediction.match.competition.code, prediction.market)
            track_record = market_track_records.get(key)

        reason = evaluate_quality_gate_v3(prediction, settings, track_record)
        if reason is not None:
            excluded.append(ExcludedSignal(prediction=prediction, reason=reason))
            continue
        key_tuple = ranking_key_v3(prediction, track_record)
        included.append(RankedSignal(prediction=prediction, score=key_tuple))

    included.sort(key=lambda s: s.score, reverse=True)
    if one_per_match:
        included = select_one_per_match(included)
    return included, excluded
