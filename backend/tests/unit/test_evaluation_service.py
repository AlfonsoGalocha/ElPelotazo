from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches
from backend.app.services.evaluation_service import (
    MIN_SETTLED_FOR_TRACK_RECORD,
    evaluate_settled_predictions,
    market_track_records,
)
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.tests.fixtures.synthetic import generate_synthetic_matches


class _FakeProvider(DataProvider):
    name = "evaluation_test_provider"

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
                    provider_id=f"eval-test-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"EvalTeam{int(r.home_team_id)}",
                    away_team_raw=f"EvalTeam{int(r.away_team_id)}",
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


def test_evaluate_settled_predictions_grades_a_now_finished_match():
    """Flujo completo del pedido del usuario: predecir un partido futuro,
    simular que se juega de verdad (lo que en produccion trae
    `football-edge update` tras `refresh_cache()`), y comprobar que la
    prediccion YA GUARDADA -- nunca una nueva -- se compara contra el
    resultado real."""
    competition_code = "evaluation_test_league"
    COMPETITIONS.setdefault(competition_code, ("Evaluation Test League", "Testland"))
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=3, seed=123)
    provider = _FakeProvider(synthetic)

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"

    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        season = (
            db.query(Season).filter_by(competition_id=comp.id).order_by(Season.id.desc()).first()
        )
        match = Match(
            provider="evaluation_test_manual",
            provider_id="eval-future-fixture-1",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
            home_team_id=1,
            away_team_id=2,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        match_id = match.id
        db.add_all(
            [
                MatchOdds(
                    match_id=match_id,
                    bookmaker="bet365",
                    market="over_under_goals",
                    line=2.5,
                    selection="over",
                    price=1.90,
                    snapshot_type="pre_match",
                    recorded_at=match.kickoff_utc,
                ),
                MatchOdds(
                    match_id=match_id,
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
        predictions = generate_predictions_for_competition(db, competition_code, fixture_date)
        assert len(predictions) > 0

    # Sin resultado real todavia: no hay nada que evaluar para esta competicion.
    with session_scope() as db:
        report = evaluate_settled_predictions(db, competition_code=competition_code)
    assert competition_code not in report["competitions"]

    # Simula que el partido ya se jugo de verdad.
    with session_scope() as db:
        db.query(Match).filter_by(id=match_id).update(
            {"home_goals": 2, "away_goals": 1, "status": "finished"}, synchronize_session=False
        )

    with session_scope() as db:
        report = evaluate_settled_predictions(db, competition_code=competition_code)
    assert competition_code in report["competitions"]
    market_reports = report["competitions"][competition_code]
    assert "over_2_5" in market_reports  # 2+1=3 goles: True para over_2_5

    quality = market_reports["over_2_5"]["model_quality"]
    assert quality["n_predictions"] == 1
    assert 0.0 <= quality["brier_score"] <= 1.0

    # over_2_5 tenia cuota real (1.90/1.95) en el proveedor fake -> debe haber estrategia.
    assert market_reports["over_2_5"]["market_strategy"] is not None

    # Prediccion original preservada, nunca borrada por haberse jugado el partido.
    with session_scope() as db:
        remaining = db.query(Match).filter_by(id=match_id).one()
        assert remaining.status == "finished"


def _seed_settled_predictions(
    db, competition_code: str, market: str, pairs: list[tuple[float, bool]]
) -> None:
    """Inserta directamente N (Prediction, PredictionResult) ya liquidados
    para (competition_code, market), sin pasar por el pipeline completo de
    entrenamiento/prediccion -- aqui solo interesa la consulta de
    `market_track_records`, no reprobar el settlement end-to-end (ya
    cubierto en test_settlement.py)."""
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    comp = Competition(code=competition_code, name=competition_code, country="Testland")
    db.add(comp)
    db.flush()
    season = Season(competition_id=comp.id, label="2024-2025")
    db.add(season)
    model_version = ModelVersion(
        name="track-record-test",
        version="v1",
        market_family="goals",
        features=[],
        hyperparameters={},
        metrics={},
        dataset_version="test",
    )
    db.add(model_version)
    db.flush()

    for i, (probability, outcome) in enumerate(pairs):
        match = Match(
            provider="track_record_test",
            provider_id=f"track-record-{competition_code}-{market}-{i}",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() - dt.timedelta(days=1),
            home_team_id=1,
            away_team_id=2,
            status="finished",
            home_goals=1,
            away_goals=0,
        )
        db.add(match)
        db.flush()
        prediction = Prediction(
            match_id=match.id,
            market=market,
            line=2.5,
            selection="over",
            model_version_id=model_version.id,
            model_probability=probability,
            fair_odds=1 / probability,
            confidence=0.8,
            data_quality=0.8,
            explanation={},
            features_used={},
            created_at=dt.datetime.utcnow(),
        )
        db.add(prediction)
        db.flush()
        db.add(
            PredictionResult(
                prediction_id=prediction.id, outcome=outcome, settled_at=dt.datetime.utcnow()
            )
        )
    db.flush()


def test_market_track_records_empty_below_minimum_settled_sample():
    """Menos liquidaciones que MIN_SETTLED_FOR_TRACK_RECORD: nunca se
    inventa un numero sobre una muestra insuficiente, el mercado queda
    AUSENTE del dict (no en 0.0 ni en 1.0)."""
    competition_code = "track-record-too-few"
    pairs = [(0.8, True)] * (MIN_SETTLED_FOR_TRACK_RECORD - 1)
    with session_scope() as db:
        _seed_settled_predictions(db, competition_code, "over_2_5", pairs)

    with session_scope() as db:
        records = market_track_records(db, competition_code=competition_code)
    assert (competition_code, "over_2_5") not in records


def test_market_track_records_well_calibrated_market_scores_near_one():
    competition_code = "track-record-well-calibrated"
    # 80% de probabilidad predicha, ~80% de aciertos reales: calibracion casi perfecta.
    pairs = [(0.8, True)] * 32 + [(0.8, False)] * 8
    with session_scope() as db:
        _seed_settled_predictions(db, competition_code, "over_2_5", pairs)

    with session_scope() as db:
        records = market_track_records(db, competition_code=competition_code)
    assert records[(competition_code, "over_2_5")] > 0.9


def test_market_track_records_poorly_calibrated_market_scores_lower():
    competition_code = "track-record-poorly-calibrated"
    # 90% de probabilidad predicha pero solo 30% de aciertos reales: mala calibracion.
    pairs = [(0.9, True)] * 12 + [(0.9, False)] * 28
    with session_scope() as db:
        _seed_settled_predictions(db, competition_code, "over_2_5", pairs)

    with session_scope() as db:
        records = market_track_records(db, competition_code=competition_code)
    assert records[(competition_code, "over_2_5")] < 0.5


def test_market_track_records_is_per_competition_and_market():
    with session_scope() as db:
        _seed_settled_predictions(
            db, "track-record-scope-a", "over_2_5", [(0.8, True)] * 32 + [(0.8, False)] * 8
        )
        _seed_settled_predictions(
            db, "track-record-scope-b", "btts", [(0.9, True)] * 12 + [(0.9, False)] * 28
        )

    with session_scope() as db:
        scoped = market_track_records(db, competition_code="track-record-scope-a")
    assert ("track-record-scope-a", "over_2_5") in scoped
    assert ("track-record-scope-b", "btts") not in scoped


def test_evaluate_settled_predictions_empty_db_returns_no_competitions():
    with session_scope() as db:
        report = evaluate_settled_predictions(
            db, competition_code="a_competition_that_does_not_exist"
        )
    assert report == {"competitions": {}}
