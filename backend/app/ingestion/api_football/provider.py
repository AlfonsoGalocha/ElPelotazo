"""Adapter para API-Football (api-football.com / RapidAPI).

ESTADO: PENDIENTE, deshabilitado por defecto (API_FOOTBALL_ENABLED=false).

API-Football tiene un plan gratuito muy limitado (100 requests/dia), lo que
lo hace inviable como fuente historica principal para 5 ligas desde 2018/19,
pero es una buena fuente FUTURA para:
- Fixtures del dia (partidos de hoy) en produccion.
- Alineaciones probables / lesiones (Fase 5-6).
- Datos live (Fase 7).

Requiere API_FOOTBALL_KEY. No se hardcodea ninguna key: se lee de entorno
via Settings. Sin key configurada, is_available() devuelve False y el
pipeline debe seguir funcionando (esta fuente es opcional).
"""

from __future__ import annotations

from backend.app.config.settings import get_settings
from backend.app.ingestion.base import DataProvider, RawMatchRecord


class ApiFootballProvider(DataProvider):
    name = "api_football"

    def is_available(self) -> bool:
        settings = get_settings()
        return settings.api_football_enabled and bool(settings.api_football_key)

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        raise NotImplementedError(
            "ApiFootballProvider requiere API_FOOTBALL_KEY y no esta implementado en el MVP."
        )
