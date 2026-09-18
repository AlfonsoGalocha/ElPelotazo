from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class TeamOut(BaseModel):
    id: int
    canonical_name: str

    model_config = {"from_attributes": True}


class CompetitionOut(BaseModel):
    id: int
    code: str
    name: str
    country: str

    model_config = {"from_attributes": True}


class MatchOut(BaseModel):
    id: int
    competition: CompetitionOut
    home_team: TeamOut
    away_team: TeamOut
    kickoff_utc: dt.datetime
    status: str
    home_goals: int | None
    away_goals: int | None
    matchday: int | None = None

    model_config = {"from_attributes": True}


class RoundOut(BaseModel):
    """Jornada actual de una competicion (ver services/round_service.py)."""

    competition_code: str
    round: int | None
    round_start: dt.datetime | None
    round_end: dt.datetime | None
    next_round: int | None
    is_fallback: bool
    match_ids: list[int]
