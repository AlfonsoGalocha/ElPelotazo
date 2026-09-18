from __future__ import annotations

from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider


def _odds_response(bet_name: str, values: list[dict], bookmaker: str = "Bet365") -> list[dict]:
    return [
        {
            "bookmakers": [
                {
                    "name": bookmaker,
                    "bets": [{"name": bet_name, "values": values}],
                }
            ]
        }
    ]


def test_parses_cards_market_by_substring_not_exact_name():
    """Sin verificacion end-to-end contra la API real: se busca por
    SUBCADENA ("card" en minusculas) porque el nombre exacto del mercado
    puede variar ligeramente entre bookmakers/version de la API."""
    response = _odds_response(
        "Cards Over/Under",
        [{"value": "Over 4.5", "odd": "1.90"}, {"value": "Under 4.5", "odd": "1.85"}],
    )
    records, seen = ApiFootballOddsProvider._parse_odds_response(response)
    assert seen == {"Cards Over/Under"}
    by_selection = {r.selection: (r.market, r.line, r.price) for r in records}
    assert by_selection["over"] == ("cards_total", 4.5, 1.90)
    assert by_selection["under"] == ("cards_total", 4.5, 1.85)


def test_parses_corners_market_case_insensitively():
    response = _odds_response(
        "TOTAL CORNERS", [{"value": "Over 9.5", "odd": "1.95"}]
    )
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    assert len(records) == 1
    assert records[0].market == "corners_total"
    assert records[0].line == 9.5
    assert records[0].selection == "over"


def test_ignores_unrelated_markets():
    response = _odds_response("Match Winner", [{"value": "Home", "odd": "1.50"}])
    records, seen = ApiFootballOddsProvider._parse_odds_response(response)
    assert records == []
    assert seen == {"Match Winner"}


def test_ignores_values_that_dont_match_over_under_pattern():
    response = _odds_response("Cards Over/Under", [{"value": "Yes", "odd": "1.50"}])
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    assert records == []


def test_league_ids_cover_all_five_mvp_leagues():
    from backend.app.ingestion.api_football.odds_provider import LEAGUE_IDS

    assert set(LEAGUE_IDS) == {"laliga", "premier_league", "bundesliga", "serie_a", "ligue_1"}
    assert all(isinstance(v, int) for v in LEAGUE_IDS.values())
