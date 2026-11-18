from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.config.settings import get_settings
from backend.app.db.database import get_db
from backend.app.services.ranking_config_service import get_opportunity_odds_band, set_opportunity_odds_band

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/config/data-sources")
def data_sources_status() -> dict:
    """Solo los booleanos de que fuentes estan activas (nunca una key real)
    -- pensado para la pagina "Configuracion" del frontend, que es de solo
    lectura: cambiar esto de verdad requiere editar el `.env` del backend
    y reiniciar. La banda de cuotas de "Mejores oportunidades" (abajo) SI
    es editable en runtime -- ver RankingConfig."""
    settings = get_settings()
    return {
        "football_data_co_uk_enabled": settings.football_data_co_uk_enabled,
        "api_football_enabled": settings.api_football_enabled and bool(settings.api_football_key),
        "odds_api_enabled": settings.odds_api_enabled and bool(settings.odds_api_key),
        "odds_api_fetch_additional_markets": settings.odds_api_fetch_additional_markets,
    }


class RankingConfigOut(BaseModel):
    min_opportunity_odds: float
    max_opportunity_odds: float
    default_min_opportunity_odds: float
    default_max_opportunity_odds: float


class RankingConfigIn(BaseModel):
    min_opportunity_odds: float
    max_opportunity_odds: float


# "Mejores oportunidades" (cambio de filosofia del ranking, pedido real
# del usuario): primera preferencia de usuario editable en RUNTIME de
# este proyecto (ver db/models/core.py::RankingConfig y
# services/ranking_config_service.py) -- las demas paginas de
# Configuracion son de solo lectura (`/config/data-sources`, arriba).
@router.get("/config/ranking", response_model=RankingConfigOut)
def get_ranking_config(db: Session = Depends(get_db)) -> RankingConfigOut:
    settings = get_settings()
    min_odds, max_odds = get_opportunity_odds_band(db, settings)
    return RankingConfigOut(
        min_opportunity_odds=min_odds,
        max_opportunity_odds=max_odds,
        default_min_opportunity_odds=settings.min_opportunity_odds,
        default_max_opportunity_odds=settings.max_opportunity_odds,
    )


@router.put("/config/ranking", response_model=RankingConfigOut)
def update_ranking_config(payload: RankingConfigIn, db: Session = Depends(get_db)) -> RankingConfigOut:
    settings = get_settings()
    try:
        min_odds, max_odds = set_opportunity_odds_band(
            db, payload.min_opportunity_odds, payload.max_opportunity_odds
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # `set_opportunity_odds_band` solo hace `db.flush()` (convencion del
    # proyecto: el servicio nunca decide cuando confirmar la transaccion,
    # ver promotion_service.py) -- `get_db()` NUNCA hace commit por su
    # cuenta (solo `db.close()` al terminar la request, ver database.py),
    # asi que sin este commit explicito la preferencia guardada se
    # perderia en silencio en cuanto esta request termine.
    db.commit()
    return RankingConfigOut(
        min_opportunity_odds=min_odds,
        max_opportunity_odds=max_odds,
        default_min_opportunity_odds=settings.min_opportunity_odds,
        default_max_opportunity_odds=settings.max_opportunity_odds,
    )
