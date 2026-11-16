"""Pedido real del usuario: escudos REALES de equipo via API-Football (el
proyecto tiene key de pago activa), capturados gratis del mismo payload
que ya trae cada fixture -- nunca una peticion/coste adicional, nunca un
escudo inventado. Ver `Team.logo_url` (db/models/core.py),
`RawMatchRecord.home_logo_url`/`away_logo_url` (ingestion/base.py) y
`_update_team_logo` (services/data_service.py)."""

from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.core import Team
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


def test_ingesting_api_football_fixture_saves_real_team_crest_urls():
    competition_code = "team_logo_test_league"
    COMPETITIONS.setdefault(competition_code, ("Team Logo Test League", "Testland"))

    record = RawMatchRecord(
        provider="api_football",
        provider_id="api-football-logo-1",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="TeamLogoHome",
        away_team_raw="TeamLogoAway",
        home_logo_url="https://media.api-sports.io/football/teams/541.png",
        away_logo_url="https://media.api-sports.io/football/teams/529.png",
    )
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("api_football", record), competition_code, "2025/26")

    with session_scope() as db:
        home = db.query(Team).filter_by(canonical_name="TeamLogoHome").one()
        away = db.query(Team).filter_by(canonical_name="TeamLogoAway").one()
        assert home.logo_url == "https://media.api-sports.io/football/teams/541.png"
        assert away.logo_url == "https://media.api-sports.io/football/teams/529.png"


def test_reingesting_from_a_source_without_crest_does_not_wipe_existing_one():
    competition_code = "team_logo_preservation_test_league"
    COMPETITIONS.setdefault(competition_code, ("Team Logo Preservation Test League", "Testland"))

    with_logo = RawMatchRecord(
        provider="api_football",
        provider_id="api-football-logo-2",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="LogoPreservedHome",
        away_team_raw="LogoPreservedAway",
        home_logo_url="https://media.api-sports.io/football/teams/541.png",
        away_logo_url="https://media.api-sports.io/football/teams/529.png",
    )
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("api_football", with_logo), competition_code, "2025/26")

    # Misma pareja equipo/fecha, otra fuente que NUNCA trae escudo
    # (football-data.co.uk/openfootball) -- se absorbe como el mismo
    # partido; el escudo real ya guardado no debe borrarse.
    without_logo = RawMatchRecord(
        provider="club_football_match_data",
        provider_id="team-logo-preservation-csv:2025/26:2025-09-20:Home:Away",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="LogoPreservedHome",
        away_team_raw="LogoPreservedAway",
        home_goals=2,
        away_goals=1,
    )
    with session_scope() as db:
        ingest_matches(
            db, _SingleRecordProvider("club_football_match_data", without_logo), competition_code, "2025/26"
        )

    with session_scope() as db:
        home = db.query(Team).filter_by(canonical_name="LogoPreservedHome").one()
        assert home.logo_url == "https://media.api-sports.io/football/teams/541.png"


def test_a_team_never_seen_with_a_real_crest_keeps_logo_url_none():
    competition_code = "team_logo_absent_test_league"
    COMPETITIONS.setdefault(competition_code, ("Team Logo Absent Test League", "Testland"))

    record = RawMatchRecord(
        provider="club_football_match_data",
        provider_id="team-logo-absent-csv",
        competition_code=competition_code,
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw="NoLogoHome",
        away_team_raw="NoLogoAway",
        home_goals=0,
        away_goals=0,
    )
    with session_scope() as db:
        ingest_matches(db, _SingleRecordProvider("club_football_match_data", record), competition_code, "2025/26")

    with session_scope() as db:
        home = db.query(Team).filter_by(canonical_name="NoLogoHome").one()
        assert home.logo_url is None
