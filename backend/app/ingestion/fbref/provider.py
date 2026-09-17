"""Adapter para FBref / Sports Reference.

ESTADO: PENDIENTE, deshabilitado por defecto (FBREF_ENABLED=false).

FBref publica tablas HTML muy completas (tiros, xG, posesion, tarjetas...)
pero:
- No tiene API oficial; requiere parsear tablas HTML (muchas envueltas en
  comentarios HTML para evitar scraping trivial).
- Su politica de uso pide explicitamente limitar el ritmo de requests
  (recomiendan <= 1 request cada 3 segundos, ver sports-reference.com/bot-traffic).
- Los nombres de equipo difieren de football-data.co.uk y Understat, por lo
  que requeriria trabajo adicional de normalizacion (backend/app/normalization).

Se deja el adapter con la interfaz correcta para no bloquear la arquitectura,
pero no se activa hasta validar un pipeline de scraping respetuoso con rate
limits y una estrategia de cache persistente.
"""

from __future__ import annotations

from backend.app.config.settings import get_settings
from backend.app.ingestion.base import DataProvider, RawMatchRecord


class FBrefProvider(DataProvider):
    name = "fbref"

    def is_available(self) -> bool:
        return get_settings().fbref_enabled

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        raise NotImplementedError("FBrefProvider esta pendiente de validacion (ver docstring).")
