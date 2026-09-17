"""Metricas de calidad probabilistica (seccion 12/53 del brief).

Priorizamos SIEMPRE, en este orden: Log Loss, Brier Score, Calibracion.
Accuracy se calcula solo como referencia secundaria (nunca como criterio de
seleccion de modelo para este proyecto).
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    return float(brier_score_loss(y_true, y_prob))


def log_loss_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    y_prob_clipped = np.clip(y_prob, 1e-6, 1 - 1e-6)
    return float(log_loss(y_true, y_prob_clipped, labels=[False, True]))


def reliability_curve(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Puntos (probabilidad media predicha, frecuencia empirica, n) por bucket.

    A diferencia de sklearn.calibration_curve, devuelve tambien el tamanho de
    muestra de cada bucket: un bucket con 3 partidos no dice lo mismo que uno
    con 500 (seccion 26: performance by probability bucket).
    """
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_idx = np.clip(np.digitize(y_prob, bins) - 1, 0, n_bins - 1)
    points = []
    for b in range(n_bins):
        mask = bin_idx == b
        n = int(mask.sum())
        if n == 0:
            continue
        points.append(
            {
                "bucket_low": float(bins[b]),
                "bucket_high": float(bins[b + 1]),
                "predicted_probability": float(np.mean(y_prob[mask])),
                "empirical_frequency": float(np.mean(y_true[mask])),
                "n": n,
            }
        )
    return points


def expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """ECE: media ponderada por tamanho de bucket de |predicho - empirico|."""
    curve = reliability_curve(y_true, y_prob, n_bins)
    total_n = sum(p["n"] for p in curve)
    if total_n == 0:
        return float("nan")
    weighted_error = sum(
        p["n"] * abs(p["predicted_probability"] - p["empirical_frequency"]) for p in curve
    )
    return float(weighted_error / total_n)
