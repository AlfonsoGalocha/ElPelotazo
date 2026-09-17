"""Features de arbitro (tarjetas/faltas por partido).

ESTADO: preparado para Fase 2 (mercados de tarjetas). No se usa en los
modelos de goles del MVP, pero se deja la funcion lista porque el modelo de
datos (Referee, Match.referee_id) ya existe desde el dia 1.
"""

from __future__ import annotations

import pandas as pd

from backend.app.features.base import rolling_prior_mean


def compute_referee_card_rate(matches_with_referee: pd.DataFrame, window: int | None = 10) -> pd.DataFrame:
    """Media de tarjetas (amarillas+rojas) por partido arbitrado, previa al partido."""
    out = matches_with_referee.copy()
    out = out.sort_values(["referee_id", "date"])
    out["total_cards"] = (
        out.get("home_yellow_cards", 0).fillna(0)
        + out.get("away_yellow_cards", 0).fillna(0)
        + out.get("home_red_cards", 0).fillna(0)
        + out.get("away_red_cards", 0).fillna(0)
    )
    out["referee_cards_avg_prior"] = out.groupby("referee_id")["total_cards"].apply(
        lambda s: rolling_prior_mean(s, window)
    )
    return out
