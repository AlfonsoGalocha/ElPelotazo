"""Ensemble estadistico + ML, con pesos APRENDIDOS por validacion (no fijados
a mano). Seccion 11 del brief: "no fijes esos pesos arbitrariamente".

Para cada mercado se busca, por grid search sobre un set de validacion
separado del train, el peso w in [0,1] que minimiza el Log Loss de:
    p_ensemble = w * p_statistical + (1 - w) * p_ml
"""

from __future__ import annotations

import numpy as np

from backend.app.models.calibration.metrics import log_loss_score


def learn_ensemble_weight(
    p_statistical: np.ndarray, p_ml: np.ndarray, y_true: np.ndarray, grid_step: float = 0.05
) -> tuple[float, float]:
    """Devuelve (mejor_peso_w_para_statistical, log_loss_en_ese_peso)."""
    best_w, best_loss = 0.5, float("inf")
    for w in np.arange(0.0, 1.0 + 1e-9, grid_step):
        blended = w * p_statistical + (1 - w) * p_ml
        loss = log_loss_score(y_true, blended)
        if loss < best_loss:
            best_w, best_loss = float(w), loss
    return best_w, best_loss


def blend(p_statistical: np.ndarray, p_ml: np.ndarray, weight_statistical: float) -> np.ndarray:
    return weight_statistical * p_statistical + (1 - weight_statistical) * p_ml
