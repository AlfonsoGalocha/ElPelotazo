from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.standings import StandingsSnapshot
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches
from backend.app.services.standings_service import (
    compute_current_standings,
    latest_standings_snapshot,
    snapshot_standings,
)


class _FixedRecordsProvider(DataProvider):
    name = "standings_test_provider"

    def __init__(self, records: list[RawMatchRecord]):
        self._records = records

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return self._records


def _record(provider_id: str, home: str, away: str, hg: int | None, ag: int | None, matchday: int | None) -> RawMatchRecord:
    return RawMatchRecord(
        provider="standings_test_provider",
        provider_id=provider_id,
        competition_code="standings_test_league",
        season_label="2025/26",
        date="2025-09-20T20:00:00",
        home_team_raw=home,
        away_team_raw=away,
        home_goals=hg,
        away_goals=ag,
        matchday=matchday,
    )


def _setup_competition(suffix: str) -> str:
    code = f"standings_test_league_{suffix}"
    COMPETITIONS.setdefault(code, ("Standings Test League", "Testland"))
    return code


def test_compute_current_standings_ranks_by_points_then_gd_then_gf():
    code = _setup_competition("rank")
    records = [
        # A 3-0 B (A: 3pts, GD+3), B 1-1 C (B: 1pt, C: 1pt), A 2-2 C (A: 1pt GD+0 total, C: 1pt)
        _record("r1", "TeamA", "TeamB", 3, 0, 1),
        _record("r2", "TeamB", "TeamC", 1, 1, 1),
        _record("r3", "TeamA", "TeamC", 2, 2, 2),
    ]
    with session_scope() as db:
        ingest_matches(db, _FixedRecordsProvider(records), code, "2025/26")

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=code).one()
        season = db.query(Season).filter_by(competition_id=competition.id, label="2025/26").one()
        rows, matchday_ceiling = compute_current_standings(db, competition.id, season.id)

    # TeamA: 2 jugados, 1V 1E, GF5 GC2, GD+3, 4pts -- primero por puntos.
    # TeamB: 2 jugados, 0V 1E 1D, GF1 GC4, GD-3, 1pt.
    # TeamC: 2 jugados, 0V 2E, GF3 GC3, GD0, 2pts -- segundo (2pts > 1pt de B).
    assert [r["points"] for r in rows] == [4, 2, 1]
    by_team = {r["team_id"]: r for r in rows}
    team_a = next(r for r in rows if r["position"] == 1)
    assert team_a["points"] == 4
    assert team_a["played"] == 2
    assert team_a["won"] == 1
    assert team_a["drawn"] == 1
    assert team_a["goals_for"] == 5
    assert team_a["goals_against"] == 2
    assert team_a["goal_difference"] == 3
    assert matchday_ceiling == 2  # el partido r3 es jornada 2, la mas alta vista
    assert len(by_team) == 3


def test_matchday_ceiling_is_none_when_any_finished_match_has_no_matchday():
    code = _setup_competition("ceiling")
    records = [
        _record("c1", "TeamX", "TeamY", 1, 0, 1),
        _record("c2", "TeamX", "TeamZ", 0, 0, None),  # fuente historica sin jornada
    ]
    with session_scope() as db:
        ingest_matches(db, _FixedRecordsProvider(records), code, "2025/26")

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=code).one()
        season = db.query(Season).filter_by(competition_id=competition.id, label="2025/26").one()
        _, matchday_ceiling = compute_current_standings(db, competition.id, season.id)

    assert matchday_ceiling is None  # nunca se inventa un techo cuando falta el dato real


def test_compute_current_standings_ignores_scheduled_matches_without_score():
    code = _setup_competition("pending")
    records = [
        _record("p1", "TeamM", "TeamN", 2, 1, 1),
        # Fecha distinta y rival distinto -- nunca el mismo partido real
        # que `p1` (si no, `_find_absorbable_duplicate` los fusionaria).
        RawMatchRecord(
            provider="standings_test_provider",
            provider_id="p2",
            competition_code=code,
            season_label="2025/26",
            date="2025-10-04T20:00:00",
            home_team_raw="TeamM",
            away_team_raw="TeamO",
            home_goals=None,
            away_goals=None,
            matchday=2,
        ),
    ]
    with session_scope() as db:
        ingest_matches(db, _FixedRecordsProvider(records), code, "2025/26")

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=code).one()
        season = db.query(Season).filter_by(competition_id=competition.id, label="2025/26").one()
        rows, _ = compute_current_standings(db, competition.id, season.id)

    team_m = next(r for r in rows if r["won"] == 1)
    assert team_m["played"] == 1  # el partido sin marcador no cuenta


def test_snapshot_standings_is_append_only_across_two_calls():
    code = _setup_competition("appendonly")
    with session_scope() as db:
        ingest_matches(db, _FixedRecordsProvider([_record("a1", "TeamP", "TeamQ", 1, 0, 1)]), code, "2025/26")

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=code).one()
        season = db.query(Season).filter_by(competition_id=competition.id, label="2025/26").one()
        before = db.query(StandingsSnapshot).filter_by(competition_id=competition.id, season_id=season.id).count()

    # Un segundo resultado real llega (p.ej. otro `update-fixtures`) -- debe
    # sumar un snapshot NUEVO, nunca sustituir el anterior.
    with session_scope() as db:
        ingest_matches(
            db,
            _FixedRecordsProvider([_record("a2", "TeamP", "TeamR", 0, 0, 2)]),
            code,
            "2025/26",
        )

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=code).one()
        season = db.query(Season).filter_by(competition_id=competition.id, label="2025/26").one()
        after = db.query(StandingsSnapshot).filter_by(competition_id=competition.id, season_id=season.id).count()
        distinct_as_of = {
            row.as_of
            for row in db.query(StandingsSnapshot).filter_by(competition_id=competition.id, season_id=season.id)
        }

    assert after > before  # nunca se borraron las filas del primer snapshot
    assert len(distinct_as_of) == 2  # dos tandas reales, cada una con su propio as_of


def test_latest_standings_snapshot_returns_only_the_most_recent_batch_ordered_by_position():
    code = _setup_competition("latest")
    with session_scope() as db:
        ingest_matches(db, _FixedRecordsProvider([_record("l1", "TeamS", "TeamT", 2, 0, 1)]), code, "2025/26")
        ingest_matches(
            db,
            _FixedRecordsProvider([_record("l2", "TeamS", "TeamU", 1, 1, 2)]),
            code,
            "2025/26",
        )

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=code).one()
        season = db.query(Season).filter_by(competition_id=competition.id, label="2025/26").one()
        latest = latest_standings_snapshot(db, competition.id, season.id)
        # TeamS jugo los 2 partidos (4 pts); TeamT/TeamU 1 cada uno -- la
        # ultima tanda ya debe reflejar AMBOS partidos, no solo el primero.
        assert latest[0].points == 4
        assert [row.position for row in latest] == sorted(row.position for row in latest)


def test_snapshot_standings_does_nothing_when_no_match_is_finished_yet():
    code = _setup_competition("empty")
    with session_scope() as db:
        ingest_matches(
            db,
            _FixedRecordsProvider([_record("e1", "TeamV", "TeamW", None, None, 1)]),
            code,
            "2025/26",
        )

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code=code).one()
        season = db.query(Season).filter_by(competition_id=competition.id, label="2025/26").one()
        rows_count = db.query(StandingsSnapshot).filter_by(competition_id=competition.id, season_id=season.id).count()

    assert rows_count == 0  # nunca se archiva una tabla vacia de relleno
