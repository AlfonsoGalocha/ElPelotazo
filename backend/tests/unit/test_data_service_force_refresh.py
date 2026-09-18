from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches


class _ProviderWithCache(DataProvider):
    """Simula `ClubFootballMatchDataProvider`: tiene `refresh_cache()`."""

    name = "fake_cached_provider"

    def __init__(self) -> None:
        self.refresh_cache_calls = 0

    def is_available(self) -> bool:
        return True

    def refresh_cache(self) -> None:
        self.refresh_cache_calls += 1

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return []


class _ProviderWithoutCache(DataProvider):
    """Simula un proveedor SIN cache (p.ej. openfootball, football-data.co.uk
    directo): no tiene `refresh_cache()` en absoluto."""

    name = "fake_uncached_provider"

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return []


def test_force_refresh_calls_refresh_cache_when_provider_supports_it():
    COMPETITIONS.setdefault("force_refresh_test_league", ("Force Refresh Test League", "Testland"))
    provider = _ProviderWithCache()
    with session_scope() as db:
        ingest_matches(db, provider, "force_refresh_test_league", "2025/26", force_refresh=True)
    assert provider.refresh_cache_calls == 1


def test_force_refresh_false_never_calls_refresh_cache():
    COMPETITIONS.setdefault("force_refresh_test_league_2", ("Force Refresh Test League 2", "Testland"))
    provider = _ProviderWithCache()
    with session_scope() as db:
        ingest_matches(db, provider, "force_refresh_test_league_2", "2025/26", force_refresh=False)
    assert provider.refresh_cache_calls == 0


def test_force_refresh_is_a_noop_for_providers_without_a_cache():
    """Bug real corregido: `force_refresh=True` no debe romper proveedores
    que nunca tuvieron cache (openfootball, football-data.co.uk directo...) --
    solo actua si el proveedor expone `refresh_cache()`."""
    COMPETITIONS.setdefault("force_refresh_test_league_3", ("Force Refresh Test League 3", "Testland"))
    provider = _ProviderWithoutCache()
    with session_scope() as db:
        ingest_matches(db, provider, "force_refresh_test_league_3", "2025/26", force_refresh=True)
