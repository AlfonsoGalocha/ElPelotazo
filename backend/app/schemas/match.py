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

    model_config = {"from_attributes": True}
