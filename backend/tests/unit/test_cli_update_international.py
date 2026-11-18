"""`cli.py::update_international` -- seccion "Selecciones" (pedido real
del usuario, 2026-11). A diferencia de `update()` (mirror historico tipo
football-data.co.uk, solo para las 5 ligas de club), esta ingesta la
UNICA fuente real disponible para selecciones (API-Football) es
`ApiFootballMatchProvider`, verificada aqui con respuestas CANNED (ver
docstring de `ingestion/api_football/client.py`: sin acceso de red real
desde este entorno)."""

from __future__ import annotations

import time

import pytest
import typer

from backend.app.cli import update_international
from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.ingestion.api_football.client import INTERNATIONAL_COMPETITION_EDITIONS
from backend.app.ingestion.api_football.fixtures_provider import ApiFootballMatchProvider
from backend.app.ingestion.base import RawMatchRecord


@pytest.fixture(autouse=True)
def _enable_api_football(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_ENABLED", "true")
    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    from backend.app.config.settings import get_settings

    get_settings.cache_clear()
    # update_international pausa 1s entre cada peticion real (seccion
    # "Selecciones": evitar el rate limit de verdad visto en vivo) -- sin
    # esto el test seria lento de verdad, no solo en apariencia.
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    yield
    get_settings.cache_clear()


def _record(provider_id: str, date: str) -> RawMatchRecord:
    return RawMatchRecord(
        provider="api_football",
        provider_id=provider_id,
        competition_code="uefa_nations_league",
        season_label="irrelevant",  # ingest_matches lo ignora: usa el que le pasa update_international
        date=date,
        home_team_raw="Spain",
        away_team_raw="Portugal",
        home_goals=2,
        away_goals=1,
    )


def test_update_international_ingests_every_known_edition(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_fetch_matches(self, competition_code, season_label):
        calls.append((competition_code, season_label))
        year = int(season_label.split("/")[0])
        return [_record(f"nl-{year}", f"{year}-09-05T19:45:00+00:00")]

    monkeypatch.setattr(ApiFootballMatchProvider, "fetch_matches", fake_fetch_matches)

    update_international(competition="uefa_nations_league")

    # Las 5 ediciones reales conocidas (ver INTERNATIONAL_COMPETITION_EDITIONS),
    # cada una con el MISMO formato de Season.label que update-fixtures
    # (season_label_from_date), nunca un anho suelto.
    assert calls == [
        ("uefa_nations_league", "2018/19"),
        ("uefa_nations_league", "2020/21"),
        ("uefa_nations_league", "2022/23"),
        ("uefa_nations_league", "2024/25"),
        ("uefa_nations_league", "2026/27"),
    ]

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code="uefa_nations_league").one()
        seasons = db.query(Season).filter_by(competition_id=competition.id).all()
        assert {s.label for s in seasons} == {
            "2018/19",
            "2020/21",
            "2022/23",
            "2024/25",
            "2026/27",
        }
        matches = db.query(Match).filter_by(competition_id=competition.id).all()
        assert len(matches) == 5
        assert {m.provider_id for m in matches} == {f"nl-{y}" for y in (2018, 2020, 2022, 2024, 2026)}


def test_update_international_rejects_an_unknown_competition():
    with pytest.raises(typer.Exit):
        update_international(competition="not-a-real-international-competition")


def test_update_international_processes_every_registered_competition_by_default(monkeypatch):
    seen_competitions: set[str] = set()

    def fake_fetch_matches(self, competition_code, season_label):
        seen_competitions.add(competition_code)
        return []

    monkeypatch.setattr(ApiFootballMatchProvider, "fetch_matches", fake_fetch_matches)

    update_international(competition=None)  # sin --competition: TODAS las registradas

    assert seen_competitions == set(INTERNATIONAL_COMPETITION_EDITIONS)


def test_update_international_recovers_after_one_edition_fails(monkeypatch):
    """Mismo mecanismo que `update()`/`update-fixtures` (ver
    test_cli_update_loop_rollback.py): un fallo en UNA edicion no debe
    tumbar las demas."""

    def fake_fetch_matches(self, competition_code, season_label):
        if season_label == "2020/21":
            raise RuntimeError("fallo simulado de API-Football para esta edicion")
        year = int(season_label.split("/")[0])
        return [_record(f"nl-retry-{year}", f"{year}-09-05T19:45:00+00:00")]

    monkeypatch.setattr(ApiFootballMatchProvider, "fetch_matches", fake_fetch_matches)

    update_international(competition="uefa_nations_league")

    with session_scope() as db:
        competition = db.query(Competition).filter_by(code="uefa_nations_league").one()
        matches = db.query(Match).filter_by(competition_id=competition.id).all()
        provider_ids = {m.provider_id for m in matches}
        # Las 4 ediciones SIN fallo se ingirieron igual -- 2020/21 (la que
        # fallo) nunca aparece, pero tampoco tumbo 2022/23/2024/25/2026/27.
        assert provider_ids >= {"nl-retry-2018", "nl-retry-2022", "nl-retry-2024", "nl-retry-2026"}
        assert "nl-retry-2020" not in provider_ids
