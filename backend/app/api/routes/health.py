from __future__ import annotations

from fastapi import APIRouter

from backend.app.config.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/config/data-sources")
def data_sources_status() -> dict:
    """Solo los booleanos de que fuentes estan activas (nunca una key real)
    -- pensado para la pagina "Configuracion" del frontend, que es de solo
    lectura: cambiar esto de verdad requiere editar el `.env` del backend
    y reiniciar, no hay (todavia) un sistema de preferencias por usuario."""
    settings = get_settings()
    return {
        "football_data_co_uk_enabled": settings.football_data_co_uk_enabled,
        "api_football_enabled": settings.api_football_enabled and bool(settings.api_football_key),
        "odds_api_enabled": settings.odds_api_enabled and bool(settings.odds_api_key),
        "odds_api_fetch_additional_markets": settings.odds_api_fetch_additional_markets,
    }
