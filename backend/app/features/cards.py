"""Features especificas de mercados de tarjetas.

ESTADO: Fase 2 (no implementado). El brief pide entregar el MVP centrado en
goles (Fase 1: Over/Under y BTTS) antes de anhadir tarjetas. La tabla
match_statistics ya guarda amarillas/rojas por partido para que, al abordar
Fase 2, esta funcion solo tenga que combinar `form.py` (ya calcula
yellow_cards_for/against) con `referee.py`.
"""

from __future__ import annotations


def compute_card_features(*args, **kwargs):  # pragma: no cover - placeholder Fase 2
    raise NotImplementedError("Mercados de tarjetas: pendiente para Fase 2.")
