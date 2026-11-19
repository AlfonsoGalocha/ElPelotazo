import datetime as dt

import pytest

from backend.app.db.models.core import Competition, ProviderResponse, Season
from backend.app.db.models.matches import CornerObservation, Match
from backend.app.prediction.eligibility import corner_verification
from scripts.reconcile_corners import extract_corner_total, reconcile


def response(team_id, count):
    return {"team": {"id": team_id}, "statistics": [{"type": "Corner Kicks", "value": count}]}


@pytest.mark.parametrize(
    ("entries", "total"),
    [
        ([response(1, 5), response(2, 0)], 5),
        ([response(1, 5), response(2, None)], None),
        ([response(1, 5), response(1, 6)], None),
        ([response(1, 5), response(2, -1)], None),
    ],
)
def test_corner_count_requires_two_independent_real_statistics(entries, total):
    assert extract_corner_total(entries) == total


def test_reconciliation_archives_source_and_preserves_existing_observations(db_session):
    db = db_session
    league = Competition(code="corner-reconciliation", name="Test", country="Test")
    db.add(league)
    db.flush()
    season = Season(competition_id=league.id, label="2026/27")
    db.add(season)
    db.flush()
    match = Match(
        provider="api_football", provider_id="api-football-1575216",
        competition_id=league.id, season_id=season.id, home_team_id=1, away_team_id=2,
        status="finished", kickoff_utc=dt.datetime.utcnow() - dt.timedelta(days=1),
    )
    db.add(match)
    db.flush()

    class Client:
        last_raw_responses = []
        data = [response(1, 5), response(2, 1)]

        def is_available(self):
            return True

        def _get(self, path, params):
            assert path == "/fixtures/statistics"
            assert params == {"fixture": 1575216}
            self.last_raw_responses.append({"path": path, "params": params, "response": {"response": self.data}})
            return self.data

    client = Client()
    assert reconcile(db, client, match.id) == 6
    assert db.query(CornerObservation).count() == 0
    assert reconcile(db, client, match.id, persist=True) == 6
    db.flush()
    client.data = [response(1, 6), response(2, 5)]
    assert reconcile(db, client, match.id, persist=True) == 11
    db.flush()

    observations = db.query(CornerObservation).order_by(CornerObservation.id).all()
    assert [row.total for row in observations] == [6, 11]
    assert db.get(ProviderResponse, observations[0].provider_response_id).params == {"fixture": 1575216}
    assert corner_verification("corners_over_8_5", None, True, 6) == "disputed"
    assert corner_verification("corners_over_9_5", None, True, 11) == "confirmed"
    assert corner_verification("corners_over_10_5", None, False, 11) == "disputed"
    assert corner_verification("corners_under_9_5", None, True, 6) == "confirmed"
