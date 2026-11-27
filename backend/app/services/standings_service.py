"""Clasificacion de liga: calculo + snapshot historico (Bloque 3.4/3.5 de
la mision, `StandingsSnapshot`, APPEND-ONLY -- ver su docstring).

Desempate deliberadamente simple (puntos -> diferencia de goles -> goles
a favor -> `team_id` como ultimo desempate estable): no replica
desempates oficiales exoticos de cada liga (enfrentamientos directos,
fair play...) -- para una foto historica razonable es suficiente, y nunca
se inventa una regla mas sofisticada sin verificar la de cada
competicion real.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.standings import StandingsSnapshot
from backend.app.normalization.competitions import EXPERIMENTAL_COMPETITIONS


def compute_current_standings(db: Session, competition_id: int, season_id: int) -> tuple[list[dict], int | None]:
    """Tabla calculada SOLO con partidos `finished` y marcador real
    conocido de esa (competicion, temporada) -- nunca cuenta un partido
    aplazado/cancelado ("void", mismo principio que
    `evaluation_service.py::void_cancelled_predictions") ni uno sin
    marcador todavia.

    Devuelve `(filas_ordenadas, matchday_ceiling)` -- filas ya en orden
    de posicion (la primera es el lider)."""
    query = db.query(Match).filter(
        Match.competition_id == competition_id,
        Match.season_id == season_id,
        Match.status == "finished",
        Match.home_goals.isnot(None),
        Match.away_goals.isnot(None),
    )
    competition = db.get(Competition, competition_id)
    if competition is not None and competition.code in EXPERIMENTAL_COMPETITIONS:
        query = query.filter(Match.round_name.like("Regular Season - %"))
    matches = query.all()

    stats: dict[int, dict] = {}
    matchdays_seen: list[int] = []
    any_unknown_matchday = False
    for m in matches:
        if m.matchday is None:
            any_unknown_matchday = True
        else:
            matchdays_seen.append(m.matchday)
        for team_id, goals_for, goals_against in (
            (m.home_team_id, m.home_goals, m.away_goals),
            (m.away_team_id, m.away_goals, m.home_goals),
        ):
            row = stats.setdefault(
                team_id,
                {"played": 0, "won": 0, "drawn": 0, "lost": 0, "goals_for": 0, "goals_against": 0, "points": 0},
            )
            row["played"] += 1
            row["goals_for"] += goals_for
            row["goals_against"] += goals_against
            if goals_for > goals_against:
                row["won"] += 1
                row["points"] += 3
            elif goals_for == goals_against:
                row["drawn"] += 1
                row["points"] += 1
            else:
                row["lost"] += 1

    def sort_key(item: tuple[int, dict]) -> tuple:
        team_id, row = item
        goal_difference = row["goals_for"] - row["goals_against"]
        return (-row["points"], -goal_difference, -row["goals_for"], team_id)

    ranked = sorted(stats.items(), key=sort_key)
    rows = [
        {
            "team_id": team_id,
            "position": position,
            "played": row["played"],
            "won": row["won"],
            "drawn": row["drawn"],
            "lost": row["lost"],
            "goals_for": row["goals_for"],
            "goals_against": row["goals_against"],
            "goal_difference": row["goals_for"] - row["goals_against"],
            "points": row["points"],
        }
        for position, (team_id, row) in enumerate(ranked, start=1)
    ]
    matchday_ceiling = None if any_unknown_matchday or not matchdays_seen else max(matchdays_seen)
    return rows, matchday_ceiling


def snapshot_standings(db: Session, competition_id: int, season_id: int) -> int:
    """Calcula la tabla actual y archiva una fila NUEVA por equipo
    (nunca actualiza una fila de un snapshot anterior, ver docstring de
    `StandingsSnapshot`). `db.flush()` solo, misma convencion que
    `record_ingestion_run`/`archive_raw_responses`: el llamador decide
    cuando hacer commit. Devuelve 0 (sin archivar nada) si todavia no hay
    ningun partido finalizado -- nunca inserta una tabla vacia de
    relleno."""
    rows, matchday_ceiling = compute_current_standings(db, competition_id, season_id)
    if not rows:
        return 0
    as_of = dt.datetime.utcnow()
    for row in rows:
        db.add(
            StandingsSnapshot(
                competition_id=competition_id,
                season_id=season_id,
                team_id=row["team_id"],
                as_of=as_of,
                matchday_ceiling=matchday_ceiling,
                position=row["position"],
                played=row["played"],
                won=row["won"],
                drawn=row["drawn"],
                lost=row["lost"],
                goals_for=row["goals_for"],
                goals_against=row["goals_against"],
                goal_difference=row["goal_difference"],
                points=row["points"],
            )
        )
    db.flush()
    return len(rows)


def latest_standings_snapshot(db: Session, competition_id: int, season_id: int) -> list[StandingsSnapshot]:
    """El snapshot MAS RECIENTE (mismo `as_of`) para (competicion,
    temporada), ya ordenado por posicion -- para servir "la clasificacion
    de hoy" sin tener que recalcularla al vuelo."""
    latest_as_of = (
        db.query(StandingsSnapshot.as_of)
        .filter(StandingsSnapshot.competition_id == competition_id, StandingsSnapshot.season_id == season_id)
        .order_by(StandingsSnapshot.as_of.desc())
        .limit(1)
        .scalar()
    )
    if latest_as_of is None:
        return []
    return (
        db.query(StandingsSnapshot)
        .filter(
            StandingsSnapshot.competition_id == competition_id,
            StandingsSnapshot.season_id == season_id,
            StandingsSnapshot.as_of == latest_as_of,
        )
        .order_by(StandingsSnapshot.position)
        .all()
    )
