"""Cuantificacion de incertidumbre sobre `model_probability` (hueco
explicito de la auditoria de arquitectura: el sistema ya distingue
confianza del modelo / calidad de datos / calidad de mercado, pero nunca
decia "con que margen" se mueve la propia probabilidad).

Dos fuentes de incertidumbre, combinadas, NUNCA una sola:

1. Tamanho de muestra: un intervalo de Wilson (score interval) sobre
   `model_probability` usando un "tamanho de muestra efectivo" (`n_eff`,
   el promedio de partidos previos conocidos por equipo que ya alimenta
   `sample_size_score` en prediction/predictor.py). Wilson es la eleccion
   estandar para un intervalo de proporcion binomial que se comporta bien
   con `n` pequenho (a diferencia del intervalo normal/Wald, que puede
   salirse de [0,1]) -- es una aproximacion deliberada: `model_probability`
   no es literalmente una proporcion observada de `n_eff` ensayos
   Bernoulli, pero `n_eff` SI es una medida real de cuanta evidencia
   histórica respalda la estimacion, y Wilson es el metodo mas usado en la
   practica para esta situacion (encuestas con tamanho de muestra
   variable, tasas de conversion con pocas visitas, etc.).
2. Desacuerdo entre submodelos (`model_agreement`, ya calculado en
   predictor.py: `1 - abs(p_estadistico - p_ml)`): si el modelo
   estadistico y el ML discrepan mucho, el intervalo se ensancha mas alla
   de lo que Wilson por si solo sugeriria -- un desacuerdo grande es
   evidencia real de que la estimacion puntual es menos fiable,
   independientemente del tamanho de muestra.

Nunca se inventa un intervalo cuando no hay suficiente informacion: sin
`n_eff` (p.ej. ningun partido previo conocido) se usa el minimo
(`MIN_EFFECTIVE_SAMPLE_SIZE`), que da el intervalo MAS ANCHO posible
(maxima incertidumbre honesta), nunca uno estrecho por defecto.
"""

from __future__ import annotations

import math

# Z para un intervalo del 95% (dos colas) -- el nivel de confianza
# estandar, no un umbral agresivo elegido para "verse bien".
Z_95 = 1.959963984540054

# Con menos partidos previos que esto, tratar la muestra como si tuviera
# exactamente este minimo: evita un intervalo degenerado (n=0 -> division
# por cero) sin fingir mas certeza de la que hay.
MIN_EFFECTIVE_SAMPLE_SIZE = 3.0

# Cuanto ensancha el intervalo el desacuerdo MAXIMO posible entre
# submodelos (model_agreement=0, es decir |p_stat - p_ml| = 1.0): 0.15 =
# hasta +/-7.5 puntos porcentuales adicionales a cada lado. Valor de
# partida razonable (ensanchamiento visible pero no dominante frente al
# termino de Wilson), documentado aqui para poder ajustarlo con datos
# reales de backtesting mas adelante.
MAX_DISAGREEMENT_WIDENING = 0.15


def wilson_interval(p: float, n: float, z: float = Z_95) -> tuple[float, float]:
    """Intervalo de Wilson para una proporcion `p` estimada sobre `n`
    observaciones. Siempre devuelve limites dentro de [0, 1]."""
    n = max(n, 1e-9)
    denom = 1.0 + (z**2) / n
    center = (p + (z**2) / (2 * n)) / denom
    margin = (z * math.sqrt((p * (1 - p) / n) + (z**2) / (4 * n**2))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def compute_uncertainty_interval(
    probability: float,
    effective_sample_size: float | None,
    model_agreement: float | None = None,
) -> tuple[float, float]:
    """`effective_sample_size`: tipicamente el promedio de
    `home_goals_for_n_prior`/`away_goals_for_n_prior` (partidos previos
    conocidos). `model_agreement`: en [0,1], 1.0 = los submodelos
    coinciden exactamente; `None` cuando no hay un segundo modelo con el
    que comparar (no ensancha nada, no penaliza por falta de dato)."""
    n_eff = max(effective_sample_size or 0.0, MIN_EFFECTIVE_SAMPLE_SIZE)
    low, high = wilson_interval(probability, n_eff)

    if model_agreement is not None:
        disagreement = max(0.0, 1.0 - model_agreement)
        widen = disagreement * MAX_DISAGREEMENT_WIDENING
        low = max(0.0, low - widen)
        high = min(1.0, high + widen)

    return low, high
