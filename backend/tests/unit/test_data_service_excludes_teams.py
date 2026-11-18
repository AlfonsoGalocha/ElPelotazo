"""`ingest_matches` nunca persiste un partido donde aparezca un equipo
excluido (`normalization/teams.py::EXCLUDED_TEAMS`) -- comprobado ANTES
de guardar nada, igual que ya pasaba con las casas de apuestas excluidas
(`normalization/bookmakers.py::EXCLUDED_BOOKMAKERS`). Motivo real
(seccion "Selecciones", 2026-11): Russia esta suspendida de UEFA/FIFA, asi
que sus fixtures nunca se van a jugar aunque la fuente los siga
devolviendo; el purgado de lo YA ingerido vive en
`scripts/purge_excluded_team_matches.py` (ver su test dedicado), este
test cubre que no vuelvan a colarse en una ingesta NUEVA."""

from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches


class _TwoRecordProvider(DataProvider):
    name = "test"

    def __init__(self, records: list[RawMatchRecord]):
        self._records = records

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return self._records


def test_ingest_matches_skips_a_fixture_with_an_excluded_team_as_home_or_away():
    competition_code = "excluded-team-ingest-test-league"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))

    excluded_as_home = RawMatchRecord(
        provider="test",
        provider_id="excluded-team-test-home",
        competition_code=competition_code,
        season_label="2026/27",
        date="2026-11-17T19:45:00",
        home_team_raw="Russia",
        away_team_raw="Spain",
    )
    excluded_as_away = RawMatchRecord(
        provider="test",
        provider_id="excluded-team-test-away",
        competition_code=competition_code,
        season_label="2026/27",
        date="2026-11-17T19:45:00",
        home_team_raw="Portugal",
        away_team_raw="Russia",
    )
    # Mismo nombre con mayusculas/espacios distintos -- el filtro normaliza
    # igual que `is_excluded_bookmaker`, nunca depende de coincidencia
    # exacta de mayusculas.
    excluded_case_insensitive = RawMatchRecord(
        provider="test",
        provider_id="excluded-team-test-case",
        competition_code=competition_code,
        season_label="2026/27",
        date="2026-11-17T19:45:00",
        home_team_raw="  RUSSIA  ",
        away_team_raw="Wales",
    )
    allowed = RawMatchRecord(
        provider="test",
        provider_id="excluded-team-test-allowed",
        competition_code=competition_code,
        season_label="2026/27",
        date="2026-11-17T19:45:00",
        home_team_raw="Spain",
        away_team_raw="Portugal",
    )

    provider = _TwoRecordProvider(
        [excluded_as_home, excluded_as_away, excluded_case_insensitive, allowed]
    )

    with session_scope() as db:
        count = ingest_matches(db, provider, competition_code, "2026/27")

    assert count == 1  # solo `allowed` se persiste

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=competition_code).one()
        matches = db.query(Match).filter(Match.competition_id == competition.id).all()
        assert len(matches) == 1
        assert matches[0].provider_id == "excluded-team-test-allowed"
