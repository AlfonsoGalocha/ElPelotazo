"""Diagnostico puntual: por que "Partidos destacados" (home) y "Mejores
predicciones" solo muestran una liga.

Esos widgets tiran de /predictions/best, que exige PREDICCIONES CON
MERCADO REAL dentro de la "jornada actual" (ver round_service.py) de cada
liga -- si para 4 de las 5 ligas la cadena se rompe en algun punto
(jornada actual sin partidos, partidos sin cuotas, predicciones sin
mercado...), esas ligas sencillamente no tienen nada que mostrar ahi,
aunque SI tengan partidos programados de verdad.

Este script recorre, para CADA una de las 5 ligas, la misma cadena real
que usa la app (round_service.get_current_round -> MatchOdds -> Prediction)
y dice en que paso se corta, en vez de adivinarlo.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_round_coverage.py
"""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import Prediction
from backend.app.services.round_service import get_current_round

COMPETITION_CODES = ["premier_league", "laliga", "bundesliga", "serie_a", "ligue_1"]


def main() -> None:
    with session_scope() as db:
        now = dt.datetime.utcnow()
        for code in COMPETITION_CODES:
            competition = db.query(Competition).filter_by(code=code).one_or_none()
            if competition is None:
                print(f"{code}: NO EXISTE como Competition en la BD (¿falta el seed inicial?)")
                continue

            total_scheduled = (
                db.query(Match)
                .filter(Match.competition_id == competition.id, Match.status == "scheduled")
                .count()
            )
            total_scheduled_with_matchday = (
                db.query(Match)
                .filter(
                    Match.competition_id == competition.id,
                    Match.status == "scheduled",
                    Match.matchday.isnot(None),
                )
                .count()
            )
            next_scheduled = (
                db.query(Match)
                .filter(
                    Match.competition_id == competition.id,
                    Match.status == "scheduled",
                    Match.kickoff_utc >= now,
                )
                .order_by(Match.kickoff_utc.asc())
                .first()
            )

            print(f"\n=== {code} ===")
            print(f"  Partidos 'scheduled' totales: {total_scheduled}")
            print(f"  ...de esos, con matchday poblado: {total_scheduled_with_matchday}")
            if next_scheduled is not None:
                print(
                    f"  Proximo partido programado: {next_scheduled.kickoff_utc} "
                    f"(matchday={next_scheduled.matchday})"
                )
            else:
                print("  Proximo partido programado: NINGUNO (no hay ni un solo "
                      "'scheduled' con fecha futura -- problema de INGESTA de fixtures)")

            round_info = get_current_round(db, code)
            if round_info is None:
                print("  get_current_round(): None -- ni jornada real ni fallback por fecha "
                      "(sin partidos 'scheduled' futuros en absoluto, ver linea de arriba)")
                continue

            print(
                f"  Jornada actual resuelta: round={round_info.round} "
                f"fallback={round_info.is_fallback} "
                f"ventana=[{round_info.round_start}, {round_info.round_end}] "
                f"partidos={len(round_info.match_ids)}"
            )
            if not round_info.match_ids:
                print("  -> 0 partidos en esa jornada con kickoff futuro (puede que ya hayan "
                      "pasado todos SIN marcarse 'finished' todavia -- revisa si 'football-edge "
                      "update' corre de verdad, ver scheduler en docker-compose.yml)")
                continue

            matches_with_odds = (
                db.query(Match.id)
                .join(MatchOdds, MatchOdds.match_id == Match.id)
                .filter(Match.id.in_(round_info.match_ids))
                .distinct()
                .count()
            )
            predictions_total = (
                db.query(Prediction)
                .filter(Prediction.match_id.in_(round_info.match_ids))
                .count()
            )
            predictions_with_market = (
                db.query(Prediction)
                .filter(
                    Prediction.match_id.in_(round_info.match_ids),
                    Prediction.market_odds.isnot(None),
                )
                .count()
            )
            print(
                f"  De esos {len(round_info.match_ids)} partidos: "
                f"{matches_with_odds} tienen AL MENOS una cuota guardada (MatchOdds)"
            )
            print(
                f"  Predicciones en esa jornada: {predictions_total} totales, "
                f"{predictions_with_market} CON mercado real (market_odds no nulo) "
                "-- solo estas pueden salir en /predictions/best"
            )
            if predictions_total == 0:
                print("  -> 0 predicciones generadas para esta jornada: revisa si "
                      "'football-edge predict-upcoming'/'train' corrieron para esta liga "
                      "(logs del servicio 'scheduler')")
            elif predictions_with_market == 0:
                print("  -> Hay predicciones pero NINGUNA con mercado real: el problema esta en "
                      "la ingesta/casamiento de cuotas para esta liga (nombres de equipo que no "
                      "casan con el proveedor, o la API de cuotas no devolvio nada para ella)")


if __name__ == "__main__":
    main()
