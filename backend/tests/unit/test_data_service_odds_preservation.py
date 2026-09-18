from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.services.data_service import ingest_matches


class _SingleRecordProvider(DataProvider):
    def __init__(self, name: str, record: RawMatchRecord):
        self.name = name
        self._record = record

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return [self._record]


def test_reingesting_a_fixture_without_odds_does_not_wipe_existing_odds():
    """Bug real detectado: `refresh` reingiere el calendario
    (`update-fixtures`) en CADA ejecucion, y ese fixture provider NUNCA
    trae cuotas (`record.odds` siempre []). Antes de este fix, cada
    reingesta borraba las cuotas reales ya conseguidas por
    `attach_odds_to_scheduled_matches` en una ejecucion anterior, y solo
    sobrevivian si el intento de re-casarlas justo despues (en el mismo
    refresh) volvia a tener exito para ese partido exacto -- cualquier
    fallo parcial y la cuota desaparecia sin que nadie lo notara."""
    competition_code = "odds_preservation_test_league"
    from backend.app.normalization.competitions import COMPETITIONS

    COMPETITIONS.setdefault(competition_code, ("Odds Preservation Test League", "Testland"))

    fixture_record = RawMatchRecord(
        provider="openfootball_fixtures",
        provider_id="odds-preservation-test:2025/26:2025-09-20:Home:Away:Matchday 5",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="OddsPreservationHome",
        away_team_raw="OddsPreservationAway",
        home_goals=None,
        away_goals=None,
        matchday=5,
    )
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("openfootball_fixtures", fixture_record), competition_code, "2025/26")

    # Simula lo que hace attach_odds_to_scheduled_matches: anhade cuotas
    # reales de la API de odds al partido ya existente.
    with session_scope() as db:
        match = db.query(Match).filter_by(provider_id=fixture_record.provider_id).one()
        db.add(
            MatchOdds(
                match_id=match.id,
                bookmaker="bet365",
                market="over_under_goals",
                line=2.5,
                selection="over",
                price=1.85,
                snapshot_type="pre_match",
                recorded_at=dt.datetime.utcnow(),
            )
        )
        match_id = match.id

    # Se vuelve a ingerir el MISMO fixture (como haria `update-fixtures` en
    # cada `refresh`), sin ninguna cuota (record.odds sigue vacio).
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("openfootball_fixtures", fixture_record), competition_code, "2025/26")

    with session_scope() as db:
        odds = db.query(MatchOdds).filter_by(match_id=match_id).all()
        assert len(odds) == 1  # la cuota real sigue ahi, no se borro
        assert odds[0].price == 1.85


def test_reingesting_a_source_that_does_carry_odds_still_refreshes_them():
    """El fix no debe romper el caso normal: una fuente que SI trae cuotas
    (el dataset historico) sigue reemplazando las cuotas viejas por las
    nuevas al reingerir."""
    competition_code = "odds_refresh_test_league"
    from backend.app.normalization.competitions import COMPETITIONS
    from backend.app.ingestion.base import RawOddsRecord

    COMPETITIONS.setdefault(competition_code, ("Odds Refresh Test League", "Testland"))

    def make_record(odds_price: float) -> RawMatchRecord:
        return RawMatchRecord(
            provider="club_football_match_data",
            provider_id="odds-refresh-test:2025/26:2025-09-20:Home:Away",
            competition_code=competition_code,
            season_label="2025/26",
            date="2025-09-20T20:00:00",
            home_team_raw="OddsRefreshHome",
            away_team_raw="OddsRefreshAway",
            home_goals=2,
            away_goals=1,
            odds=[RawOddsRecord("bet365", "over_under_goals", 2.5, "over", odds_price)],
        )

    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("club_football_match_data", make_record(1.85)), competition_code, "2025/26")

    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("club_football_match_data", make_record(1.95)), competition_code, "2025/26")

    with session_scope() as db:
        match = db.query(Match).filter_by(provider_id="odds-refresh-test:2025/26:2025-09-20:Home:Away").one()
        odds = db.query(MatchOdds).filter_by(match_id=match.id).all()
        assert len(odds) == 1
        assert odds[0].price == 1.95  # se reemplazo por la nueva, no se acumulo
