from __future__ import annotations

from backend.app.ingestion.odds.provider import COMPETITION_TO_SPORT_KEY, OddsApiProvider


def test_sport_keys_match_the_documented_odds_api_values():
    """Regresion: "soccer_germany_bundesliga1" (con el "1") es un
    sport_key INVALIDO en The Odds API -- el correcto es
    "soccer_germany_bundesliga" a secas ("...bundesliga2" es la 2.
    Bundesliga). Con la key invalida, la API devolvia error en cada
    peticion y Bundesliga se quedaba sin cuotas siempre mientras el resto
    de ligas funcionaba con normalidad (bug real reportado por el
    usuario). Este test fija los 5 sport_keys documentados para que un
    typo similar no pueda volver a colarse sin que un test falle."""
    assert COMPETITION_TO_SPORT_KEY == {
        "laliga": "soccer_spain_la_liga",
        "premier_league": "soccer_epl",
        "bundesliga": "soccer_germany_bundesliga",
        "serie_a": "soccer_italy_serie_a",
        "ligue_1": "soccer_france_ligue_one",
    }


def test_parse_event_extracts_h2h_and_totals_markets():
    event = {
        "home_team": "Bayern Munich",
        "away_team": "Union Berlin",
        "commence_time": "2026-09-19T18:30:00Z",
        "bookmakers": [
            {
                "key": "bet365",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Bayern Munich", "price": 1.20},
                            {"name": "Union Berlin", "price": 12.0},
                            {"name": "Draw", "price": 7.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "point": 2.5, "price": 1.60},
                            {"name": "Under", "point": 2.5, "price": 2.30},
                        ],
                    },
                ],
            }
        ],
    }
    snapshot = OddsApiProvider._parse_event(event)
    assert snapshot.home_team_raw == "Bayern Munich"
    assert snapshot.away_team_raw == "Union Berlin"

    markets = {(o.market, o.line, o.selection): o.price for o in snapshot.odds}
    assert markets[("match_result", None, "home")] == 1.20
    assert markets[("match_result", None, "away")] == 12.0
    assert markets[("match_result", None, "draw")] == 7.5
    assert markets[("over_under_goals", 2.5, "over")] == 1.60
    assert markets[("over_under_goals", 2.5, "under")] == 2.30


def test_parse_event_ignores_irrelevant_total_lines():
    """Solo interesan las lineas 1.5/2.5/3.5 (las que usan los mercados
    del MVP); otras lineas que algunas casas ofrecen (0.5, 4.5...) se
    descartan explicitamente en vez de guardarse sin usarlas."""
    event = {
        "home_team": "A",
        "away_team": "B",
        "commence_time": "2026-09-19T18:30:00Z",
        "bookmakers": [
            {
                "key": "bet365",
                "markets": [
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "point": 0.5, "price": 1.05},
                            {"name": "Under", "point": 0.5, "price": 8.0},
                            {"name": "Over", "point": 2.5, "price": 1.80},
                        ],
                    },
                ],
            }
        ],
    }
    snapshot = OddsApiProvider._parse_event(event)
    lines = {o.line for o in snapshot.odds}
    assert lines == {2.5}
