"""Adapter para The Odds API (cuotas en vivo/futuras, no historicas).

ESTADO: PENDIENTE, deshabilitado por defecto (ODDS_API_ENABLED=false).

Para el MVP, las cuotas historicas viven embebidas en el CSV de
football-data.co.uk (ver ingestion/football_data). Este adapter se reserva
para cuando el sistema necesite cuotas ACTUALES de partidos futuros (fase de
produccion en vivo), donde The Odds API ofrece cobertura multi-bookmaker con
plan gratuito limitado (500 requests/mes).

Requiere ODDS_API_KEY.
"""

from __future__ import annotations

from backend.app.config.settings import get_settings
from backend.app.ingestion.base import DataProvider, RawMatchRecord


class OddsApiProvider(DataProvider):
    name = "odds_api"

    def is_available(self) -> bool:
        settings = get_settings()
        return settings.odds_api_enabled and bool(settings.odds_api_key)

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        raise NotImplementedError("OddsApiProvider no esta implementado en el MVP.")
