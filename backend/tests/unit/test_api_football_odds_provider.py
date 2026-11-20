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


def test_cards_markets_are_ignored_entirely():
    """Pedido real del usuario: tarjetas se quito del todo (confirmado
    contra una respuesta real de /odds, `debug_match_odds.py`, que
    ninguna casa agregada por API-Football ofrece ese mercado para estas
    5 ligas). No hay ningun hint para "card" -- un bet asi se ignora, no
    se guarda bajo ningun mercado (ni "cards_total", que ya no existe)."""
    response = _odds_response(
        "Cards Over/Under",
        [{"value": "Over 4.5", "odd": "1.90"}, {"value": "Under 4.5", "odd": "1.85"}],
    )
    records, seen = ApiFootballOddsProvider._parse_odds_response(response)
    assert seen == {"Cards Over/Under"}  # se ve en los logs de diagnostico, pero no se guarda
    assert records == []


def test_parses_corners_market_case_insensitively():
    response = _odds_response("TOTAL CORNERS", [{"value": "Over 9.5", "odd": "1.95"}])
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    assert len(records) == 1
    assert records[0].market == "corners_total"
    assert records[0].line == 9.5
    assert records[0].selection == "over"


def test_ignores_unrelated_markets():
    response = _odds_response("Correct Score", [{"value": "1-0", "odd": "7.50"}])
    records, seen = ApiFootballOddsProvider._parse_odds_response(response)
    assert records == []
    assert seen == {"Correct Score"}


def test_parses_double_chance_market_as_three_independent_single_sided_markets():
    """Bug real reportado por el usuario (comparo contra una app de cuotas
    real: Sevilla-Betis "Doble Oportunidad 1X" a 1.57-1.61 segun la casa,
    la app mostraba 1.70, un precio SIN margen derivado del 1X2 en vez de
    la cuota real de este mercado propio). "Double Chance" SI es un
    mercado real que API-Football agrega -- se guarda como 3 mercados
    independientes de una sola cara (ver DOUBLE_CHANCE_VALUE_MAP), nunca
    como 3 selecciones del mismo "double_chance" (sus probabilidades
    reales se SOLAPAN, no son un par complementario valido para quitar
    el vig con el metodo proporcional estandar)."""
    response = _odds_response(
        "Double Chance",
        [
            {"value": "Home/Draw", "odd": "1.57"},
            {"value": "Draw/Away", "odd": "1.30"},
            {"value": "Home/Away", "odd": "1.40"},
        ],
    )
    records, seen = ApiFootballOddsProvider._parse_odds_response(response)
    assert seen == {"Double Chance"}
    by_market = {r.market: (r.selection, r.line, r.price) for r in records}
    assert by_market == {
        "double_chance_1x": ("yes", None, 1.57),
        "double_chance_x2": ("yes", None, 1.30),
        "double_chance_12": ("yes", None, 1.40),
    }


def test_parses_double_chance_with_1x_style_values():
    """Algunos bookmakers devuelven "1X"/"X2"/"12" en vez de
    "Home/Draw"/"Draw/Away"/"Home/Away" (confirmado en la propia UI de una
    app de cuotas real, columnas literalmente tituladas "1X"/"12"/"X2")."""
    response = _odds_response(
        "Double Chance",
        [
            {"value": "1X", "odd": "1.57"},
            {"value": "X2", "odd": "1.30"},
            {"value": "12", "odd": "1.40"},
        ],
    )
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    by_market = {r.market: r.price for r in records}
    assert by_market == {
        "double_chance_1x": 1.57,
        "double_chance_x2": 1.30,
        "double_chance_12": 1.40,
    }


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


def test_cards_market_is_never_misclassified_as_goals():
    """ "Cards Over/Under" contiene la subcadena "over/under" (el hint
    generico de goles) -- sin el exclude "card" en `over_under_goals`,
    caeria ahi y una linea de tarjetas se guardaria como si fuera de
    goles, mezclando dos estadisticas sin relacion. Debe ignorarse del
    todo, nunca clasificarse como ningun mercado."""
    response = _odds_response("Cards Over/Under", [{"value": "Over 4.5", "odd": "1.90"}])
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    assert records == []


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


