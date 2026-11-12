"""Mismo bug (y mismo arreglo) que `test_data_service_odds_preservation.py`,
pero para las estadisticas del partido (tiros/corners/tarjetas/faltas):
una fuente que no trae una estadistica concreta manda `None` en ese campo
de `RawMatchRecord` (p.ej. `api_football`/`fixtures_provider.py` solo trae
goles, nunca tiros/corners/tarjetas) -- antes del fix, re-ingerir ese
partido via un duplicado absorbido borraba las estadisticas reales ya
conseguidas por OTRA fuente (p.ej. el historico CSV)."""

from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches


class _SingleRecordProvider(DataProvider):
    def __init__(self, name: str, record: RawMatchRecord):
        self.name = name
        self._record = record

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return [self._record]


def test_reingesting_a_source_without_stats_does_not_wipe_existing_stats():
    competition_code = "stats_preservation_test_league"
    COMPETITIONS.setdefault(competition_code, ("Stats Preservation Test League", "Testland"))

    rich_record = RawMatchRecord(
        provider="club_football_match_data",
        provider_id="stats-preservation-test:2025/26:2025-09-20:Home:Away",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="StatsPreservationHome",
        away_team_raw="StatsPreservationAway",
        home_goals=2,
        away_goals=1,
        home_shots=14,
        away_shots=9,
        home_corners=7,
        away_corners=3,
        home_yellow_cards=2,
        away_yellow_cards=4,
    )
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("club_football_match_data", rich_record), competition_code, "2025/26")

    # Misma pareja equipo/fecha, otra fuente (p.ej. api_football) que SOLO
    # trae el resultado -- se absorbe como el mismo partido (misma fecha,
    # mismos equipos), nunca duplicado.
    bare_record = RawMatchRecord(
        provider="api_football",
        provider_id="api-football-99999",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="StatsPreservationHome",
        away_team_raw="StatsPreservationAway",
        home_goals=2,
        away_goals=1,
    )
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("api_football", bare_record), competition_code, "2025/26")

    with session_scope() as db:
        match = db.query(Match).filter_by(provider_id="api-football-99999").one()
        stats = match.statistics
        assert stats.home_shots == 14  # no se borro aunque la nueva fuente no lo traiga
        assert stats.away_shots == 9
        assert stats.home_corners == 7
        assert stats.away_corners == 3
        assert stats.home_yellow_cards == 2
        assert stats.away_yellow_cards == 4


def test_reingesting_a_source_that_does_carry_stats_still_refreshes_them():
    competition_code = "stats_refresh_test_league"
    COMPETITIONS.setdefault(competition_code, ("Stats Refresh Test League", "Testland"))

    def make_record(home_corners: int) -> RawMatchRecord:
        return RawMatchRecord(
            provider="club_football_match_data",
            provider_id="stats-refresh-test:2025/26:2025-09-20:Home:Away",
            competition_code=competition_code,
            season_label="2025/26",
            date="2025-09-20T20:00:00",
            home_team_raw="StatsRefreshHome",
            away_team_raw="StatsRefreshAway",
            home_goals=1,
            away_goals=1,
            home_corners=home_corners,
        )

    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("club_football_match_data", make_record(5)), competition_code, "2025/26")

    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("club_football_match_data", make_record(8)), competition_code, "2025/26")

    with session_scope() as db:
        match = db.query(Match).filter_by(
            provider_id="stats-refresh-test:2025/26:2025-09-20:Home:Away"
        ).one()
        assert match.statistics.home_corners == 8  # se actualizo, no se quedo en el valor viejo
