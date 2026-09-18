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


def test_parse_event_extracts_alternate_totals_and_btts():
    """Regresion: antes solo se pedia "h2h,totals" a The Odds API, y
    "totals" a secas solo trae la linea PRINCIPAL de cada bookmaker
    (normalmente 2.5, a veces 3.5) -- nunca lineas alternativas como 1.5,
    y el mercado "btts" (Ambos Marcan) no se pedia en absoluto aunque el
    modelo ya lo soporta (ver prediction/market_labels.py). Bug real
    reportado por el usuario: "solo sacamos... 2.5 o 3.5... quiero
    tambien 1.5 y ambos marcan"."""
    event = {
        "home_team": "A",
        "away_team": "B",
        "commence_time": "2026-09-19T18:30:00Z",
        "bookmakers": [
            {
                "key": "bet365",
                "markets": [
                    {
                        "key": "alternate_totals",
                        "outcomes": [
                            {"name": "Over", "point": 1.5, "price": 1.25},
                            {"name": "Under", "point": 1.5, "price": 3.75},
                        ],
                    },
                    {
                        "key": "btts",
                        "outcomes": [
                            {"name": "Yes", "price": 1.70},
                            {"name": "No", "price": 2.05},
                        ],
                    },
                ],
            }
        ],
    }
    snapshot = OddsApiProvider._parse_event(event)
    markets = {(o.market, o.line, o.selection): o.price for o in snapshot.odds}
    assert markets[("over_under_goals", 1.5, "over")] == 1.25
    assert markets[("over_under_goals", 1.5, "under")] == 3.75
    assert markets[("btts", None, "yes")] == 1.70
    assert markets[("btts", None, "no")] == 2.05


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise __import__("httpx").HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload

    @property
    def text(self):
        return str(self._payload)


def test_download_only_requests_featured_markets(monkeypatch):
    """Regresion real: pedir un mercado "additional" (alternate_totals,
    btts) en el endpoint MASIVO (`/sports/{sport}/odds`) devuelve 422
    "Markets not supported by this endpoint" y tumba TODA la peticion --
    tambien h2h/totals, que si funcionaban. Este test fija que el
    endpoint masivo solo pida mercados "featured"."""
    captured: dict = {}

    class _FakeClient:
        def get(self, url, params):
            captured["params"] = params
            return _FakeResponse([])

    from backend.app.config.settings import get_settings

    provider = OddsApiProvider(http_client=_FakeClient())
    monkeypatch.setattr(get_settings(), "odds_api_key", "fake-key", raising=False)
    provider._download("soccer_epl")
    assert captured["params"]["markets"] == "h2h,totals"


def test_fetch_odds_does_not_call_additional_markets_by_default(monkeypatch):
    """Por defecto (`odds_api_fetch_additional_markets=False`) no se hace
    ninguna request extra por evento, para no gastar cuota sin que el
    usuario lo pida explicitamente."""
    call_count = {"n": 0}

    class _FakeClient:
        def get(self, url, params):
            call_count["n"] += 1
            return _FakeResponse(
                [{"id": "evt1", "home_team": "A", "away_team": "B", "commence_time": "2026-09-19T18:30:00Z", "bookmakers": []}]
            )

    from backend.app.config.settings import get_settings

    provider = OddsApiProvider(http_client=_FakeClient())
    settings = get_settings()
    monkeypatch.setattr(settings, "odds_api_key", "fake-key", raising=False)
    monkeypatch.setattr(settings, "odds_api_fetch_additional_markets", False, raising=False)
    provider.fetch_odds("premier_league")
    assert call_count["n"] == 1


def test_fetch_odds_merges_additional_markets_per_event_when_enabled(monkeypatch):
    """Con el flag activado, se hace 1 request extra POR EVENTO al
    endpoint `/events/{id}/odds` y se combinan sus mercados con los del
    endpoint masivo antes de parsear."""
    requests_made: list[str] = []

    class _FakeClient:
        def get(self, url, params):
            requests_made.append(url)
            if url.endswith("/odds") and "/events/" not in url:
                return _FakeResponse(
                    [
                        {
                            "id": "evt1",
                            "home_team": "A",
                            "away_team": "B",
                            "commence_time": "2026-09-19T18:30:00Z",
                            "bookmakers": [
                                {"key": "bet365", "markets": [{"key": "h2h", "outcomes": []}]},
                            ],
                        }
                    ]
                )
            return _FakeResponse(
                {
                    "bookmakers": [
                        {
                            "key": "bet365",
                            "markets": [
                                {"key": "btts", "outcomes": [{"name": "Yes", "price": 1.7}]},
                            ],
                        }
                    ]
                }
            )

    from backend.app.config.settings import get_settings

    provider = OddsApiProvider(http_client=_FakeClient())
    settings = get_settings()
    monkeypatch.setattr(settings, "odds_api_key", "fake-key", raising=False)
    monkeypatch.setattr(settings, "odds_api_fetch_additional_markets", True, raising=False)
    snapshots = provider.fetch_odds("premier_league")
    assert any(r.market == "btts" for r in snapshots[0].odds)
    assert any("/events/evt1/odds" in url for url in requests_made)


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