def test_btts_is_not_overwritten_by_half_time_or_both_halves_markets():
    """Bug real reportado por el usuario ("cuota 3 en ambos marcan, la real
    esta a 1.50"): confirmado contra la respuesta real de API-Football
    para Bet365 -- "Both Teams Score" (partido completo, Yes=1.44/No=2.62,
    el real), "Both Teams Score - First Half" (Yes=3.25/No=1.33) y "Both
    Teams To Score - Second Half" (Yes=2.50/No=1.50) comparten subcadena y
    se clasificaban todas como `btts` del partido completo. "Both Teams To
    Score in Both Halves" (Marathonbet, Yes=7.40/No=1.06) ni siquiera usa
    "half" en singular -- por eso el excluye lleva tambien "halves"."""
    response = [
        {
            "bookmakers": [
                {
                    "name": "Bet365",
                    "bets": [
                        {
                            "name": "Both Teams Score",
                            "values": [
                                {"value": "Yes", "odd": "1.44"},
                                {"value": "No", "odd": "2.62"},
                            ],
                        },
                        {
                            "name": "Both Teams Score - First Half",
                            "values": [
                                {"value": "Yes", "odd": "3.25"},
                                {"value": "No", "odd": "1.33"},
                            ],
                        },
                        {
                            "name": "Both Teams To Score - Second Half",
                            "values": [
                                {"value": "Yes", "odd": "2.50"},
                                {"value": "No", "odd": "1.50"},
                            ],
                        },
                        {
                            "name": "Both Teams To Score in Both Halves",
                            "values": [
                                {"value": "Yes", "odd": "7.40"},
                                {"value": "No", "odd": "1.06"},
                            ],
                        },
                        {
                            "name": "Results/Both Teams Score",
                            "values": [{"value": "Home/Yes", "odd": "2.50"}],
                        },
                    ],
                }
            ]
        }
    ]
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    btts_records = [r for r in records if r.market == "btts"]
    by_selection = {r.selection: r.price for r in btts_records}
    assert by_selection == {"yes": 1.44, "no": 2.62}


def test_corners_total_is_not_corrupted_by_per_team_or_unrelated_corner_markets():
    """Bug real reportado por el usuario (screenshot: "Mas de 8.5 corners"
    a cuota 6.74 con solo 2 casas, mientras "Mas de 9.5 corners" sale a
    1.90 con 3 casas -- imposible, cuanto mas baja la linea mas cara
    tendria que ser la cuota de "mas de"). Confirmado contra la respuesta
    real de API-Football (`debug_match_odds.py`): el mercado del
    PARTIDO COMPLETO se llama literalmente "Corners Over Under" (1xBet y
    Betano de acuerdo, ~1.48-1.52 a "Over 8.5", un consenso sano), pero
    "corner" como subcadena tambien atrapaba "Home/Away Corners
    Over/Under" (corners de UN SOLO equipo, con precios mucho mas altos
    para la misma linea nominal -- la causa confirmada del disparate),
    "Multicorners" (linea 20.5, mercado distinto) y "Corners. total
    between 0 and 10m" (solo primeros 10 minutos). Solo debe sobrevivir
    el valor real del partido completo."""
    response = [
        {
            "bookmakers": [
                {
                    "name": "1xBet",
                    "bets": [
                        {
                            "name": "Corners Over Under",
                            "values": [
                                {"value": "Over 8.5", "odd": "1.48"},
                                {"value": "Under 8.5", "odd": "2.55"},
                            ],
                        },
                        {
                            "name": "Home Corners Over/Under",
                            "values": [{"value": "Over 8.5", "odd": "5.50"}],
                        },
                        {
                            "name": "Away Corners Over/Under",
                            "values": [{"value": "Over 8.5", "odd": "7.80"}],
                        },
                        {
                            "name": "Multicorners",
                            "values": [{"value": "Over 20.5", "odd": "3.10"}],
                        },
                        {
                            "name": "Corners. total between 0 and 10m",
                            "values": [{"value": "Over 0.5", "odd": "1.65"}],
                        },
                    ],
                },
                {
                    "name": "Betano",
                    "bets": [
                        {
                            "name": "Corners Over Under",
                            "values": [{"value": "Over 8.5", "odd": "1.52"}],
                        }
                    ],
                },
            ]
        }
    ]
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    corners_records = [r for r in records if r.market == "corners_total"]
    by_bookmaker_and_line = {(r.bookmaker, r.line, r.selection): r.price for r in corners_records}
    assert by_bookmaker_and_line == {
        ("1xBet", 8.5, "over"): 1.48,
        ("1xBet", 8.5, "under"): 2.55,
        ("Betano", 8.5, "over"): 1.52,
    }


