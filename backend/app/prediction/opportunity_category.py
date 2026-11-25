"""Categorias A+/A/B/C/D de oportunidad (Fase D de la revision v2.8).

Gap real: no existia ninguna categorizacion (ni en backend ni en
frontend) que resumiera, de un vistazo, cuan solida es una prediccion --
solo flags sueltas (`prediction/anomaly.py`) y el filtro binario
pasa/no-pasa de "Mejores oportunidades" (`evaluate_quality_gate_v3`).

Regla DETERMINISTA basada en campos que ya existen (nunca un score nuevo
con pesos inventados -- misma decision de arquitectura ya tomada y
validada en v3, ver `prediction/ranking.py`): la categoria es una funcion
pura de (a) si pasa el filtro general v1/v2, (b) si pasa v3 ("Mejores
oportunidades"), (c) la procedencia real de la fiabilidad historica
(`evaluation_service.py::TrackRecordEstimate.source_level`) y (d) el
nivel de contradiccion graduado (Fase C, `anomaly.classify_contradiction`)
mas las flags binarias existentes (`anomaly.compute_anomaly_flags`).

Jerarquia estricta D < C < B < A < A+ (cada categoria es un subconjunto
mas exigente de la anterior):

- D: no pasa el filtro general de sanidad (v1/v2, `evaluate_quality_gate`)
  -- ni siquiera es una senhal valida --, o contradiccion FUERTE, o
  CONTRADICCION/ALTA_INCERTIDUMBRE en `anomaly_flags` (seccion 2/6 del
  brief: nunca mostrar esto como recomendacion).
- C: pasa el filtro general pero no "Mejores oportunidades" (v3) --
  senhal valida pero sin el respaldo que v3 exige (calibracion,
  incertidumbre, fiabilidad historica...) --, o contradiccion MODERADA
  o EVIDENCIA_INSUFICIENTE (evidencia real demasiado debil para confiar
  del todo, aunque v3 la deje pasar).
- B: pasa v3 pero con fiabilidad historica respaldada por shrinkage/tier/
  global (nunca el (competicion, mercado) exacto), o contradiccion LEVE.
- A: pasa v3, fiabilidad historica del (competicion, mercado) EXACTO,
  contradiccion SIN_CONTRADICCION, y ninguna flag de anomalia.
- A+: todo lo de A, ademas con intervalo de incertidumbre estrecho (ver
  `A_PLUS_MAX_UNCERTAINTY_WIDTH`) y una muestra de fiabilidad historica
  muy por encima del minimo (ver `A_PLUS_MIN_TRACK_RECORD_OBSERVATIONS`).

NOTA sobre el orden de evaluacion (deliberadamente de D hacia A+, nunca al
reves): cada bloque de abajo comprueba la condicion de SU categoria y, si
no se cumple, cae al siguiente nivel mas estricto -- nunca se evalua A+
antes de confirmar que ya cumple A.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import (
    CONTRADICTION,
    HIGH_UNCERTAINTY,
    ContradictionLevel,
)
from backend.app.prediction.ranking import (
    ExclusionReason,
    evaluate_quality_gate,
    evaluate_quality_gate_v3,
)
from backend.app.services.evaluation_service import (
    MIN_SETTLED_FOR_TRACK_RECORD,
    TrackRecordEstimate,
)

OPPORTUNITY_CATEGORY_VERSION = "v1"

EXACT_SOURCE_LEVEL = "competition_market"

# "Muy por encima del minimo" (seccion de A+ del pedido): el doble del
# umbral que ya define "suficiente" para construir un track record real
# (`MIN_SETTLED_FOR_TRACK_RECORD`, tambien usado como `TRACK_RECORD_
# SHRINKAGE_K`) -- un multiplo principiado de una constante ya validada,
# no un numero nuevo elegido a ojo.
A_PLUS_MIN_TRACK_RECORD_OBSERVATIONS = MIN_SETTLED_FOR_TRACK_RECORD * 2

# Ancho de intervalo de incertidumbre por debajo del cual se considera
# "estrecho" para A+. Mitad de `HIGH_UNCERTAINTY_WIDTH_THRESHOLD` (0.60,
# `prediction/anomaly.py`) -- que a su vez se eligio (ver su propio
# comentario) cerca del p90 medido sobre un dataset sintetico realista
# (p50=0.32, p75=0.43, p90=0.59). Por debajo de 0.30 cae por debajo de la
# MEDIANA real medida (~0.32): exigir estar por debajo de la mediana para
# la categoria mas exigente es un criterio principiado ("mas estrecho que
# la prediccion tipica"), reutilizando la unica medicion real disponible
# en el codigo en vez de inventar un numero nuevo sin medir nada.
A_PLUS_MAX_UNCERTAINTY_WIDTH = 0.30


class OpportunityCategory(str, Enum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"


def opportunity_category(
    prediction: Prediction,
    contradiction_level: ContradictionLevel,
    reliability: TrackRecordEstimate | None,
    anomaly_flags: list[str],
    settings: Settings | None = None,
    now: dt.datetime | None = None,
) -> OpportunityCategory:
    """Categoria determinista de `prediction` (Fase D). `reliability`:
    `None` cuando no hay fiabilidad historica real para su (competicion,
    mercado) -- nunca se inventa (mismo contrato que
    `evaluation_service.py::market_track_records_with_provenance`).
    `anomaly_flags`: salida de `anomaly.compute_anomaly_flags` para esta
    prediccion en el contexto de su partido."""
    settings = settings or get_settings()

    if evaluate_quality_gate(prediction, settings, now=now) is not None:
        return OpportunityCategory.D
    if contradiction_level == ContradictionLevel.FUERTE:
        return OpportunityCategory.D
    if CONTRADICTION in anomaly_flags or HIGH_UNCERTAINTY in anomaly_flags:
        return OpportunityCategory.D

    market_track_record = reliability.value if reliability is not None else None
    v3_exclusion = evaluate_quality_gate_v3(
        prediction, settings, market_track_record=market_track_record, now=now
    )
    if v3_exclusion is not None:
        return OpportunityCategory.C
    if contradiction_level in (ContradictionLevel.MODERADA, ContradictionLevel.EVIDENCIA_INSUFICIENTE):
        return OpportunityCategory.C

    # A partir de aqui, `evaluate_quality_gate_v3` ya exigio que
    # `market_track_record`/`reliability` no fueran `None` (ver
    # `ExclusionReason.INSUFFICIENT_TRACK_RECORD`).
    assert reliability is not None

    if reliability.source_level != EXACT_SOURCE_LEVEL:
        return OpportunityCategory.B
    if contradiction_level == ContradictionLevel.LEVE:
        return OpportunityCategory.B
    # Llegados aqui, `contradiction_level` solo puede ser SIN_CONTRADICCION
    # (FUERTE/MODERADA/EVIDENCIA_INSUFICIENTE ya devolvieron D/C arriba,
    # LEVE ya devolvio B justo arriba) -- lo unico que falta para A es que
    # no haya ninguna flag de anomalia (OUTLIER/SENAL_BAJA_FIABILIDAD/
    # HIGH_PROBABILITY_LOW_VALUE; CONTRADICCION/ALTA_INCERTIDUMBRE ya
    # devolvieron D al principio).
    if anomaly_flags:
        return OpportunityCategory.B

    uncertainty_width = None
    if prediction.uncertainty_low is not None and prediction.uncertainty_high is not None:
        uncertainty_width = prediction.uncertainty_high - prediction.uncertainty_low

    if (
        uncertainty_width is not None
        and uncertainty_width < A_PLUS_MAX_UNCERTAINTY_WIDTH
        and reliability.n_observations >= A_PLUS_MIN_TRACK_RECORD_OBSERVATIONS
    ):
        return OpportunityCategory.A_PLUS

    return OpportunityCategory.A
