"""Normalizacion de competiciones y temporadas a entidades de BD."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition, Season
from backend.app.utils.dates import season_label as season_label_from_date

CHAMPIONS_LEAGUE = "champions_league"
LIGA_F = "liga_f"
AFC_CHAMPIONS_LEAGUE = "afc_champions_league"
CONTINENTAL_CHAMPIONS = (CHAMPIONS_LEAGUE, AFC_CHAMPIONS_LEAGUE)
API_CANDIDATE_COMPETITIONS = (*CONTINENTAL_CHAMPIONS, LIGA_F)


def competition_team_name(name: str, competition_code: str) -> str:
    return f"{name} · Liga F" if competition_code == LIGA_F else name


COMPETITIONS: dict[str, tuple[str, str]] = {
    # code -> (nombre, pais)
    "laliga": ("La Liga", "Spain"),
    "premier_league": ("Premier League", "England"),
    "bundesliga": ("Bundesliga", "Germany"),
    "serie_a": ("Serie A", "Italy"),
    "ligue_1": ("Ligue 1", "France"),
    CHAMPIONS_LEAGUE: ("UEFA Champions League", "Europe"),
    LIGA_F: ("Liga F", "Spain"),
    AFC_CHAMPIONS_LEAGUE: ("AFC Champions League Elite", "Asia"),
    # Seccion "Selecciones" (pedido real del usuario, 2026-11): a
    # diferencia de las 5 de club, no hay mirror historico tipo
    # football-data.co.uk -- TODO su historico viene de API-Football
    # (ver ingestion/api_football/client.py::INTERNATIONAL_COMPETITION_EDITIONS
    # y cli.py::update_international). El "pais" de una competicion de
    # selecciones es una simplificacion deliberada (compite gente de todo
    # un continente/confederacion, no de un unico pais) -- no se usa para
    # logica, solo texto. Alcance decidido explicitamente por el usuario:
    # absoluta MASCULINA (Mundial, Eurocopa, Copa America, Copa Africa de
    # Naciones + sus clasificatorios, Nations League) -- deja fuera
    # femenino, categorias inferiores y amistosos (ver seccion 17 de
    # docs/architecture_audit.md).
    "uefa_nations_league": ("UEFA Nations League", "Europe"),
    "world_cup": ("World Cup", "World"),
    "world_cup_qualification_europe": ("World Cup - Qualification Europe", "Europe"),
    "world_cup_qualification_south_america": (
        "World Cup - Qualification South America",
        "South America",
    ),
    "world_cup_qualification_concacaf": ("World Cup - Qualification CONCACAF", "North America"),
    "world_cup_qualification_africa": ("World Cup - Qualification Africa", "Africa"),
    "world_cup_qualification_asia": ("World Cup - Qualification Asia", "Asia"),
    "world_cup_qualification_oceania": ("World Cup - Qualification Oceania", "Oceania"),
    "world_cup_qualification_intercontinental_playoffs": (
        "World Cup - Qualification Intercontinental Play-offs",
        "World",
    ),
    "euro_championship": ("Euro Championship", "Europe"),
    "euro_championship_qualification": ("Euro Championship - Qualification", "Europe"),
    "copa_america": ("Copa America", "South America"),
    "africa_cup_of_nations": ("Africa Cup of Nations", "Africa"),
    "africa_cup_of_nations_qualification": ("Africa Cup of Nations - Qualification", "Africa"),
    "concacaf_nations_league": ("CONCACAF Nations League", "North America"),
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
