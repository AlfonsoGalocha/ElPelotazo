"""Diagnostico puntual: la jornada "atascada" de una liga (round N desde
hace tiempo, ver debug_round_coverage.py) no avanza ni corriendo
`update-fixtures` con API-Football activado (confirmado via
GET /config/data-sources). `ApiFootballMatchProvider` deberia traer el
marcador real de esos partidos ya jugados (ver su docstring en cli.py),
asi que si siguen 'scheduled' en la BD, hay dos posibilidades:

1. La API tampoco trae resultado real para ellos todavia (lag de la
   propia API, no de nuestro codigo).
2. La API SI lo trae, pero el partido "nuevo" (finished) no se absorbe
   contra la fila YA EXISTENTE (scheduled) -- se crea una fila NUEVA
   huerfana en vez de actualizar la vieja, normalmente porque
   `resolve_team_id` resuelve el nombre de equipo de API-Football a un
   `team_id` DISTINTO del que ya tiene la fila existente (mismo tipo de
   bug que el de equipos fantasma ya visto antes en este proyecto).

Este script, para los partidos 'scheduled' de la jornada atascada:
- Imprime su provider/provider_id/equipos/fecha tal como estan en la BD.
- Pide a ApiFootballMatchProvider los partidos reales de esa ventana de
  fechas y busca, por FECHA (sin pasar por resolve_team_id), si existe
  algun registro de la API con marcador real para ese mismo rango -- y
  compara los nombres de equipo a ojo.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_stuck_round.py premier_league
"""

from __future__ import annotations

import datetime as dt
import sys

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.ingestion.api_football.fixtures_provider import ApiFootballMatchProvider
from backend.app.services.round_service import get_current_round
from backend.app.utils.dates import season_label as season_label_from_date


def main(competition_code: str) -> None:
    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        if competition is None:
            print(f"{competition_code}: no existe en la BD.")
            return

        round_info = get_current_round(db, competition_code)
        if round_info is None:
            print(f"{competition_code}: get_current_round() devolvio None, nada que comprobar.")
            return

        # round_info.match_ids ya viene filtrado a kickoff futuro (por eso
        # sale vacio en la jornada atascada) -- aqui hace falta la jornada
        # COMPLETA, pasada incluida, para ver que hay realmente atascado.
        stuck_matches = (
            db.query(Match)
            .filter(
                Match.competition_id == competition.id,
                Match.season_id == round_info.season_id,
                Match.matchday == round_info.round,
            )
            .order_by(Match.kickoff_utc)
            .all()
        )
        print(
            f"=== {competition_code}: jornada {round_info.round} "
            f"(temporada {round_info.season_id}) ==="
        )
        for m in stuck_matches:
            print(
                f"  match_id={m.id} status={m.status} provider={m.provider!r} "
                f"provider_id={m.provider_id!r} kickoff={m.kickoff_utc} "
                f"home={m.home_team.canonical_name!r} away={m.away_team.canonical_name!r}"
            )

        if not stuck_matches:
            print("  (ningun partido con ese matchday -- raro, revisa el numero de jornada)")
            return

        date_from = min(m.kickoff_utc for m in stuck_matches).date() - dt.timedelta(days=2)
        date_to = max(m.kickoff_utc for m in stuck_matches).date() + dt.timedelta(days=2)

    print(
        f"\n=== Lo que devuelve ApiFootballMatchProvider para esa ventana "
        f"({date_from} a {date_to}) ==="
    )
    provider = ApiFootballMatchProvider()
    if not provider.is_available():
        print(
            "  ApiFootballMatchProvider.is_available() = False -- revisa "
            "API_FOOTBALL_ENABLED/KEY."
        )
        return

    # Misma temporada que usa `update-fixtures` de verdad (cli.py): la de
    # HOY, no la de `date_from` -- evitar calcularla a partir de una fecha
    # pasada que pudiera caer justo al otro lado del corte de temporada
    # (1 de julio, ver utils/dates.py).
    season_label = season_label_from_date(dt.date.today())
    records = provider.fetch_matches(competition_code, season_label)
    relevant = [
        r
        for r in records
        if date_from <= dt.datetime.fromisoformat(r.date).replace(tzinfo=None).date() <= date_to
    ]
    if not relevant:
        print("  NINGUN registro de la API cae en esa ventana de fechas -- la API tampoco tiene "
              "(todavia) estos partidos con esa fecha exacta.")
        return
    for r in relevant:
        has_goals = r.home_goals is not None and r.away_goals is not None
        score = f"SI ({r.home_goals}-{r.away_goals})" if has_goals else "NO"
        print(
            f"  provider_id={r.provider_id!r} fecha={r.date} home={r.home_team_raw!r} "
            f"away={r.away_team_raw!r} goles={score}"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python scripts/debug_stuck_round.py <competition_code>")
        sys.exit(1)
    main(sys.argv[1])
