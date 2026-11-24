"""Contrato comun para todos los adapters de datos (Adapter pattern)."""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RawMatchRecord:
    """Partido crudo tal como lo entrega una fuente, antes de normalizar.

    Todos los campos estadisticos son opcionales: una fuente puede no
    proveerlos. `None` significa "desconocido", nunca 0 por defecto.
    """

    provider: str
    provider_id: str
    competition_code: str
    season_label: str
    date: str  # ISO 8601
    home_team_raw: str
    away_team_raw: str
    home_goals: int | None = None
    away_goals: int | None = None
    home_goals_ht: int | None = None
    away_goals_ht: int | None = None
    referee_raw: str | None = None
    matchday: int | None = None  # jornada, cuando la fuente la provee (ver fixtures_provider.py)
    round_name: str | None = None
    provider_status: str | None = None

    # Estado REAL del partido segun la fuente (seccion "Rendimiento del
    # modelo", pedido real del usuario): "scheduled"|"finished"|
    # "postponed"|"cancelled". `None` para fuentes que no distinguen nada
    # mas alla de "tiene goles o no" (football-data.co.uk, openfootball
    # fixtures) -- `services/data_service.py::_upsert_match` cae entonces
    # al criterio antiguo (hay goles -> finished, si no -> scheduled).
    # Unicamente `api_football/fixtures_provider.py` (temporada en curso,
    # con estados reales de la API) lo rellena hoy.
    status: str | None = None

    # Escudo REAL del equipo, cuando la fuente lo trae en el mismo payload
    # que el partido (API-Football: `teams.home/away.logo`) -- `None` para
    # fuentes que no lo proveen (football-data.co.uk, openfootball...),
    # nunca un valor inventado. Ver db/models/core.py::Team.logo_url.
    home_logo_url: str | None = None
    away_logo_url: str | None = None

    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_fouls: int | None = None
    away_fouls: int | None = None
    home_yellow_cards: int | None = None
    away_yellow_cards: int | None = None
    home_red_cards: int | None = None
    away_red_cards: int | None = None

    odds: list[RawOddsRecord] = field(default_factory=list)


@dataclass
class RawOddsRecord:
    bookmaker: str
    market: str
    line: float | None
    selection: str
    price: float
    snapshot_type: str = "closing"
    provider_updated_at: dt.datetime | None = None
    fetched_at: dt.datetime | None = None


@dataclass
class FixtureOddsSnapshot:
    """Cuotas de un partido FUTURO concreto, listas para casar contra un
    `Match` ya existente en la BD (no crea partidos nuevos, solo cuotas).
    Compartido por cualquier adapter de cuotas en vivo (The Odds API,
    API-Football...): la logica de casar equipo+fecha y de agregar
    consenso multi-casa es identica sea cual sea la fuente."""

    home_team_raw: str
    away_team_raw: str
    commence_time: dt.datetime
    odds: list[RawOddsRecord]
    provider_fixture_id: str | None = None
    suspended_markets: frozenset[str] = frozenset()


class DataProvider(ABC):
    """Interfaz que debe implementar cualquier fuente de datos."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Chequeo rapido de disponibilidad/config (p.ej. API key presente)."""

    @abstractmethod
    def fetch_matches(
        self, competition_code: str, season_label: str
    ) -> list[RawMatchRecord]:
        """Devuelve los partidos crudos de una competicion/temporada."""
