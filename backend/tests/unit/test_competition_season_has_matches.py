from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import competition_season_has_matches, ingest_matches


class _SingleRecordProvider(DataProvider):
    def __init__(self, name: str, record: RawMatchRecord):
        self.name = name
        self._record = record

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return [self._record]


def test_false_for_unknown_competition():
    with session_scope() as db:
        assert competition_season_has_matches(db, "does_not_exist_at_all", "2025/26") is False


def test_false_before_ingesting_and_true_after():
    competition_code = "season_has_matches_test_league"
    COMPETITIONS.setdefault(competition_code, ("Season Has Matches Test League", "Testland"))

    with session_scope() as db:
        assert competition_season_has_matches(db, competition_code, "2024/25") is False

    record = RawMatchRecord(
        provider="test_provider",
        provider_id="season-has-matches-test:2024/25",
        competition_code=competition_code,
        season_label="2024/25",
        date="2024-09-20T20:00:00",
        home_team_raw="SeasonHasMatchesHome",
        away_team_raw="SeasonHasMatchesAway",
        home_goals=1,
        away_goals=0,
    )
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("test_provider", record), competition_code, "2024/25")

    with session_scope() as db:
        assert competition_season_has_matches(db, competition_code, "2024/25") is True
        # Otra temporada de la MISMA competicion sigue sin tener partidos.
        assert competition_season_has_matches(db, competition_code, "2023/24") is False
