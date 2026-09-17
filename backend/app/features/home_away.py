"""Separa rendimiento global de rendimiento especifico como local/visitante."""

from __future__ import annotations

import pandas as pd

from backend.app.features.base import rolling_prior_mean
from backend.app.features.form import FORM_STATS


def compute_home_away_split_features(team_long: pd.DataFrame, window: int = 10) -> pd.DataFrame:
    """Anhade `{stat}_avg_last{window}_as_home` / `_as_away`.

    Calcula la rolling mean condicionada a is_home dentro del propio grupo de
    equipo, de forma que "rendimiento como local" solo promedia partidos
    jugados en casa (y analogamente para visitante), siempre excluyendo el
    partido actual.
    """
    out = team_long.copy()
    for is_home_flag, suffix in ((True, "as_home"), (False, "as_away")):
        mask = out["is_home"] == is_home_flag
        subset = out.loc[mask].sort_values(["team_id", "date"])
        for stat in FORM_STATS:
            if stat not in out.columns:
                continue
            col = f"{stat}_avg_last{window}_{suffix}"
            values = subset.groupby("team_id", group_keys=False)[stat].apply(
                lambda s: rolling_prior_mean(s, window)
            )
            out.loc[values.index, col] = values
        # Propaga el ultimo valor conocido hacia partidos del "otro lado"
        # (p.ej. usar la ultima forma como local en un partido como visitante
        # no tendria sentido; en su lugar, forward-fill dentro del propio
        # equipo para que la feature nunca quede NaN por simple alternancia).
        for stat in FORM_STATS:
            if stat not in out.columns:
                continue
            col = f"{stat}_avg_last{window}_{suffix}"
            if col in out.columns:
                out[col] = out.groupby("team_id")[col].ffill()
    return out
