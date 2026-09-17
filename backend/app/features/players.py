"""Features a nivel de jugador. Fase 5, no implementado.

Arquitectura prevista (ver seccion 21 del brief): player_id, expected_minutes,
starting_probability, shots/90, shots_on_target/90, xg/90, role, opponent
defensive strength. No se puebla en el MVP: football-data.co.uk no ofrece
datos a nivel de jugador y el modelo de datos (PlayerMatchStatistics) esta
vacio hasta integrar una fuente adecuada (understat u otra).
"""

from __future__ import annotations


def compute_player_features(*args, **kwargs):  # pragma: no cover - placeholder Fase 5
    raise NotImplementedError("Features de jugador: pendiente para Fase 5 (roadmap).")
