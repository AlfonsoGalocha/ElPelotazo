from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
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


def test_same_fixture_from_two_providers_does_not_duplicate():
    """Regresion de arquitectura: un partido ingerido primero como
    'scheduled' (fixtures futuros, p.ej. openfootball) y luego, una vez
    jugado, ingerido de nuevo como 'finished' via el dataset historico con
    un provider_id DISTINTO, no debe crear una fila duplicada -- la fila
    programada original se quedaria huerfana para siempre.

    Usa `session_scope()` (no el fixture `db_session`) porque
    `ingest_matches` hace `commit()` internamente."""
    from backend.app.normalization.competitions import COMPETITIONS

    competition_code = "dedup_test_league"
    COMPETITIONS.setdefault(competition_code, ("Dedup Test League", "Testland"))
    scheduled_record = RawMatchRecord(
        provider="openfootball_fixtures",
        provider_id="dedup-test:2025/26:2025-09-20:Dedup Home:Dedup Away:Matchday 5",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="Dedup Home",
        away_team_raw="Dedup Away",
        home_goals=None,
        away_goals=None,
        matchday=5,
    )
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("openfootball_fixtures", scheduled_record), competition_code, "2025/26")

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=competition_code).one()
        scheduled = db.query(Match).filter(Match.competition_id == competition.id).all()
        assert len(scheduled) == 1
        assert scheduled[0].status == "scheduled"
        assert scheduled[0].matchday == 5
        original_id = scheduled[0].id

    finished_record = RawMatchRecord(
        provider="club_football_match_data",
        provider_id=f"{competition_code}:2025/26:2025-09-20:Dedup Home:Dedup Away",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="Dedup Home",
        away_team_raw="Dedup Away",
        home_goals=2,
        away_goals=1,
        matchday=None,  # el dataset historico nunca trae jornada
    )
    with session_scope() as db:
        ingest_matches(
            db, _SingleRecordProvider("club_football_match_data", finished_record), competition_code, "2025/26"
        )

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=competition_code).one()
        all_matches = db.query(Match).filter(Match.competition_id == competition.id).all()
        assert len(all_matches) == 1  # NO se duplico
        match = all_matches[0]
        assert match.id == original_id  # misma fila (predicciones/cuotas asociadas se conservan)
        assert match.status == "finished"
        assert match.home_goals == 2
        assert match.away_goals == 1
        assert match.matchday == 5  # se conserva del fixture original, la fuente nueva no trae uno
        assert match.provider == "club_football_match_data"  # se adopta la fuente mas completa


def test_absorbing_a_duplicate_with_an_offset_aware_date_does_not_crash():
    """Bug real reportado por el usuario: API-Football da `fixture.date`
    CON offset (p.ej. '...+00:00'), a diferencia de openfootball/el
    dataset historico (sin offset) -- comparar ese datetime "offset-aware"
    contra el `kickoff_utc` ya guardado (siempre naive) lanzaba
    `TypeError: can't subtract offset-naive and offset-aware datetimes` en
    el primer partido con un duplicado absorbible, abortando TODA la
    ingesta de esa competicion desde el primer fixture -- exactamente el
    caso real al migrar una liga que ya tenia partidos de openfootball."""
    import datetime as dt

    from backend.app.normalization.competitions import COMPETITIONS

    competition_code = "tz_dedup_test_league"
    COMPETITIONS.setdefault(competition_code, ("TZ Dedup Test League", "Testland"))
    scheduled_record = RawMatchRecord(
        provider="openfootball_fixtures",
        provider_id="tz-dedup-test:2025/26:2025-09-20:TZ Home:TZ Away:Matchday 5",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="TZ Home",
        away_team_raw="TZ Away",
        home_goals=None,
        away_goals=None,
        matchday=5,
    )
    with session_scope() as db:
        ingest_matches(
            db, _SingleRecordProvider("openfootball_fixtures", scheduled_record), competition_code, "2025/26"
        )

    api_football_record = RawMatchRecord(
        provider="api_football",
        provider_id="api-football-tz-dedup-1",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00+00:00",  # CON offset, como da API-Football de verdad
        home_team_raw="TZ Home",
        away_team_raw="TZ Away",
        home_goals=2,
        away_goals=1,
        matchday=5,
    )
    with session_scope() as db:
        n = ingest_matches(
            db, _SingleRecordProvider("api_football", api_football_record), competition_code, "2025/26"
        )
        assert n == 1  # no lanza TypeError

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=competition_code).one()
        all_matches = db.query(Match).filter(Match.competition_id == competition.id).all()
        assert len(all_matches) == 1  # absorbido, no duplicado
        match = all_matches[0]
        assert match.status == "finished"
        assert match.kickoff_utc.tzinfo is None  # guardado naive, como el resto de la BD
        assert match.kickoff_utc == dt.datetime(2025, 9, 20, 20, 0, 0)
