"""Diagnostico puntual: para UN partido concreto, vuelca los VALORES
reales de las features de forma/fuerza que alimentan al modelo -- para
responder "¿por que el modelo esta tan seguro de este resultado?" con
datos, no con teorias (pedido real del usuario, caso real: Getafe-
Atletico Madrid, el clasificador ML daba away_win=6.7% para un equipo
que el mercado ve ganador claro).

Complementa a `debug_match_result_breakdown.py` (que descompone la
probabilidad FINAL en Dixon-Coles vs ML): este script mira los INSUMOS
-- forma reciente (3/5/10 partidos), rating de fuerza tipo Elo, y cuantos
partidos previos hay detras de cada media (`n_prior`: una media de solo
2-3 partidos es mucho mas ruidosa que una de 10+, pero el modelo no
necesariamente lo sabe salvo via `data_quality`/`confidence`).

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_match_features.py <match_id>
"""

from __future__ import annotations

import sys

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.features.goals import build_match_feature_table
from backend.app.services.match_service import load_matches_dataframe

STATS_TO_SHOW = ["goals_for", "goals_against", "shots_for", "shots_on_target_for"]
WINDOWS = ["last3", "last5", "last10", "season"]


def main(match_id: int) -> None:
    with session_scope() as db:
        match = db.get(Match, match_id)
        if match is None:
            print(f"No existe ningun partido con id={match_id}.")
            return
        home, away = match.home_team.canonical_name, match.away_team.canonical_name
        competition = db.get(Competition, match.competition_id)
        print(f"Partido: {home} vs {away} ({competition.code}, id={match_id})\n")

        matches = load_matches_dataframe(db, competition.id)

    table = build_match_feature_table(matches)
    row_df = table[table["match_id"] == match_id]
    if row_df.empty:
        print("Este partido no esta en la tabla de features.")
        return
    row = row_df.iloc[0]

    for side, team_name in (("home", home), ("away", away)):
        print(f"--- {side.upper()} ({team_name}) ---")
        for stat in STATS_TO_SHOW:
            values = []
            for window in WINDOWS:
                col = f"{side}_{stat}_avg_{window}"
                if col not in row:
                    continue
                is_nan = row[col] != row[col]
                values.append(f"{window}=NaN" if is_nan else f"{window}={row[col]:.2f}")
            print(f"  {stat:22s} " + "  ".join(values))
        n_prior_col = f"{side}_goals_for_n_prior"
        if n_prior_col in row:
            print(f"  (partidos previos detras de estas medias: {row[n_prior_col]:.0f})")
        for strength_stat in ("attack_strength", "defense_strength"):
            col = f"{side}_{strength_stat}"
            if col in row:
                print(f"  {strength_stat:22s} {row[col]:.3f}")
        rest_col = f"{side}_rest_days"
        if rest_col in row:
            print(f"  {'rest_days':22s} {row[rest_col]:.0f}")
        print()

    attack_diff = row.get("attack_strength_difference", float("nan"))
    defense_diff = row.get("defense_strength_difference", float("nan"))
    print(f"attack_strength_difference  = {attack_diff:.3f}")
    print(f"defense_strength_difference = {defense_diff:.3f}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Uso: python scripts/debug_match_features.py <match_id>")
        sys.exit(1)
    main(int(sys.argv[1]))
