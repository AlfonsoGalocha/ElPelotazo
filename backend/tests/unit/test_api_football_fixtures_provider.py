from __future__ import annotations

import pytest

from backend.app.ingestion.api_football.fixtures_provider import (
    ApiFootballMatchProvider,
    parse_matchday,
)


def test_parse_matchday_handles_regular_season_format():
    assert parse_matchday("Regular Season - 12") == 12


def test_parse_matchday_handles_bare_round_format():
    assert parse_matchday("Round 7") == 7


def test_parse_matchday_returns_none_for_unrecognized_format():
    assert parse_matchday("Quarter-finals") is None
    assert parse_matchday("") is None


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class _FakeHttpClient:
    def __init__(self, payload: dict):
        self._payload = payload

    def get(self, url: str, params: dict, headers: dict) -> _FakeResponse:
        return _FakeResponse(self._payload)


def _fixture(
    fixture_id: int,
    status: str,
    home: str = "Team A",
    away: str = "Team B",
    home_goals=None,
    away_goals=None,
    round_name: str = "Regular Season - 10",
):
    return {
        "fixture": {
            "id": fixture_id,
            "date": "2026-01-10T20:00:00+00:00",
            "status": {"short": status},
        },
        "teams": {"home": {"name": home}, "away": {"name": away}},
        "goals": {"home": home_goals, "away": away_goals},
        "score": {"halftime": {"home": None, "away": None}},
        "league": {"round": round_name},
    }


def test_fetch_matches_marks_finished_fixtures_with_real_score():
    payload = {"response": [_fixture(1, "FT", home_goals=2, away_goals=1)], "errors": []}
    provider = ApiFootballMatchProvider(http_client=_FakeHttpClient(payload))

    records = provider.fetch_matches("laliga", "2025/26")

    assert len(records) == 1
    record = records[0]
    assert record.home_goals == 2
    assert record.away_goals == 1
    assert record.matchday == 10
    assert record.provider == "api_football"
    assert record.provider_id == "api-football-1"


def test_fetch_matches_never_fabricates_a_score_for_an_unplayed_fixture():
    payload = {"response": [_fixture(2, "NS", home_goals=None, away_goals=None)], "errors": []}
    provider = ApiFootballMatchProvider(http_client=_FakeHttpClient(payload))

    records = provider.fetch_matches("laliga", "2025/26")

    assert records[0].home_goals is None
    assert records[0].away_goals is None


def test_fetch_matches_never_uses_goals_for_statuses_other_than_finished():
    """Si por lo que sea la API devolviera goals no nulos en un partido
    "in play" (1H/2H), NUNCA se debe tratar como resultado final -- solo
    FT/AET/PEN cuentan como terminado."""
    payload = {"response": [_fixture(3, "2H", home_goals=1, away_goals=0)], "errors": []}
    provider = ApiFootballMatchProvider(http_client=_FakeHttpClient(payload))

    records = provider.fetch_matches("laliga", "2025/26")

    assert records[0].home_goals is None
    assert records[0].away_goals is None


def test_fetch_matches_logs_warning_when_round_format_unrecognized(caplog):
    payload = {"response": [_fixture(4, "NS", round_name="Quarter-finals")], "errors": []}
    provider = ApiFootballMatchProvider(http_client=_FakeHttpClient(payload))

    with caplog.at_level("WARNING"):
        records = provider.fetch_matches("laliga", "2025/26")

    assert records[0].matchday is None
    assert any("unrecognized_round_format" in r.getMessage() for r in caplog.records)


def test_fetch_matches_raises_for_unsupported_competition():
    provider = ApiFootballMatchProvider(http_client=_FakeHttpClient({"response": [], "errors": []}))
    with pytest.raises(ValueError):
        provider.fetch_matches("not-a-real-league", "2025/26")