def test_home_away_corners_over_under_never_leaks_into_goals():
    """Bug real reportado por el usuario (comparo contra una app de
    cuotas real: Real Madrid "Over 2.5 goles" de Bet365 a 1.83 en nuestra
    BD, 1.40 real en bet365.com): confirmado contra la respuesta real de
    /odds que "Home Corners Over/Under"/"Away Corners Over/Under"
    (corners de UN SOLO equipo) SI quedan excluidos del mercado
    "corners_total" (por "home"/"away" en CORNERS_EXCLUDE), pero al caer
    excluidos de ahi, el bucle de `_parse_odds_response` los volvia a
    probar contra el SIGUIENTE market_key de la lista -- y como SI
    contienen "Over/Under" (con barra), se colaban como linea de GOLES.
    1.83 es el valor REAL visto en produccion para "Away Corners
    Over/Under" de Bet365 en ese partido -- nunca debe aparecer bajo
    'over_under_goals'."""
    response = [
        {
            "bookmakers": [
                {
                    "name": "Bet365",
                    "bets": [
                        {
                            "name": "Goals Over/Under",
                            "values": [
                                {"value": "Over 2.5", "odd": "1.40"},
                                {"value": "Under 2.5", "odd": "3.00"},
                            ],
                        },
                        {
                            "name": "Home Corners Over/Under",
                            "values": [
                                {"value": "Over 6.5", "odd": "1.80"},
                                {"value": "Under 6.5", "odd": "1.91"},
                            ],
                        },
                        {
                            "name": "Away Corners Over/Under",
                            "values": [
                                {"value": "Over 2.5", "odd": "1.83"},
                                {"value": "Under 2.5", "odd": "1.83"},
                            ],
                        },
                    ],
                }
            ]
        }
    ]
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    # Los dos bets de corners por equipo no deben acabar en NINGUN mercado
    # reconocido (ni goles ni corners_total) -- solo debe sobrevivir el
    # "Goals Over/Under" real del partido completo.
    assert len(records) == 2
    by_line_and_selection = {(r.market, r.line, r.selection): r.price for r in records}
    assert by_line_and_selection == {
        ("over_under_goals", 2.5, "over"): 1.40,
        ("over_under_goals", 2.5, "under"): 3.00,
    }


def test_double_chance_first_half_never_leaks_into_full_match_double_chance():
    """Mismo patron de bug que el de goles de arriba, aplicado a Doble
    Oportunidad: Bet365 tambien ofrece "Double Chance - First Half" --
    sin excluir "half" aqui, se mezclaria con la Doble Oportunidad del
    PARTIDO COMPLETO bajo la misma clave de mercado."""
    response = [
        {
            "bookmakers": [
                {
                    "name": "Bet365",
                    "bets": [
                        {
                            "name": "Double Chance",
                            "values": [{"value": "Home/Draw", "odd": "1.57"}],
                        },
                        {
                            "name": "Double Chance - First Half",
                            "values": [{"value": "Home/Draw", "odd": "1.15"}],
                        },
                    ],
                }
            ]
        }
    ]
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    dc_1x_records = [r for r in records if r.market == "double_chance_1x"]
    assert len(dc_1x_records) == 1
    assert dc_1x_records[0].price == 1.57


def test_corners_total_range_market_edge_values_are_excluded():
    """Confirmado contra la respuesta real: "Corners. Total (Range)" es
    una apuesta por RANGOS ("Under 6", "6 - 8", "9 - 11", "12 - 14", "Over
    14"), no una linea over/under normal -- pero sus dos valores extremo
    ("Under 6"/"Over 14") SI encajan con la regex generica de over/under y
    se colaban como si fueran lineas 6/14 reales del mercado principal."""
    response = _odds_response(
        "Corners. Total (Range)",
        [
            {"value": "Under 6", "odd": "4.50"},
            {"value": "6 - 8", "odd": "3.20"},
            {"value": "Over 14", "odd": "12.00"},
        ],
    )
    records, _ = ApiFootballOddsProvider._parse_odds_response(response)
    assert records == []


