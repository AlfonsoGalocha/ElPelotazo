from __future__ import annotations

import httpx

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
    response = _odds_response("TOTAL CORNERS", [{"value": "Over 9.5", "odd": "1.95"}])
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    assert len(records) == 1
    assert records[0].market == "corners_total"
    assert records[0].line == 9.5
    assert records[0].selection == "over"


def test_ignores_unrelated_markets():
    response = _odds_response("Double Chance", [{"value": "Home/Draw", "odd": "1.25"}])
    records, seen = ApiFootballOddsProvider._parse_odds_response(response)
    assert records == []
    assert seen == {"Double Chance"}


def test_ignores_values_that_dont_match_over_under_pattern():
    response = _odds_response("Cards Over/Under", [{"value": "Yes", "odd": "1.50"}])
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    assert records == []


def test_parses_match_winner_market_as_match_result():
    response = _odds_response(
        "Match Winner",
        [
            {"value": "Home", "odd": "1.80"},
            {"value": "Draw", "odd": "3.40"},
            {"value": "Away", "odd": "4.20"},
        ],
    )
    records, seen = ApiFootballOddsProvider._parse_odds_response(response)
    assert seen == {"Match Winner"}
    by_selection = {r.selection: (r.market, r.line, r.price) for r in records}
    assert by_selection["home"] == ("match_result", None, 1.80)
    assert by_selection["draw"] == ("match_result", None, 3.40)
    assert by_selection["away"] == ("match_result", None, 4.20)


def test_parses_match_winner_with_1x2_style_values():
    """Algunos bookmakers devuelven "1"/"X"/"2" en vez de "Home"/"Draw"/"Away"."""
    response = _odds_response(
        "1X2",
        [
            {"value": "1", "odd": "1.80"},
            {"value": "X", "odd": "3.40"},
            {"value": "2", "odd": "4.20"},
        ],
    )
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    by_selection = {r.selection: r.price for r in records}
    assert by_selection == {"home": 1.80, "draw": 3.40, "away": 4.20}


def test_parses_goals_over_under_market():
    response = _odds_response(
        "Goals Over/Under",
        [{"value": "Over 2.5", "odd": "1.90"}, {"value": "Under 2.5", "odd": "1.95"}],
    )
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    by_selection = {r.selection: (r.market, r.line, r.price) for r in records}
    assert by_selection["over"] == ("over_under_goals", 2.5, 1.90)
    assert by_selection["under"] == ("over_under_goals", 2.5, 1.95)


def test_parses_both_teams_to_score_market():
    response = _odds_response(
        "Both Teams Score", [{"value": "Yes", "odd": "1.70"}, {"value": "No", "odd": "2.05"}]
    )
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    by_selection = {r.selection: (r.market, r.line, r.price) for r in records}
    assert by_selection["yes"] == ("btts", None, 1.70)
    assert by_selection["no"] == ("btts", None, 2.05)


def test_cards_market_is_not_misclassified_as_generic_over_under():
    """ "Cards Over/Under" contiene la subcadena "over/under" (el hint
    generico de goles) -- debe clasificarse como cards_total, no
    over_under_goals, porque el hint de card/corner se comprueba primero."""
    response = _odds_response("Cards Over/Under", [{"value": "Over 4.5", "odd": "1.90"}])
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    assert records[0].market == "cards_total"


def test_full_match_over_under_is_not_overwritten_by_half_time_markets():
    """Bug real reportado por el usuario (cuota de "Mas de 1.5 goles" no
    coincidia con la real): API-Football devuelve "Goals Over/Under"
    (partido completo), "Goals Over/Under First Half" y "Goals Over/Under
    - Second Half" para el MISMO bookmaker -- las tres contienen la
    subcadena "over/under", asi que las tres se clasificaban como
    `over_under_goals` del partido completo. Al procesarse en orden,
    Bet365 "Over 1.5" del partido completo (1.10, el real) quedaba
    sobreescrito por el de la segunda parte (1.57) porque ambos generan la
    misma clave (bookmaker, linea=1.5, seleccion=over). Solo debe
    sobrevivir el valor del partido completo."""
    response = [
        {
            "bookmakers": [
                {
                    "name": "Bet365",
                    "bets": [
                        {
                            "name": "Goals Over/Under",
                            "values": [
                                {"value": "Over 1.5", "odd": "1.10"},
                                {"value": "Under 1.5", "odd": "7.00"},
                            ],
                        },
                        {
                            "name": "Goals Over/Under First Half",
                            "values": [
                                {"value": "Over 1.5", "odd": "1.91"},
                                {"value": "Under 1.5", "odd": "1.80"},
                            ],
                        },
                        {
                            "name": "Goals Over/Under - Second Half",
                            "values": [
                                {"value": "Over 1.5", "odd": "1.57"},
                                {"value": "Under 1.5", "odd": "2.25"},
                            ],
                        },
                        {
                            "name": "Home Team Total Goals(1st Half)",
                            "values": [{"value": "Over 1.5", "odd": "3.14"}],
                        },
                        {
                            "name": "Result/Total Goals",
                            "values": [{"value": "Home/Over 3.5", "odd": "2.62"}],
                        },
                    ],
                }
            ]
        }
    ]
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    goals_records = [r for r in records if r.market == "over_under_goals" and r.line == 1.5]
    by_selection = {r.selection: r.price for r in goals_records}
    assert by_selection == {"over": 1.10, "under": 7.00}


