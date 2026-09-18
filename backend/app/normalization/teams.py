"""Resolucion de nombres de equipo de cualquier fuente a un team_id canonico.

Nunca se debe hacer join por igualdad de strings entre fuentes distintas:
"Man United" (football-data.co.uk) vs "Manchester Utd" (otra fuente) deben
resolver al mismo Team. Este modulo mantiene la tabla de mapping y aplica
una normalizacion basica (fallback) cuando no hay mapping explicito.
"""

from __future__ import annotations

import re
import unicodedata

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
    "nottm forest": "Nottingham Forest",
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
    # --- Alias del fixture provider (openfootball/football.json), nombres
    # oficiales completos -> nombre canonico corto ya usado por el dataset
    # historico (football-data.co.uk). Critico: si esto falla, el partido
    # arranca sin historial (cold start) en vez de usar las stats reales
    # ya ingeridas para ese equipo.
    # La Liga
    "athletic club": "Athletic Bilbao",
    "ca osasuna": "Osasuna",
    "club atletico de madrid": "Atletico Madrid",
    "deportivo alaves": "Deportivo Alaves",
    "elche cf": "Elche",
    "fc barcelona": "Barcelona",
    "getafe cf": "Getafe",
    "levante ud": "Levante",
    "malaga cf": "Malaga",
    "rc celta de vigo": "Celta Vigo",
    "rc deportivo la coruna": "La Coruna",
    "rcd espanyol de barcelona": "Espanyol",
    "rayo vallecano de madrid": "Rayo Vallecano",
    "real betis balompie": "Real Betis",
    "real madrid cf": "Real Madrid",
    "real racing club de santander": "Santander",
    "real sociedad de futbol": "Real Sociedad",
    "sevilla fc": "Sevilla",
    "valencia cf": "Valencia",
    "villarreal cf": "Villarreal",
    "rcd mallorca": "Mallorca",
    "real oviedo": "Oviedo",
    "girona fc": "Girona",
    # Premier League
    "afc bournemouth": "Bournemouth",
    "arsenal fc": "Arsenal",
    "aston villa fc": "Aston Villa",
    "brentford fc": "Brentford",
    "brighton & hove albion fc": "Brighton",
    "chelsea fc": "Chelsea",
    "coventry city fc": "Coventry",
    "crystal palace fc": "Crystal Palace",
    "everton fc": "Everton",
    "fulham fc": "Fulham",
    "hull city afc": "Hull",
    "ipswich town fc": "Ipswich",
    "leeds united fc": "Leeds",
    "liverpool fc": "Liverpool",
    "manchester city fc": "Manchester City",
    "manchester united fc": "Manchester United",
    "newcastle united fc": "Newcastle United",
    "nottingham forest fc": "Nottingham Forest",
    "sunderland afc": "Sunderland",
    "tottenham hotspur fc": "Tottenham Hotspur",
    "west ham united fc": "West Ham",
    "wolverhampton wanderers fc": "Wolverhampton Wanderers",
    "burnley fc": "Burnley",
    # Bundesliga
    "1. fc koln": "FC Koln",
    "1. fc union berlin": "Union Berlin",
    "1. fsv mainz 05": "Mainz",
    "bayer 04 leverkusen": "Bayer Leverkusen",
    "borussia monchengladbach": "Borussia Monchengladbach",
    "fc augsburg": "Augsburg",
    "fc bayern munchen": "Bayern Munich",
    "fc schalke 04": "Schalke 04",
    "hamburger sv": "Hamburg",
    "sc freiburg": "Freiburg",
    "sc paderborn 07": "Paderborn",
    "sv 07 elversberg": "Elversberg",
    "sv werder bremen": "Werder Bremen",
    "tsg 1899 hoffenheim": "Hoffenheim",
    "vfb stuttgart": "Stuttgart",
    "vfl wolfsburg": "Wolfsburg",
    "1. fc heidenheim 1846": "Heidenheim",
    "fc st. pauli": "St Pauli",
    # Serie A
    "ac monza": "Monza",
    "acf fiorentina": "Fiorentina",
    "as roma": "Roma",
    "atalanta bc": "Atalanta",
    "bologna fc 1909": "Bologna",
    "cagliari calcio": "Cagliari",
    "como 1907": "Como",
    "fc internazionale milano": "Inter Milan",
    "frosinone calcio": "Frosinone",
    "genoa cfc": "Genoa",
    "juventus fc": "Juventus",
    "parma calcio 1913": "Parma",
    "ss lazio": "Lazio",
    "ssc napoli": "Napoli",
    "torino fc": "Torino",
    "us lecce": "Lecce",
    "us sassuolo calcio": "Sassuolo",
    "udinese calcio": "Udinese",
    "venezia fc": "Venezia",
    "hellas verona fc": "Verona",
    "ac pisa 1909": "Pisa",
    "us cremonese": "Cremonese",
    # Ligue 1
    "aj auxerre": "Auxerre",
    "as monaco fc": "Monaco",
    "angers sco": "Angers",
    "es troyes ac": "Troyes",
    "fc lorient": "Lorient",
    "le havre ac": "Le Havre",
    "le mans fc": "Le Mans",
    "lille osc": "Lille",
    "ogc nice": "Nice",
    "olympique lyonnais": "Lyon",
    "olympique de marseille": "Marseille",
    "paris saint-germain fc": "Paris Saint-Germain",
    "rc strasbourg alsace": "Strasbourg",
    "racing club de lens": "Lens",
    "stade brestois 29": "Brest",
    "stade rennais fc 1901": "Rennes",
    "toulouse fc": "Toulouse",
    "fc nantes": "Nantes",
    "fc metz": "Metz",
}


