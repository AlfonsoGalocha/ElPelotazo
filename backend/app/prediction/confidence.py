"""Confianza del modelo (Signal.confidence) — seccion 16 del brief.

NO es "que tan grande es el edge". Es una estimacion de cuanto se puede
confiar en la probabilidad reportada, construida a partir de:

    confidence = w1*calibration_quality + w2*sample_size_score
               + w3*model_agreement + w4*data_quality

- calibration_quality: 1 - calibration_error historico del modelo para ESE
  mercado (de backtesting/calibration.py), normalizado a [0,1]. Si no hay
  historico suficiente, se usa un prior neutro conservador (0.5).
- sample_size_score: partidos previos disponibles para ambos equipos
  (mismo criterio que data_quality.py, pero aqui mide "cuanto hay para
  aprender el patron", no "cuanto hay para ESTA prediccion").
- model_agreement: 1 - |p_estadistico - p_ml| cuando hay ensemble; si solo
  hay un modelo, se usa un valor neutro (0.5) en vez de inventarse acuerdo.
- data_quality: score de prediction/data_quality.py.
- market_coverage: cuantas casas de apuestas respaldan `market_probability`
  (ver market/consensus.py). Una cuota de una UNICA casa es mucho menos
  fiable como "el mercado" que un consenso de 5+ casas independientes; sin
  este componente, confidence no distinguia estos dos casos. Si el mercado
  no tiene cuota (tarjetas/corners, o goles sin odds todavia), se usa un
  valor neutro (0.5): la ausencia de mercado no es, en si misma, un motivo
  para desconfiar del modelo estadistico.

Los pesos estan documentados aqui explicitamente (no ocultos) y son un
punto de partida razonable; se recomienda re-calibrarlos con backtesting
antes de confiar en ellos para producción real.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

WEIGHTS = {
    "calibration_quality": 0.30,
    "sample_size": 0.15,
    "model_agreement": 0.15,
    "data_quality": 0.20,
    "market_coverage": 0.20,
}


def market_coverage_score(bookmakers_used: int | None) -> float:
    """Cuantas casas independientes respaldan el consenso de mercado.

    Escala documentada, no arbitraria: 0/None (sin mercado) no penaliza al
    modelo estadistico; 1 sola casa es la situacion menos fiable con datos
    (podria ser un error de tipeo o una casa poco liquida); 5+ casas de
    acuerdo es la evidencia mas fuerte posible de consenso real.
    """
    if bookmakers_used is None or bookmakers_used <= 0:
        return 0.5
    if bookmakers_used == 1:
        return 0.4
    if bookmakers_used == 2:
        return 0.6
    if bookmakers_used <= 4:
        return 0.8
    return 1.0


@dataclass
class ConfidenceInputs:
    calibration_error: float | None  # p.ej. |empirical - predicted| medio por bucket
    sample_size_score: float
    model_agreement: float | None
    data_quality: float
    bookmakers_used: int | None = None


def confidence_score(inputs: ConfidenceInputs) -> float:
    calibration_quality = (
        float(np.clip(1.0 - inputs.calibration_error, 0.0, 1.0))
        if inputs.calibration_error is not None
        else 0.5
    )
    model_agreement = inputs.model_agreement if inputs.model_agreement is not None else 0.5
    market_coverage = market_coverage_score(inputs.bookmakers_used)

    score = (
        WEIGHTS["calibration_quality"] * calibration_quality
        + WEIGHTS["sample_size"] * inputs.sample_size_score
        + WEIGHTS["model_agreement"] * model_agreement
        + WEIGHTS["data_quality"] * inputs.data_quality
        + WEIGHTS["market_coverage"] * market_coverage
    )
    return float(np.clip(score, 0.0, 1.0))


def signal_tier(confidence: float, data_quality: float) -> str:
    """Etiqueta discreta ("HIGH DATA SUPPORT", etc.) con definicion estadistica
    explicita, no un adjetivo arbitrario (seccion 24 del brief).

    HIGH:   confidence >= 0.70 y data_quality >= 0.70
    MEDIUM: confidence >= 0.50 y data_quality >= 0.50
    LOW:    en cualquier otro caso
    """
    if confidence >= 0.70 and data_quality >= 0.70:
        return "HIGH_DATA_SUPPORT"
    if confidence >= 0.50 and data_quality >= 0.50:
        return "MEDIUM_DATA_SUPPORT"
    return "LOW_DATA_SUPPORT"