def test_league_ids_cover_all_five_mvp_leagues():
    from backend.app.ingestion.api_football.odds_provider import LEAGUE_IDS

    assert set(LEAGUE_IDS) == {"laliga", "premier_league", "bundesliga", "serie_a", "ligue_1"}
    assert all(isinstance(v, int) for v in LEAGUE_IDS.values())


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


class _FakeHttpClient:
    def __init__(self, payload: dict):
        self._payload = payload
        self.last_call: tuple[str, dict] | None = None

    def get(self, url: str, params: dict, headers: dict) -> _FakeResponse:
        self.last_call = (url, params)
        return _FakeResponse(self._payload)


def test_get_logs_api_errors_even_with_http_200(caplog):
    """Bug real detectado: API-Football devuelve HTTP 200 incluso cuando
    hay un error de parametros/plan -- el error real viene dentro del
    JSON ("errors"), nunca en el codigo HTTP. Sin comprobar esa clave, un
    error real se tragaba en silencio y solo se veia "0 partidos
    consultados", indistinguible de "no hay partidos en esas fechas"."""
    fake_client = _FakeHttpClient(
        {"response": [], "errors": {"season": "This season is only available in the pro plan"}}
    )
    provider = ApiFootballOddsProvider(http_client=fake_client)
    with caplog.at_level("WARNING"):
        result = provider._get("/fixtures", {"league": 140, "season": 2026})
    assert result == []
    assert any("pro plan" in record.getMessage() for record in caplog.records)


def test_get_returns_response_without_warning_when_no_errors(caplog):
    fake_client = _FakeHttpClient({"response": [{"fixture": {"id": 1}}], "errors": []})
    provider = ApiFootballOddsProvider(http_client=fake_client)
    with caplog.at_level("WARNING"):
        result = provider._get("/fixtures", {"league": 140, "season": 2026})
    assert result == [{"fixture": {"id": 1}}]
    assert not any("api_errors" in record.getMessage() for record in caplog.records)


class _FakeMultiCallHttpClient:
    """Devuelve una respuesta distinta segun el path pedido (simula
    `/fixtures` seguido de uno o varios `/odds`), contando cuantas veces
    se llama a cada uno -- para comprobar que `fetch_secondary_odds`
    reutiliza el mismo fetch de `/odds` en vez de pedirlo dos veces."""

    def __init__(self, responses_by_path: dict[str, dict]):
        self._responses_by_path = responses_by_path
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict, headers: dict) -> _FakeResponse:
        matched_path = next((key for key in self._responses_by_path if key in url), "?")
        self.calls.append((matched_path, params))
        if matched_path in self._responses_by_path:
            return _FakeResponse(self._responses_by_path[matched_path])
        return _FakeResponse({"response": [], "errors": []})


def test_fetch_secondary_odds_reuses_the_combined_fetch_without_duplicate_requests():
    import datetime as dt

    fixtures_payload = {
        "response": [
            {
                "fixture": {"id": 1, "date": "2026-01-10T20:00:00+00:00"},
                "teams": {"home": {"name": "Team A"}, "away": {"name": "Team B"}},
            }
        ],
        "errors": [],
    }
    odds_payload = {
        "response": [
            {
                "bookmakers": [
                    {
                        "name": "Bet365",
                        "bets": [
                            {
                                "name": "Cards Over/Under",
                                "values": [{"value": "Over 4.5", "odd": "1.9"}],
                            },
                            {"name": "Match Winner", "values": [{"value": "Home", "odd": "1.8"}]},
                        ],
                    }
                ]
            }
        ],
        "errors": [],
    }
    fake_client = _FakeMultiCallHttpClient({"/fixtures": fixtures_payload, "/odds": odds_payload})
    provider = ApiFootballOddsProvider(http_client=fake_client)

    secondary = provider.fetch_secondary_odds(
        "laliga", 2026, dt.date(2026, 1, 1), dt.date(2026, 1, 31)
    )

    odds_calls = [c for c in fake_client.calls if c[0] == "/odds"]
    assert len(odds_calls) == 1  # un unico fetch de /odds, nunca uno por "familia" de mercado
    assert len(secondary) == 1
    assert (
        secondary[0].odds[0].market == "cards_total"
    )  # match_winner queda fuera, es cards/corners SOLO