def _normalize_key(name: str) -> str:
    key = name.strip().lower()
    key = unicodedata.normalize("NFKD", key).encode("ascii", "ignore").decode("ascii")
    key = re.sub(r"\s+", " ", key)
    return key


def _fuzzy_key(name: str) -> str:
    """Normalizacion mas agresiva que `_normalize_key`, solo para el
    fallback de `resolve_team_id`: quita acentos Y toda la puntuacion/
    espacios, no solo colapsa espacios. Pensada para casar variantes de
    escritura de una fuente NUEVA (p.ej. una casa de apuestas) contra un
    Team ya existente sin necesitar un alias explicito para cada detalle
    menor: "Atletico Madrid" == "Atlético Madrid", "Paris Saint-Germain"
    == "Paris Saint Germain", "AFC Bournemouth" == "Bournemouth AFC", etc.
    No sustituye a KNOWN_ALIASES (que sigue siendo la fuente de verdad para
    abreviaturas tipo "Utd"/"Sociedad"), solo evita crear un Team DUPLICADO
    cuando la unica diferencia es tildes/puntuacion/orden de una palabra
    generica de club ("fc", "cf", "afc", "sc"...).
    """
    key = _normalize_key(name)
    key = re.sub(r"[^a-z0-9]+", "", key)
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

    Antes de crear un Team nuevo se intenta ademas un match "fuzzy" (sin
    tildes ni puntuacion) contra los Team ya existentes: sin esto, una
    fuente nueva (p.ej. una casa de apuestas) que escriba un nombre ya
    conocido con una tilde o un guion distinto ("Atlético Madrid" en vez
    de "Atletico Madrid") crearia un Team FANTASMA con un team_id distinto
    al que usa el resto del sistema, y esa cuota nunca casaria con ningun
    partido programado aunque el equipo sea exactamente el mismo.
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
        fuzzy_target = _fuzzy_key(canonical)
        for candidate in db.query(Team).all():
            if _fuzzy_key(candidate.canonical_name) == fuzzy_target:
                team = candidate
                break

    if team is None:
        team = Team(canonical_name=canonical)
        db.add(team)
        db.flush()

    db.add(TeamNameMapping(source=source, source_name=raw_name, team_id=team.id))
    db.flush()
    return team.id