def test_league_ids_cover_all_five_mvp_leagues():
    """Subconjunto, no igualdad exacta (seccion "Selecciones", 2026-11):
    `LEAGUE_IDS` ahora tambien cubre competiciones de selecciones (ver
    `INTERNATIONAL_COMPETITION_EDITIONS`) ademas de las 5 ligas de club
    del MVP -- anhadir una no debe romper esta prueba."""
    from backend.app.ingestion.api_football.odds_provider import LEAGUE_IDS

    assert {"laliga", "premier_league", "bundesliga", "serie_a", "ligue_1"} <= set(LEAGUE_IDS)
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
                                "name": "Corners Over Under",
                                "values": [{"value": "Over 8.5", "odd": "1.9"}],
                            },
                            {"name": "Match Winner", "values": [{"value": "Home", "odd": "1.8"}]},
                            # Tarjetas se ignora del todo (pedido real del usuario, ver
                            # odds_provider.py) -- no debe colarse bajo ningun mercado.
                            {
                                "name": "Cards Over/Under",
                                "values": [{"value": "Over 4.5", "odd": "1.9"}],
                            },
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
    assert secondary[0].odds[0].market == "corners_total"  # match_winner/cards quedan fuera


def test_fetch_odds_for_window_pauses_between_fixtures_to_avoid_the_per_minute_rate_limit(monkeypatch):
    """Bug real reportado por el usuario (cuota real movida a 1.35, la
    app seguia mostrando 1.70 varios ciclos despues): este bucle pide
    `/odds` UNA VEZ POR PARTIDO de la ventana sin ninguna pausa -- con
    varios partidos programados, eso agota el limite de peticiones por
    minuto de API-Football a mitad de la lista, y la excepcion resultante
    (ver ApiFootballRateLimitError) cancela TODOS los partidos restantes
    de esa liga para ese ciclo, no solo el que la disparo. Se comprueba
    aqui que hay una pausa entre cada peticion a `/odds` (nunca antes de
    la primera, ver `enumerate(fixtures)`/`i > 0`) -- mismo remedio ya
    usado en `cli.py::update_international` para el mismo problema real."""
    import datetime as dt

    from backend.app.ingestion.api_football import odds_provider as odds_provider_module

    sleep_calls: list[float] = []
    monkeypatch.setattr(odds_provider_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    fixtures_payload = {
        "response": [
            {
                "fixture": {"id": i, "date": "2026-01-10T20:00:00+00:00"},
                "teams": {"home": {"name": f"Team {i}A"}, "away": {"name": f"Team {i}B"}},
            }
            for i in range(3)
        ],
        "errors": [],
    }
    odds_payload = {"response": [], "errors": []}
    fake_client = _FakeMultiCallHttpClient({"/fixtures": fixtures_payload, "/odds": odds_payload})
    provider = ApiFootballOddsProvider(http_client=fake_client)

    provider.fetch_odds_for_window("laliga", 2026, dt.date(2026, 1, 1), dt.date(2026, 1, 31))

    odds_calls = [c for c in fake_client.calls if c[0] == "/odds"]
    assert len(odds_calls) == 3
    assert sleep_calls == [1, 1]  # 2 pausas entre 3 peticiones, nunca antes de la primera


def test_match_winner_does_not_use_other_1x2_markets():
    from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider
    records, _ = ApiFootballOddsProvider._parse_odds_response([{"bookmakers": [{"name": "Bet365", "bets": [
        {"id": 1, "name": "Match Winner", "values": [{"value": "Home", "odd": "1.42"}]},
        {"id": 55, "name": "Corners 1x2", "values": [{"value": "Home", "odd": "1.25"}]},
        {"id": 176, "name": "ShotOnTarget 1x2", "values": [{"value": "Home", "odd": "1.18"}]},
    ]}]}])
    assert [(r.market, r.selection, r.price) for r in records] == [("match_result", "home", 1.42)]


def test_corners_two_way_keeps_its_price_when_three_way_has_the_same_line():
    records, _ = ApiFootballOddsProvider._parse_odds_response([{"bookmakers": [{"name": "Bet365", "bets": [
        {"id": 45, "name": "Corners Over Under", "values": [{"value": "Over 9.5", "odd": "1.80"}]},
        {"id": 85, "name": "Total Corners (3 way)", "values": [{"value": "Over 9.5", "odd": "2.20"}]},
    ]}]}])
    assert [(r.market, r.line, r.selection, r.price) for r in records] == [
        ("corners_total", 9.5, "over", 1.8),
    ]
