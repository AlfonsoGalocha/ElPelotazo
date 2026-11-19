"""`scripts/debug_api_football_injuries_coverage.py` -- sin acceso de red
real a api-football.com desde este entorno (ver docstring de
`ingestion/api_football/client.py`), asi que se verifica con una
respuesta CANNED (misma forma documentada publicamente, mismo patron que
`test_debug_api_football_national_teams_script.py`) en vez de contra la
API real -- el usuario lo correra de verdad en su contenedor, con su key."""

from __future__ import annotations

import pytest

from backend.app.ingestion.api_football.client import ApiFootballClient
from scripts.debug_api_football_injuries_coverage import main


@pytest.fixture(autouse=True)
def _enable_api_football(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_ENABLED", "true")
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    from backend.app.config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_main_prints_injuries_coverage_flag_when_present(capsys, monkeypatch):
    def fake_get(self, path, params):
        assert path == "/leagues"
        league_id = params["id"]
        return [
            {
                "league": {"id": league_id, "name": f"League {league_id}"},
                "seasons": [
                    {"year": 2025, "coverage": {"fixtures": True, "odds": True, "injuries": league_id == 140}}
                ],
            }
        ]

    monkeypatch.setattr(ApiFootballClient, "_get", fake_get)

    main()

    output = capsys.readouterr().out
    assert "laliga (id=140)" in output
    assert "coverage.injuries=True" in output
    assert "coverage.injuries=False" in output  # el resto de ligas, en esta respuesta canned
    assert "clave presente en la respuesta: True" in output


def test_main_reports_when_injuries_key_is_entirely_absent(capsys, monkeypatch):
    """Si la API real no incluye 'injuries' dentro de `coverage` para
    ninguna liga, el script debe decirlo explicitamente -- nunca asumir
    False cuando en realidad la clave no vino en la respuesta."""

    def fake_get(self, path, params):
        return [{"league": {"id": params["id"]}, "seasons": [{"year": 2025, "coverage": {"fixtures": True}}]}]

    monkeypatch.setattr(ApiFootballClient, "_get", fake_get)

    main()

    output = capsys.readouterr().out
    assert "coverage.injuries=None" in output
    assert "clave presente en la respuesta: False" in output


def test_main_reports_when_api_football_is_not_configured(capsys, monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_ENABLED", "false")
    from backend.app.config.settings import get_settings

    get_settings.cache_clear()

    main()

    output = capsys.readouterr().out
    assert "no se puede comprobar nada real" in output
