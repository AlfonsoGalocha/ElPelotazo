"""Adapter para Understat (xG por partido).

ESTADO: PENDIENTE, deshabilitado por defecto (UNDERSTAT_ENABLED=false).

Motivo: Understat no ofrece API publica formal; los datos se obtienen
parseando un JSON embebido en las paginas HTML (`JSON.parse('...')` dentro
de un <script>). Esto es fragil ante cambios de maquetacion y no tiene
garantias de licencia/ToS explicitas para uso programatico continuo.

Antes de habilitar en produccion:
1. Confirmar que el HTML actual sigue exponiendo el bloque `matchesData`.
2. Revisar el aviso legal de understat.com sobre scraping.
3. Anhadir cache agresiva (no roundtrip en cada request) y rate limiting.

El adapter existe para dejar la interfaz lista (arquitectura de adapters,
seccion 5 del brief) pero `is_available()` devuelve False hasta activarlo.
"""

from __future__ import annotations

from backend.app.config.settings import get_settings
from backend.app.ingestion.base import DataProvider, RawMatchRecord


class UnderstatProvider(DataProvider):
    name = "understat"

    def is_available(self) -> bool:
        return get_settings().understat_enabled

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        raise NotImplementedError(
            "UnderstatProvider esta pendiente de validacion (ver docstring del modulo)."
        )
