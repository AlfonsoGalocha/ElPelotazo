"""Tests de `ranking_config_service.py`: la preferencia editable en
runtime para la banda de cuotas de "Mejores oportunidades" (seccion 2/7
del pedido de cambio de filosofia del ranking -- "Anhade un filtro de
cuotas configurable en Configuracion"). Fila UNICA en `RankingConfig`
(`id=1`), creada sola en el primer acceso."""

from __future__ import annotations

import pytest

from backend.app.config.settings import Settings
from backend.app.db.models.core import RankingConfig
from backend.app.services.ranking_config_service import (
    get_opportunity_odds_band,
    set_opportunity_odds_band,
)


def test_get_opportunity_odds_band_defaults_to_settings_when_nothing_was_saved(db_session):
    # `RankingConfig` es una fila UNICA compartida por TODA la BD de test
    # (incluida la real, no transaccional, que usan los tests de
    # integracion vía `/config/ranking`) -- sin este borrado explicito,
    # una fila guardada por otro modulo en una ejecucion de la suite
    # COMPLETA haria que este test leyera esa preferencia en vez de "nada
    # guardado todavia" (mismo patron defensivo que el test de abajo).
    db_session.query(RankingConfig).delete()
    settings = Settings(min_opportunity_odds=1.35, max_opportunity_odds=1.90)
    min_odds, max_odds = get_opportunity_odds_band(db_session, settings)
    assert (min_odds, max_odds) == (1.35, 1.90)


def test_get_opportunity_odds_band_does_not_write_defaults(db_session):
    db_session.query(RankingConfig).delete()
    get_opportunity_odds_band(db_session)
    get_opportunity_odds_band(db_session)
    assert db_session.query(RankingConfig).count() == 0


def test_set_opportunity_odds_band_persists_and_is_read_back(db_session):
    set_opportunity_odds_band(db_session, 1.40, 2.20)
    min_odds, max_odds = get_opportunity_odds_band(db_session)
    assert (min_odds, max_odds) == (1.40, 2.20)


def test_set_opportunity_odds_band_overrides_settings_defaults(db_session):
    """Tras guardar una preferencia, `get_opportunity_odds_band` debe
    devolver la preferencia guardada, nunca el default de `Settings`,
    aunque se le pase una instancia de `Settings` distinta."""
    set_opportunity_odds_band(db_session, 1.50, 2.00)
    settings = Settings(min_opportunity_odds=1.35, max_opportunity_odds=1.90)
    min_odds, max_odds = get_opportunity_odds_band(db_session, settings)
    assert (min_odds, max_odds) == (1.50, 2.00)


def test_set_opportunity_odds_band_rejects_min_odds_at_or_below_one(db_session):
    with pytest.raises(ValueError):
        set_opportunity_odds_band(db_session, 1.0, 1.90)
    with pytest.raises(ValueError):
        set_opportunity_odds_band(db_session, 0.90, 1.90)


def test_set_opportunity_odds_band_rejects_max_odds_at_or_below_min_odds(db_session):
    with pytest.raises(ValueError):
        set_opportunity_odds_band(db_session, 1.50, 1.50)
    with pytest.raises(ValueError):
        set_opportunity_odds_band(db_session, 1.50, 1.40)


def test_set_opportunity_odds_band_rejection_never_persists_an_invalid_value(db_session):
    """Una llamada invalida no debe dejar la fila a medio escribir (seccion
    2: "configurable", nunca un estado inconsistente en BD)."""
    set_opportunity_odds_band(db_session, 1.40, 2.00)
    with pytest.raises(ValueError):
        set_opportunity_odds_band(db_session, 3.00, 1.50)
    min_odds, max_odds = get_opportunity_odds_band(db_session)
    assert (min_odds, max_odds) == (1.40, 2.00)
