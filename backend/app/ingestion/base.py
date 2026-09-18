"""Contrato comun para todos los adapters de datos (Adapter pattern)."""

from __future__ import annotations

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
