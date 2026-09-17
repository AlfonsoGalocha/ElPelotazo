from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.main import app
from backend.app.prediction.market_labels import MARKET_DEFINITIONS
from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS
from backend.app.services.data_service import ingest_matches
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.tests.fixtures.synthetic import generate_synthetic_matches

TOTAL_MVP_MARKETS = len(MARKET_DEFINITIONS) + len(SECONDARY_MARKET_DEFINITIONS)


class _FakeProvider(DataProvider):
    name = "integration_test_provider"

    def __init__(self, synthetic):
        self._synthetic = synthetic

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        rows = self._synthetic[self._synthetic["season_label"] == season_label]
        records = []
        for _, r in rows.iterrows():
            records.append(
                RawMatchRecord(
                    provider=self.name,
                    provider_id=f"itest-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"IntegrationTeam{int(r.home_team_id)}",
                    away_team_raw=f"IntegrationTeam{int(r.away_team_id)}",
                    home_goals=int(r.home_goals),
                    away_goals=int(r.away_goals),
                    home_shots=int(r.home_shots),
                    away_shots=int(r.away_shots),
                    odds=[
                        RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.9),
                        RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
                    ],
                )
            )
        return records


@pytest.fixture(scope="module")
def seeded_competition_code():
    """Ingiere partidos sinteticos, entrena y genera predicciones para un
    partido futuro, end-to-end, usando el pipeline real (no mocks internos)."""
    competition_code = "laliga_integration_test"
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=3, seed=99)
    provider = _FakeProvider(synthetic)

    from backend.app.normalization.competitions import COMPETITIONS

    COMPETITIONS.setdefault(competition_code, ("La Liga (integration test)", "Spain"))

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"

    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        season = db.query(Season).filter_by(competition_id=comp.id).order_by(Season.id.desc()).first()
        db.add(
            Match(
                provider="integration_test_manual",
                provider_id="future-fixture-1",
                competition_id=comp.id,
                season_id=season.id,
                kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
                home_team_id=1,
                away_team_id=2,
                status="scheduled",
            )
        )

    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, competition_code, fixture_date)
        assert len(predictions) == TOTAL_MVP_MARKETS  # goles + tarjetas + corners

    return competition_code


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_competitions_endpoint_lists_seeded_competition(seeded_competition_code):
    client = TestClient(app)
    response = client.get("/competitions")
    assert response.status_code == 200
    codes = [c["code"] for c in response.json()]
    assert seeded_competition_code in codes


def test_predictions_today_returns_all_five_markets(seeded_competition_code):
    client = TestClient(app)
    response = client.get("/predictions/today")
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= TOTAL_MVP_MARKETS
    markets = {p["market"] for p in body}
    assert markets == set(MARKET_DEFINITIONS) | set(SECONDARY_MARKET_DEFINITIONS)

    for prediction in body:
        assert 0.0 <= prediction["model_probability"] <= 1.0
        assert 0.0 <= prediction["confidence"] <= 1.0
        assert 0.0 <= prediction["data_quality"] <= 1.0
        assert prediction["fair_odds"] > 1.0
        assert prediction["signal_tier"] in {"HIGH_DATA_SUPPORT", "MEDIUM_DATA_SUPPORT", "LOW_DATA_SUPPORT"}


def test_match_predictions_endpoint(seeded_competition_code):
    client = TestClient(app)
    today = client.get("/predictions/today").json()
    match_id = today[0]["match"]["id"]

    response = client.get(f"/matches/{match_id}/predictions")
    assert response.status_code == 200
    assert len(response.json()) == TOTAL_MVP_MARKETS


def test_models_endpoint_lists_trained_model(seeded_competition_code):
    client = TestClient(app)
    response = client.get("/models")
    assert response.status_code == 200
    assert any(m["dataset_version"].startswith(seeded_competition_code) for m in response.json())


def test_get_nonexistent_match_returns_404():
    client = TestClient(app)
    response = client.get("/matches/999999")
    assert response.status_code == 404


def test_regenerating_predictions_does_not_duplicate_rows(seeded_competition_code):
    """Regresion: `generate_predictions_for_competition` insertaba SIEMPRE
    filas nuevas sin borrar las anteriores. Como el flujo pensado (`refresh`,
    `predict-upcoming`) se ejecuta repetidamente sobre los MISMOS partidos
    programados para refrescar el dashboard, esto acumulaba duplicados
    (un mismo partido apareciendo 2-3 veces en "Las 5 mejores predicciones").
    Regenerar sobre los mismos partidos debe SUSTITUIR, no acumular.
    """
    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        first_run = generate_predictions_for_competition(db, seeded_competition_code, fixture_date)
        assert len(first_run) == TOTAL_MVP_MARKETS

    with session_scope() as db:
        second_run = generate_predictions_for_competition(db, seeded_competition_code, fixture_date)
        assert len(second_run) == TOTAL_MVP_MARKETS
        match_id = second_run[0].match_id

    client = TestClient(app)
    response = client.get(f"/matches/{match_id}/predictions")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == TOTAL_MVP_MARKETS
    markets = [p["market"] for p in body]
    assert len(markets) == len(set(markets))
