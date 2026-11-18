"""`_upsert_match` (seccion "Rendimiento del modelo", pedido real del
usuario): ahora respeta `RawMatchRecord.status` cuando la fuente lo trae
(hoy solo `api_football/fixtures_provider.py`), en vez de inferir
siempre "finished si hay goles, si no scheduled". Bug real corregido:
antes, un partido aplazado/cancelado quedaba indistinguible de uno
normal por jugar, para siempre -- sus predicciones no se liberaban
nunca (ver evaluation_service.py::void_cancelled_predictions, test
dedicado en test_void_settlement.py)."""

from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches


class _OneRecordProvider(DataProvider):
    name = "test"

    def __init__(self, record: RawMatchRecord):
        self._record = record

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return [self._record]


def _ingest_one(competition_code: str, provider_id: str, status: str | None) -> Match:
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    record = RawMatchRecord(
        provider="test",
        provider_id=provider_id,
        competition_code=competition_code,
        season_label="2026/27",
        date="2026-11-17T19:45:00",
        home_team_raw="Status Test Home",
        away_team_raw="Status Test Away",
        status=status,
    )
    with session_scope() as db:
        ingest_matches(db, _OneRecordProvider(record), competition_code, "2026/27")
    with session_scope() as db:
        match = db.query(Match).filter_by(provider="test", provider_id=provider_id).one()
        db.expunge(match)
        return match


def test_record_status_postponed_is_never_treated_as_scheduled():
    match = _ingest_one("status-test-league-postponed", "status-test-pst", status="postponed")
    assert match.status == "postponed"


def test_record_status_cancelled_is_never_treated_as_scheduled():
    match = _ingest_one("status-test-league-cancelled", "status-test-canc", status="cancelled")
    assert match.status == "cancelled"


def test_record_status_finished_is_honored_directly():
    match = _ingest_one("status-test-league-finished", "status-test-ft", status="finished")
    assert match.status == "finished"


def test_record_without_status_falls_back_to_old_goals_based_inference():
    """Fuentes que no distinguen nada mas (football-data.co.uk,
    openfootball) mandan `status=None` -- comportamiento IDENTICO al de
    antes de esta columna: sin goles, scheduled."""
    match = _ingest_one("status-test-league-legacy", "status-test-legacy", status=None)
    assert match.status == "scheduled"


def test_a_match_reset_to_scheduled_after_being_marked_postponed_is_not_stuck():
    """Un aplazamiento real suele reprogramarse bajo el MISMO fixture id
    (vuelve a "NS"/scheduled con fecha nueva) -- la siguiente ingesta debe
    poder sacarlo de "postponed" sin intervencion manual."""
    competition_code = "status-test-league-reschedule"
    provider_id = "status-test-reschedule"
    _ingest_one(competition_code, provider_id, status="postponed")
    match = _ingest_one(competition_code, provider_id, status="scheduled")
    assert match.status == "scheduled"
