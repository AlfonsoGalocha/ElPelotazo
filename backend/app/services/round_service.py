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
todavia tiene AL MENOS UN partido 'scheduled' Y que NO esta "superada" por
una jornada posterior que ya tenga algun partido 'finished' de verdad.
Esto cubre dos casos a la vez:
- Jornada 5 con partidos pendientes y jornada 6 ya con fecha pero SIN
  jugarse todavia: la jornada 5 sigue siendo "la actual" (la 6 no la ha
  "superado", solo tiene fecha).
- Jornada 6 con un UNICO partido aplazado muy lejos en el tiempo, mientras
  la jornada 7 (o posteriores) YA se jugaron y tienen partidos 'finished':
  la jornada 6 queda descartada como "actual" aunque su numero sea mas
  bajo, porque el calendario real ya demostro que se siguio jugando sin
  ella.

Bug real corregido (2026-10-09): la version anterior elegia la jornada por
el NUMERO de `matchday` mas bajo con algun partido 'scheduled', sin mirar
si alguna jornada posterior ya se habia jugado. Un partido aplazado
conserva su numero de jornada ORIGINAL aunque se juegue mucho mas tarde --
caso real: jornada 6 con un unico partido aplazado al 21 de octubre,
mientras las jornadas 7/8/9 ya estaban jugadas o jugandose. El numero mas
bajo (6) "secuestraba" indefinidamente la jornada actual, dejando el Home
sin partidos reales de esa liga hasta esa fecha lejana.

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

    now = dt.datetime.utcnow()
    scheduled_rows = (
        db.query(Match.season_id, Match.matchday)
        .filter(
            Match.competition_id == competition.id,
            Match.status == "scheduled",
            Match.matchday.isnot(None),
        )
        .all()
    )

    if not scheduled_rows:
        return _fallback_round(db, competition.id)

    # Candidatos (season_id, matchday) ordenados por matchday ascendente --
    # el primero que NO este "superado" (ver docstring del modulo) gana.
    candidates = sorted({(row[0], row[1]) for row in scheduled_rows}, key=lambda c: c[1])

    season_id = matchday = None
    for candidate_season_id, candidate_matchday in candidates:
        later_round_already_played = (
            db.query(Match.id)
            .filter(
                Match.competition_id == competition.id,
                Match.season_id == candidate_season_id,
                Match.matchday > candidate_matchday,
                Match.status == "finished",
            )
            .first()
            is not None
        )
        if not later_round_already_played:
            season_id, matchday = candidate_season_id, candidate_matchday
            break
    if matchday is None:
        season_id, matchday = candidates[0]

    # Una jornada se juega en varios dias: puede tener partidos YA jugados
    # (viernes/sabado) y otros todavia por jugar (domingo/lunes) a la vez.
    # Sin filtrar por fecha, el Home mostraria partidos que ya pasaron
    # (pedido explicito: nunca mostrar un partido cuyo kickoff sea anterior
    # a ahora, mires o no su `status` -- un partido recien acabado puede
    # tardar hasta la siguiente pasada del scheduler en marcarse
    # 'finished'). Esto NO cambia que jornada se considera "actual" (eso ya
    # lo decide la seleccion de arriba): solo evita listar, DENTRO de esa
    # jornada, partidos con fecha pasada.
    round_matches = (
        db.query(Match)
        .filter(
            Match.competition_id == competition.id,
            Match.season_id == season_id,
            Match.matchday == matchday,
            Match.kickoff_utc >= now,
        )
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


def get_next_round(db: Session, competition_code: str) -> RoundInfo | None:
    """La jornada INMEDIATAMENTE posterior a la actual (mismo criterio de
    `matchday`), para el filtro "Próxima jornada" -- nunca "cualquier
    partido futuro que caiga en una ventana de dias", que puede mezclar
    dos jornadas o solo cubrir parte de una. `None` si no hay jornada
    actual detectable (mismo fallback que `get_current_round`) o si la
    jornada actual es la ultima programada."""
    current = get_current_round(db, competition_code)
    if current is None or current.is_fallback or current.next_round is None:
        return None

    round_matches = (
        db.query(Match)
        .filter(
            Match.competition_id == current.competition_id,
            Match.season_id == current.season_id,
            Match.matchday == current.next_round,
        )
        .all()
    )
    if not round_matches:
        return None
    kickoffs = [m.kickoff_utc for m in round_matches]
    next_next_round_row = (
        db.query(func.min(Match.matchday))
        .filter(
            Match.competition_id == current.competition_id,
            Match.season_id == current.season_id,
            Match.matchday > current.next_round,
        )
        .scalar()
    )
    return RoundInfo(
        competition_id=current.competition_id,
        season_id=current.season_id,
        round=current.next_round,
        round_start=min(kickoffs),
        round_end=max(kickoffs),
        match_ids=[m.id for m in round_matches],
        is_fallback=False,
        next_round=next_next_round_row,
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
