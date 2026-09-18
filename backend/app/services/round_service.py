"""Determina la "jornada actual" de una competicion (seccion 1 de la
revision de arquitectura).

Por que no basta con "los proximos N dias": el futbol no juega todos los
dias, y una ventana de dias fija puede saltarse jornadas que aun tienen
partidos pendientes (p.ej. un aplazamiento) o mezclar dos jornadas
distintas en la misma vista sin decirlo.

Fuente del numero de jornada: `Match.matchday`, poblado SOLO por
`OpenFootballFixturesProvider` a partir del campo `round: "Matchday N"` de
openfootball/football.json (verificado real y consistente en las 5 ligas
del MVP). El dataset historico (football-data.co.uk / xgabora) NO trae
jornada, asi que partidos ya jugados hace tiempo pueden tener
`matchday IS NULL` — esto no afecta a la logica de "jornada actual", que
solo necesita jornada en los partidos programados (los que SI vienen de
openfootball).

Definicion de "jornada actual": la jornada con el `matchday` mas bajo que
todavia tiene AL MENOS UN partido con status='scheduled'. Esto cubre
exactamente el caso pedido: si la jornada 5 tiene partidos pendientes
(aplazados, o simplemente porque la jornada se juega en varios dias),
esa sigue siendo la jornada "actual" aunque la jornada 6 ya tenga fecha.

Limitacion honesta: si `refresh`/`update-fixtures` no se ha ejecutado
nunca con esta version del codigo, los partidos programados existentes
pueden tener `matchday IS NULL` (se ingirieron antes de que este campo se
rellenara). En ese caso se cae a un fallback explicito por fecha (proximos
N dias) y se marca `is_fallback=True` en vez de fingir una jornada real.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match

FALLBACK_WINDOW_DAYS = 7


@dataclass
class RoundInfo:
    competition_id: int
    season_id: int | None
    round: int | None
    round_start: dt.datetime | None
    round_end: dt.datetime | None
    match_ids: list[int] = field(default_factory=list)
    is_fallback: bool = False
    next_round: int | None = None


def get_current_round(db: Session, competition_code: str) -> RoundInfo | None:
    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        return None

    current_matchday_row = (
        db.query(Match.season_id, Match.matchday)
        .filter(
            Match.competition_id == competition.id,
            Match.status == "scheduled",
            Match.matchday.isnot(None),
        )
        .order_by(Match.matchday.asc())
        .first()
    )

    if current_matchday_row is None:
        return _fallback_round(db, competition.id)

    season_id, matchday = current_matchday_row
    round_matches = (
        db.query(Match)
        .filter(Match.competition_id == competition.id, Match.season_id == season_id, Match.matchday == matchday)
        .all()
    )
    kickoffs = [m.kickoff_utc for m in round_matches]

    next_round_row = (
        db.query(func.min(Match.matchday))
        .filter(
            Match.competition_id == competition.id,
            Match.season_id == season_id,
            Match.matchday > matchday,
        )
        .scalar()
    )

    return RoundInfo(
        competition_id=competition.id,
        season_id=season_id,
        round=matchday,
        round_start=min(kickoffs) if kickoffs else None,
        round_end=max(kickoffs) if kickoffs else None,
        match_ids=[m.id for m in round_matches],
        is_fallback=False,
        next_round=next_round_row,
    )


def _fallback_round(db: Session, competition_id: int) -> RoundInfo | None:
    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=FALLBACK_WINDOW_DAYS)
    matches = (
        db.query(Match)
        .filter(
            Match.competition_id == competition_id,
            Match.status == "scheduled",
            Match.kickoff_utc >= now,
            Match.kickoff_utc <= horizon,
        )
        .order_by(Match.kickoff_utc.asc())
        .all()
    )
    if not matches:
        return None
    return RoundInfo(
        competition_id=competition_id,
        season_id=matches[0].season_id,
        round=None,
        round_start=matches[0].kickoff_utc,
        round_end=matches[-1].kickoff_utc,
        match_ids=[m.id for m in matches],
        is_fallback=True,
        next_round=None,
    )
