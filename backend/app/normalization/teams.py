"""Resolucion de nombres de equipo de cualquier fuente a un team_id canonico.

Nunca se debe hacer join por igualdad de strings entre fuentes distintas:
"Man United" (football-data.co.uk) vs "Manchester Utd" (otra fuente) deben
resolver al mismo Team. Este modulo mantiene la tabla de mapping y aplica
una normalizacion basica (fallback) cuando no hay mapping explicito.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from backend.app.db.models.core import Team, TeamNameMapping

# Mapping explicito para los alias mas comunes en football-data.co.uk.
# Se amplia segun se detecten nuevos alias al ingerir mas temporadas/ligas.
KNOWN_ALIASES: dict[str, str] = {
    "man united": "Manchester United",
    "man utd": "Manchester United",
    "man city": "Manchester City",
    "newcastle": "Newcastle United",
    "nott'm forest": "Nottingham Forest",
    "wolves": "Wolverhampton Wanderers",
    "spurs": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur",
    "sheffield united": "Sheffield United",
    "west brom": "West Bromwich Albion",
    "atletico madrid": "Atletico Madrid",
    "ath madrid": "Atletico Madrid",
    "ath bilbao": "Athletic Bilbao",
    "sociedad": "Real Sociedad",
    "betis": "Real Betis",
    "celta": "Celta Vigo",
    "espanol": "Espanyol",
    "vallecano": "Rayo Vallecano",
    "alaves": "Deportivo Alaves",
    "bayern munich": "Bayern Munich",
    "dortmund": "Borussia Dortmund",
    "m'gladbach": "Borussia Monchengladbach",
    "leverkusen": "Bayer Leverkusen",
    "ein frankfurt": "Eintracht Frankfurt",
    "hertha": "Hertha Berlin",
    "inter": "Inter Milan",
    "milan": "AC Milan",
    "juventus": "Juventus",
    "paris sg": "Paris Saint-Germain",
    "psg": "Paris Saint-Germain",
    "st etienne": "Saint-Etienne",
}


def _normalize_key(name: str) -> str:
    key = name.strip().lower()
    key = re.sub(r"\s+", " ", key)
    return key


def canonical_name_for(raw_name: str) -> str:
    """Aplica alias conocidos; si no hay alias, usa el propio nombre (title case)."""
    key = _normalize_key(raw_name)
    return KNOWN_ALIASES.get(key, raw_name.strip())


def resolve_team_id(db: Session, source: str, raw_name: str) -> int:
    """Devuelve el team_id canonico para (source, raw_name), creandolo si falta.

    1. Busca un mapping explicito (source, raw_name) -> team_id.
    2. Si no existe, resuelve el nombre canonico via alias/heuristica y
       busca/crea el Team correspondiente, guardando el mapping para que
       futuras ingestas de la misma fuente sean directas (sin re-resolver).
    """
    mapping = (
        db.query(TeamNameMapping)
        .filter_by(source=source, source_name=raw_name)
        .one_or_none()
    )
    if mapping is not None:
        return mapping.team_id

    canonical = canonical_name_for(raw_name)
    team = db.query(Team).filter_by(canonical_name=canonical).one_or_none()
    if team is None:
        team = Team(canonical_name=canonical)
        db.add(team)
        db.flush()

    db.add(TeamNameMapping(source=source, source_name=raw_name, team_id=team.id))
    db.flush()
    return team.id
