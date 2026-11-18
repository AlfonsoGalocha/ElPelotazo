"""`scripts/debug_api_football_national_teams.py` -- sin acceso de red real
a api-football.com desde este entorno (ver docstring de
`ingestion/api_football/client.py`), asi que se verifica con una respuesta
CANNED realista (misma forma documentada publicamente que ya usan
`fixtures_provider.py`/`odds_provider.py`) en vez de contra la API real --
el usuario la correra de verdad en su contenedor, con su key."""

from __future__ import annotations

import pytest

from backend.app.ingestion.api_football.client import ApiFootballClient
from scripts.debug_api_football_national_teams import main

LEAGUES_RESPONSE = [
    {
        "league": {"id": 1, "name": "World Cup", "type": "Cup"},
        "country": {"name": "World"},
        "seasons": [
            {
                "year": 2026,
                "coverage": {
                    "fixtures": True,
                    "odds": True,
                    "standings": False,
                    "predictions": True,
                },
            }
        ],
    },
    {
        "league": {"id": 5, "name": "UEFA Nations League", "type": "Cup"},
        "country": {"name": "World"},
        "seasons": [
            {
                "year": 2025,
                "coverage": {
                    "fixtures": True,
                    "odds": True,
                    "standings": True,
                    "predictions": True,
                },
            }
        ],
    },
    {
        "league": {"id": 999, "name": "Women's World Cup", "type": "Cup"},
        "country": {"name": "World"},
        "seasons": [{"year": 2026, "coverage": {"fixtures": True, "odds": False}}],
    },
]

FIXTURES_RESPONSE = [
    {
        "fixture": {"id": 1234, "date": "2026-11-20T20:00:00+00:00"},
        "teams": {"home": {"name": "Spain"}, "away": {"name": "Portugal"}},
        "league": {"round": "Final"},
    }
]


@pytest.fixture(autouse=True)
def _enable_api_football(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_ENABLED", "true")
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    from backend.app.config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_main_prints_leagues_of_interest_and_their_upcoming_fixtures(capsys, monkeypatch):
    def fake_get(self, path, params):
        if path == "/leagues":
            assert params == {"country": "World"}
            return LEAGUES_RESPONSE
        if path == "/fixtures":
            return FIXTURES_RESPONSE
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(ApiFootballClient, "_get", fake_get)

    main()

    output = capsys.readouterr().out
    assert "Total competiciones devueltas: 3" in output
    assert "World Cup" in output
    assert "UEFA Nations League" in output
    # El filtro de interes es por subcadena (deliberadamente simple, ver
    # docstring del script): "Women's World Cup" tambien contiene "world
    # cup" y aparece en la lista de interes -- no se oculta nada, el
    # usuario ve el nombre completo y decide. Lo que SI debe cumplirse es
    # que el catalogo completo (3 ligas) se imprime siempre, filtradas o no.
    assert "id=999" in output
    assert "Spain vs Portugal" in output
    assert "fixture_id=1234" in output


def test_main_prints_odds_for_a_specific_fixture_when_requested(capsys, monkeypatch):
    odds_response = [
        {
            "bookmakers": [
                {
                    "id": 1,
                    "name": "Bet365",
                    "bets": [{"name": "Match Winner", "values": [{"value": "Home", "odd": "1.90"}]}],
                }
            ]
        }
    ]

    def fake_get(self, path, params):
        assert path == "/odds"
        assert params == {"fixture": 1234}
        return odds_response

    monkeypatch.setattr(ApiFootballClient, "_get", fake_get)

    main(odds_for_fixture=1234)

    output = capsys.readouterr().out
    assert "Cuotas RAW para fixture_id=1234" in output
    assert "Match Winner" in output
    assert "Bet365" in output


def test_main_prints_full_season_history_for_mens_absolute_candidates(capsys, monkeypatch):
    """La seccion "ABSOLUTA MASCULINA" (pedido real del usuario tras ver
    que 'Selecciones' solo tenia Nations League) imprime el HISTORIAL
    COMPLETO de temporadas (no solo la ultima) de cada candidata real --
    aqui, World Cup (id=1) con 2 ediciones -- y avisa explicitamente
    de un ID que falte en la respuesta real (nunca lo oculta en
    silencio) en vez de fallar o inventar datos."""
    world_cup_two_editions = {
        "league": {"id": 1, "name": "World Cup", "type": "Cup"},
        "country": {"name": "World"},
        "seasons": [
            {"year": 2022, "coverage": {"fixtures": True, "odds": False, "standings": True}},
            {"year": 2026, "coverage": {"fixtures": True, "odds": False, "standings": False}},
        ],
    }

    def fake_get(self, path, params):
        if path == "/leagues":
            return [world_cup_two_editions]
        if path == "/fixtures":
            return []
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(ApiFootballClient, "_get", fake_get)

    main()

    output = capsys.readouterr().out
    assert "ABSOLUTA MASCULINA" in output
    assert "id=1  nombre='World Cup'  (2 temporadas reales listadas)" in output
    assert "year=2022" in output
    assert "year=2026" in output
    # Euro Championship (id=4) no esta en la respuesta canned -- debe
    # avisar, nunca callarse ni inventar una temporada.
    assert "id=4  'Euro Championship': NO aparece en la respuesta real" in output


def test_main_reports_when_api_football_is_not_configured(capsys, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_ENABLED", "false")
    from backend.app.config.settings import get_settings

    get_settings.cache_clear()

    main()

    output = capsys.readouterr().out
    assert "no esta activado" in output
