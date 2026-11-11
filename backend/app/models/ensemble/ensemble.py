"""Ensemble de N modelos con pesos APRENDIDOS por validacion (no fijados a
mano). Seccion 11 del brief original: "no fijes esos pesos arbitrariamente".

Para cada mercado se busca, por grid search sobre un set de validacion
separado del train, los pesos w_1..w_n (no negativos, suman 1) que
minimizan el Log Loss de:
    p_ensemble = sum(w_i * p_i for i in modelos)

Generalizado desde la version original de 2 modelos (estadistico + ML) para
poder incorporar mas "vocales" al ensemble (p.ej. un modelo de arboles como
LightGBM, ver models/goals/gradient_boosting.py) sin rediseñar la logica de
aprendizaje de pesos -- solo se le pasa una lista mas larga de
probabilidades candidatas.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from backend.app.models.calibration.metrics import log_loss_score


def _simplex_grid(n_models: int, grid_step: float) -> Iterator[tuple[float, ...]]:
    """Todas las combinaciones de pesos no negativos que suman 1 sobre una
    rejilla de paso `grid_step` (p.ej. 0.1 -> pesos multiplos de 0.1).
    Crece como combinaciones-con-repeticion: manejable para el numero de
    modelos de este proyecto (2-4) con un paso >= 0.05."""
    steps = int(round(1.0 / grid_step))

    def _rec(remaining_steps: int, remaining_models: int) -> Iterator[tuple[int, ...]]:
        if remaining_models == 1:
            yield (remaining_steps,)
            return
        for first in range(remaining_steps + 1):
            for rest in _rec(remaining_steps - first, remaining_models - 1):
                yield (first,) + rest

    for combo in _rec(steps, n_models):
        yield tuple(c * grid_step for c in combo)


def learn_ensemble_weights(
    probabilities: list[np.ndarray], y_true: np.ndarray, grid_step: float = 0.1
) -> tuple[list[float], float]:
    """Devuelve (lista_de_pesos_en_el_mismo_orden_que_`probabilities`,
    log_loss_en_esos_pesos). Con un unico modelo no hay nada que aprender
    (peso 1.0 trivial)."""
    if len(probabilities) == 1:
        return [1.0], log_loss_score(y_true, probabilities[0])

    best_weights: list[float] | None = None
    best_loss = float("inf")
    for combo in _simplex_grid(len(probabilities), grid_step):
        blended = sum(w * p for w, p in zip(combo, probabilities, strict=True))
        loss = log_loss_score(y_true, blended)
        if loss < best_loss:
            best_weights, best_loss = list(combo), loss
    assert best_weights is not None  # _simplex_grid siempre produce >=1 combinacion
    return best_weights, best_loss


def blend_many(probabilities: list[np.ndarray], weights: list[float]) -> np.ndarray:
    return sum(w * p for w, p in zip(weights, probabilities, strict=True))
