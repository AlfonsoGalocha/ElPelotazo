"""Normalizacion de competiciones y temporadas a entidades de BD."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition, Season
from backend.app.utils.dates import season_label as season_label_from_date

COMPETITIONS: dict[str, tuple[str, str]] = {
    # code -> (nombre, pais)
    "laliga": ("La Liga", "Spain"),
    "premier_league": ("Premier League", "England"),
    "bundesliga": ("Bundesliga", "Germany"),
    "serie_a": ("Serie A", "Italy"),
    "ligue_1": ("Ligue 1", "France"),
}


def resolve_competition_id(db: Session, code: str) -> int:
    competition = db.query(Competition).filter_by(code=code).one_or_none()
    if competition is None:
        name, country = COMPETITIONS[code]
        competition = Competition(code=code, name=name, country=country)
        db.add(competition)
        db.flush()
    return competition.id


def resolve_season_id(db: Session, competition_id: int, label: str) -> int:
    season = (
        db.query(Season).filter_by(competition_id=competition_id, label=label).one_or_none()
    )
    if season is None:
        season = Season(competition_id=competition_id, label=label)
        db.add(season)
        db.flush()
    return season.id


__all__ = ["COMPETITIONS", "resolve_competition_id", "resolve_season_id", "season_label_from_date"]
