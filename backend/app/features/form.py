"""Features de forma reciente por equipo (ventanas 3/5/10/temporada)."""

from __future__ import annotations

import pandas as pd

from backend.app.features.base import WINDOWS, rolling_prior_mean, sample_size_prior

# "xg_for"/"xg_against" quitados a proposito (pedido del usuario): ninguna
# fuente de datos los rellena nunca (RawMatchRecord no tiene campo de xG),
# asi que esas columnas estaban siempre vacias en las 5 ligas -- puro
# ruido en los logs de entrenamiento (sklearn avisando de que no podia
# imputarlas) sin aportar nada al modelo. Si algun dia se conecta una
# fuente real de xG, se reanhaden aqui.
FORM_STATS = ["goals_for", "goals_against", "shots_for", "shots_against",
              "shots_on_target_for", "shots_on_target_against", "corners_for",
              "corners_against",
              "yellow_cards_for", "yellow_cards_against",
              "red_cards_for", "red_cards_against", "fouls_for", "fouls_against"]


def compute_form_features(team_long: pd.DataFrame) -> pd.DataFrame:
    """Anhade columnas `{stat}_avg_last{window}` y `{stat}_avg_season` por fila.

    Opera sobre el dataframe largo (una fila = un equipo en un partido),
    agrupado por team_id y ordenado por fecha, para que `rolling_prior_mean`
    solo vea el pasado de ESE equipo.
    """
    out = team_long.copy()
    grouped = out.groupby("team_id", group_keys=False)

    for stat in FORM_STATS:
        if stat not in out.columns:
            continue
        for window in WINDOWS:
            out[f"{stat}_avg_last{window}"] = grouped[stat].apply(
                lambda s, w=window: rolling_prior_mean(s, w)
            )
        out[f"{stat}_avg_season"] = grouped[stat].apply(lambda s: rolling_prior_mean(s, None))
        out[f"{stat}_n_prior"] = grouped[stat].apply(lambda s: sample_size_prior(s, None))

    return out
