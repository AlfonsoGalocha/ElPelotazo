"""Fase 7 (seccion 27): dos casos nombrados explicitamente que no tenian
un test dedicado con ese nombre todavia (aunque ya estaban cubiertos
indirectamente por el filtro duro / el fix de renormalizacion):

- Una prediccion MODEL_ONLY (sin mercado) nunca aparece en
  `/predictions/top-signals` ni en `/predictions/best`, solo en
  `/predictions/model-only`.
- Over y Under del mismo partido nunca se muestran ambos como "señal
  fuerte" (is_strong_signal), end-to-end sobre `Prediction` reales, no
  solo sobre las probabilidades crudas de `market_labels.py`.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import Prediction
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.main import app
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.prediction.anomaly import compute_anomaly_flags, is_strong_signal
from backend.app.services.data_service import ingest_matches
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.tests.fixtures.synthetic import generate_synthetic_matches

client = TestClient(app)


class _FakeProvider(DataProvider):
    name = "signal_exclusions_test_provider"

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
                    provider_id=f"exclusions-test-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"ExclusionsTeam{int(r.home_team_id)}",
                    away_team_raw=f"ExclusionsTeam{int(r.away_team_id)}",
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


def test_model_only_predictions_never_appear_in_top_signals_or_best():
    """Genera predicciones reales (goles CON mercado, tarjetas/corners SIN
    mercado -- el proveedor fake no trae cuotas de esos mercados) y
    comprueba que las señales sin mercado nunca aparecen en los rankings
    con mercado, solo en `/predictions/model-only`."""
    competition_code = "signal_exclusions_test_league"
    COMPETITIONS.setdefault(competition_code, ("Signal Exclusions Test League", "Testland"))
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=3, seed=42)
    provider = _FakeProvider(synthetic)

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"

    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        season = db.query(Season).filter_by(competition_id=comp.id).order_by(Season.id.desc()).first()
        match = Match(
            provider="signal_exclusions_test_manual",
            provider_id="exclusions-future-fixture-1",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
            home_team_id=1,
            away_team_id=2,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        db.add_all(
            [
                MatchOdds(
                    match_id=match.id,
                    bookmaker="bet365",
                    market="over_under_goals",
                    line=2.5,
                    selection="over",
                    price=1.90,
                    snapshot_type="pre_match",
                    recorded_at=match.kickoff_utc,
                ),
                MatchOdds(
                    match_id=match.id,
                    bookmaker="bet365",
                    market="over_under_goals",
                    line=2.5,
                    selection="under",
                    price=1.95,
                    snapshot_type="pre_match",
                    recorded_at=match.kickoff_utc,
                ),
            ]
        )

    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        generate_predictions_for_competition(db, competition_code, fixture_date)

    with session_scope() as db:
        model_only_ids = {
            p.id
            for p in db.query(Prediction).all()
            if p.market_odds is None or p.market_probability is None
        }
    assert model_only_ids, "las predicciones de tarjetas/corners no deberian tener mercado en este fixture"

    top_signals = client.get(
        "/predictions/top-signals", params={"limit": 100, "competition_code": competition_code, "scope": "all_upcoming", "days": 30}
    ).json()
    best = client.get(
        "/predictions/best", params={"limit": 50, "competition_code": competition_code, "scope": "all_upcoming", "days": 30}
    ).json()

    for prediction in top_signals + best:
        assert prediction["id"] not in model_only_ids
        assert prediction["has_market"] is True


def test_over_and_under_never_both_strong_signals_for_the_same_match():
    """Construye a mano dos predicciones del mismo partido, mismo grupo
    mutuamente excluyente (`over_2_5`/`under_2_5`), ambas con
    `model_probability > 0.5` (un estado que el fix de renormalizacion
    deberia impedir en la practica, pero se comprueba la red de
    seguridad): ninguna de las dos debe pasar `is_strong_signal`."""

    class _Stub:
        def __init__(self, id, market, model_probability):
            self.id = id
            self.market = market
            self.model_probability = model_probability
            self.edge = 0.1
            self.confidence = 0.8
            self.data_quality = 0.8
            self.bookmakers_used = 5
            self.market_odds_min = 1.9
            self.market_odds_max = 2.0
            self.market_odds_median = 1.95

    over = _Stub(1, "over_2_5", 0.55)
    under = _Stub(2, "under_2_5", 0.52)  # contradictorio a proposito: ambos > 0.5
    match_predictions = [over, under]

    flags_over = compute_anomaly_flags(over, match_predictions)
    flags_under = compute_anomaly_flags(under, match_predictions)

    assert not is_strong_signal(flags_over)
    assert not is_strong_signal(flags_under)
