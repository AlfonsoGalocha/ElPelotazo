from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, ProviderResponse
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches
from backend.app.services.provider_response_service import archive_raw_responses


class _FakeApiFootballProvider(DataProvider):
    """Simula un proveedor API-Football real: mismo `name` y mismo
    `last_raw_responses` que `ApiFootballClient` expone de verdad (ver
    client.py) -- sin tener que levantar HTTP real."""

    name = "api_football"

    def __init__(self, raw_responses: list[dict]):
        self.last_raw_responses = raw_responses

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return [
            RawMatchRecord(
                provider="api_football",
                provider_id="provider-response-test-1",
                competition_code=competition_code,
                season_label=season_label,
                date="2025-09-20T20:00:00",
                home_team_raw="PRHome",
                away_team_raw="PRAway",
                home_goals=1,
                away_goals=0,
            )
        ]


def test_ingest_matches_archives_raw_api_football_responses_and_clears_the_buffer():
    code = "provider_response_test_league"
    COMPETITIONS.setdefault(code, ("Provider Response Test League", "Testland"))
    raw_responses = [
        {"path": "/fixtures", "params": {"league": 140, "season": 2025}, "response": {"response": ["fixture-a"]}},
    ]
    provider = _FakeApiFootballProvider(raw_responses)

    with session_scope() as db:
        ingest_matches(db, provider, code, "2025/26", operation="update_fixtures")

    # El buffer del proveedor se vacia tras archivar -- si se reutilizara
    # la misma instancia para otra competicion, nunca debe re-archivar lo
    # de esta.
    assert provider.last_raw_responses == []

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=code).one()
        rows = db.query(ProviderResponse).filter_by(competition_code=code).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.provider == "api_football"
        assert row.operation == "update_fixtures"
        assert row.path == "/fixtures"
        assert row.params == {"league": 140, "season": 2025}
        assert row.response == {"response": ["fixture-a"]}
        assert row.fetched_at is not None
        assert competition.code == code  # el partido si se ingirio normalmente


def test_providers_without_raw_responses_attribute_are_never_archived():
    """Un proveedor sin HTTP real (CSV, openfootball...) no tiene
    `last_raw_responses` -- `ingest_matches` no debe fallar ni crear nada."""
    code = "provider_response_test_league_csv"
    COMPETITIONS.setdefault(code, ("Provider Response Test League CSV", "Testland"))

    class _CsvLikeProvider(DataProvider):
        name = "openfootball_fixtures"

        def is_available(self) -> bool:
            return True

        def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
            return []

    with session_scope() as db:
        ingest_matches(db, _CsvLikeProvider(), code, "2025/26")

    with session_scope() as db:
        rows = db.query(ProviderResponse).filter_by(competition_code=code).all()
        assert rows == []


def test_archive_raw_responses_does_nothing_with_an_empty_list():
    with session_scope() as db:
        n = archive_raw_responses(db, "api_football", "update_odds", "any_competition", [])
        assert n == 0
