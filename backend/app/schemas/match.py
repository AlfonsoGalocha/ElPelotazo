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
    # CompetitionConfig (ver db/models/core.py): `None` en competiciones
    # ya existentes antes de anhadir estos campos, tratado como el
    # default documentado alli ("CORE"/habilitada/prioridad 0), nunca
    # como ausencia de dato real.
    tier: str | None = None
    enabled: bool | None = None
    priority: int | None = None

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


class FormMatchOut(BaseModel):
    """Un partido pasado desde el punto de vista de UN equipo concreto
    (ver api/routes/matches.py::get_match_form) -- usado tanto para forma
    reciente como para el cara a cara (H2H)."""

    match_id: int
    kickoff_utc: dt.datetime
    opponent: str
    venue: str  # "home" | "away"
    goals_for: int
    goals_against: int
    result: str  # "W" | "D" | "L"


class TeamFormOut(BaseModel):
    team: TeamOut
    recent: list[FormMatchOut]
    recent_at_venue: list[FormMatchOut]


class MatchFormOut(BaseModel):
    home_team: TeamFormOut
    away_team: TeamFormOut
    head_to_head: list[FormMatchOut]


class RoundOut(BaseModel):
    """Jornada actual de una competicion (ver services/round_service.py)."""

    competition_code: str
    round: int | None
    round_start: dt.datetime | None
    round_end: dt.datetime | None
    next_round: int | None
    is_fallback: bool
    is_next_round: bool = False
    match_ids: list[int]
