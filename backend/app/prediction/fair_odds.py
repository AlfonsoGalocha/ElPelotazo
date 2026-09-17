"""Precio justo: la cuota que igualaria la probabilidad estimada por el modelo.

fair_odds = 1 / model_probability

No confundir con la cuota de mercado: fair_odds asume 0% de margen. Si se
apostara exactamente a fair_odds, el valor esperado seria 0 SEGUN el modelo.
"""

from __future__ import annotations

import numpy as np


def fair_odds(model_probability: float | np.ndarray) -> float | np.ndarray:
    # Cota superior tambien acotada: una probabilidad de EXACTAMENTE 100%
    # nunca es honesta (seccion 71: no fabricar certeza), asi que fair_odds
    # nunca puede caer a 1.00 por un redondeo/extrapolacion del modelo.
    probability = np.clip(model_probability, 1e-6, 1.0 - 1e-6)
    return 1.0 / probability
