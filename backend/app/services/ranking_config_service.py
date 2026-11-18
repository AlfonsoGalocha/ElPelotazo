"""Preferencia de usuario editable en runtime para la banda de cuotas de
"Mejores oportunidades" (ver db/models/core.py::RankingConfig). Fila UNICA
(siempre `id=1`): se crea sola, con los valores por defecto de `Settings`,
en el primer GET/PUT -- nunca hace falta una migracion de datos a mano.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.config.settings import Settings, get_settings
from backend.app.db.models.core import RankingConfig

RANKING_CONFIG_ID = 1


def _get_or_create(db: Session) -> RankingConfig:
    config = db.get(RankingConfig, RANKING_CONFIG_ID)
    if config is None:
        config = RankingConfig(id=RANKING_CONFIG_ID, min_opportunity_odds=None, max_opportunity_odds=None)
        db.add(config)
        db.flush()
    return config


def get_opportunity_odds_band(db: Session, settings: Settings | None = None) -> tuple[float, float]:
    """(min, max) vigentes: la preferencia guardada si existe, si no los
    valores por defecto de `Settings` -- nunca None, nunca inventado."""
    settings = settings or get_settings()
    config = _get_or_create(db)
    min_odds = config.min_opportunity_odds if config.min_opportunity_odds is not None else settings.min_opportunity_odds
    max_odds = config.max_opportunity_odds if config.max_opportunity_odds is not None else settings.max_opportunity_odds
    return min_odds, max_odds


def set_opportunity_odds_band(db: Session, min_odds: float, max_odds: float) -> tuple[float, float]:
    if min_odds <= 1.0:
        raise ValueError("min_odds debe ser mayor que 1.0 (una cuota no paga menos que el stake)")
    if max_odds <= min_odds:
        raise ValueError("max_odds debe ser mayor que min_odds")
    config = _get_or_create(db)
    config.min_opportunity_odds = min_odds
    config.max_opportunity_odds = max_odds
    db.flush()
    return min_odds, max_odds
