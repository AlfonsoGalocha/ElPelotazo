"""Diagnostico puntual: por que `football-edge update` no marca 'finished'
los partidos de la jornada atascada en 4 de las 5 ligas, pese a correr sin
error (ver debug_round_coverage.py).

`_upsert_match` (data_service.py) SI pone `status="finished"` cuando el
registro que llega trae `home_goals`/`away_goals` -- la pregunta real es
si la fuente historica (`ClubFootballMatchDataProvider`, el CSV mirror de
GitHub que usa `update` por defecto) REALMENTE trae esos goles para los
partidos ya jugados de esa liga, o si el mirror simplemente va por detras
para ella. Este script compara, para una liga, cuantos partidos YA
PASADOS (fecha < ahora) trae la fuente CON goles vs SIN goles -- sin
tocar la BD en absoluto, solo lee la fuente cruda.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_source_results.py premier_league
"""

from __future__ import annotations

import datetime as dt
import sys

from backend.app.ingestion.football_data.history_dataset import ClubFootballMatchDataProvider
from backend.app.utils.dates import season_label as season_label_from_date


def main(competition_code: str) -> None:
    provider = ClubFootballMatchDataProvider()
    today = dt.date.today()
    season = season_label_from_date(today)
    print(f"Fuente: {provider.name} | competicion: {competition_code} | temporada: {season}")

    records = provider.fetch_matches(competition_code, season)
    print(f"Total de registros devueltos por la fuente: {len(records)}")

    now = dt.datetime.utcnow()
    past, past_with_goals, past_without_goals = 0, 0, 0
    future = 0
    latest_with_goals: tuple[str, str, str] | None = None
    earliest_without_goals: tuple[str, str, str] | None = None

    for r in sorted(records, key=lambda r: r.date):
        record_date = dt.datetime.fromisoformat(r.date).replace(tzinfo=None)
        if record_date >= now:
            future += 1
            continue
        past += 1
        has_goals = r.home_goals is not None and r.away_goals is not None
        if has_goals:
            past_with_goals += 1
            latest_with_goals = (r.date, r.home_team_raw, r.away_team_raw)
        else:
            past_without_goals += 1
            if earliest_without_goals is None:
                earliest_without_goals = (r.date, r.home_team_raw, r.away_team_raw)

    print(f"\nPartidos con fecha YA PASADA segun la fuente: {past}")
    print(f"  ...con goles reales (home_goals/away_goals no nulos): {past_with_goals}")
    print(f"  ...SIN goles (la fuente los marca como no jugados todavia): {past_without_goals}")
    print(f"Partidos con fecha futura: {future}")

    if latest_with_goals:
        print(f"\nUltimo partido pasado CON resultado real en la fuente: {latest_with_goals}")
    if earliest_without_goals:
        print(f"Primer partido pasado SIN resultado en la fuente (aqui esta el retraso): "
              f"{earliest_without_goals}")

    if past_without_goals > 0:
        print(
            "\n-> La propia fuente historica (CSV mirror de GitHub) todavia NO tiene el "
            "resultado real de esos partidos -- esto no es un bug de nuestro codigo, es un "
            "retraso de esa fuente para esta liga en concreto. 'update-fixtures' (si "
            "API_FOOTBALL_ENABLED=true) deberia traer el marcador de una fuente mas rapida "
            "para los mismos partidos -- revisa si de verdad esta activado."
        )
    else:
        print(
            "\n-> La fuente SI trae resultado real para todos los partidos pasados: si el "
            "partido sigue 'scheduled' en la BD, el problema esta en el CASAMIENTO "
            "(provider_id/equipo/fecha) entre este registro y la fila ya existente, no en la "
            "fuente. Compara el 'provider_id' de este registro contra el de la fila en "
            "matches via debug_round_coverage.py."
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python scripts/debug_source_results.py <competition_code>")
        sys.exit(1)
    main(sys.argv[1])
