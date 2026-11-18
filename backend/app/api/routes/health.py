from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.config.settings import get_settings
from backend.app.db.database import get_db
from backend.app.schemas.match import UtcDatetime
from backend.app.services.odds_diagnostics_service import latest_runs_by_competition, market_odds_freshness
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


class OddsIngestionRunOut(BaseModel):
    provider: str
    operation: str
    competition_code: str
    # `UtcDatetime` (nunca `dt.datetime` a secas): estos timestamps son
    # naive-pero-UTC por convencion del backend (ver `started_at`/
    # `finished_at` en cli.py, siempre `dt.datetime.utcnow()`) -- sin
    # anhadir la zona explicita en la serializacion, `new Date(...)` en
    # el navegador los interpretaria como hora LOCAL, no UTC (mismo bug
    # real ya corregido una vez para `kickoff_utc`, ver schemas/match.py).
    started_at: UtcDatetime
    finished_at: UtcDatetime
    fixtures_fetched: int | None
    matched: int | None
    error: str | None

    model_config = {"from_attributes": True}


class OddsDiagnosticsOut(BaseModel):
    # Una fila por (provider, operation, competition_code) -- el ultimo
    # intento REAL de cada una, exito o fallo (ver OddsIngestionRun).
    latest_runs: list[OddsIngestionRunOut]
    # Evidencia ya guardada en MatchOdds (nunca inventada): cuando se
    # registro la cuota mas reciente de verdad, y que casas tenemos.
    most_recent_odds_recorded_at: UtcDatetime | None
    bookmakers_seen: list[str]
    scheduled_matches_with_odds: int


# Panel tecnico de diagnostico (seccion "Configuracion", pedido real del
# usuario tras investigar por que la app mostraba cuotas desactualizadas):
# "por que esta cuota no se actualizo" sin tener que volver a ejecutar un
# comando a mano y mirar la terminal en directo -- ver
# services/odds_diagnostics_service.py.
@router.get("/config/odds-diagnostics", response_model=OddsDiagnosticsOut)
def odds_diagnostics(db: Session = Depends(get_db)) -> OddsDiagnosticsOut:
    runs = latest_runs_by_competition(db)
    freshness = market_odds_freshness(db)
    return OddsDiagnosticsOut(
        latest_runs=[OddsIngestionRunOut.model_validate(run) for run in runs],
        most_recent_odds_recorded_at=freshness["most_recent_odds_recorded_at"],
        bookmakers_seen=freshness["bookmakers_seen"],
        scheduled_matches_with_odds=freshness["scheduled_matches_with_odds"],
    )
