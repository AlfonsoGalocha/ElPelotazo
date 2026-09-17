"""Conversion cuota <-> probabilidad implicita.

implied_probability = 1 / odds

Esta probabilidad SIEMPRE incluye el margen (vig) de la casa de apuestas: no
es la probabilidad "real" segun el mercado, es la probabilidad que se
obtendria si se apostara literalmente a esa cuota. Para comparar contra el
modelo de forma justa hay que quitar el margen primero (ver vig.py).
"""

from __future__ import annotations


def implied_probability(odds: float) -> float:
    if odds <= 1.0:
        raise ValueError(f"Cuota invalida: {odds} (debe ser > 1.0)")
    return 1.0 / odds
