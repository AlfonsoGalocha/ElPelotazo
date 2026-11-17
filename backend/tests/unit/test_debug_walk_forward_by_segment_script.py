"""`scripts/debug_walk_forward_by_segment.py` (seccion 15.10 de
docs/architecture_audit.md) y el nuevo filtro `min_kickoff`/`max_kickoff`
de `load_settled_predictions_with_kickoff` -- verificados contra la BD de
test real, mismo motivo que los demas scripts `debug_*` (un
`DetachedInstanceError` no lo detectan `py_compile`/`ruff`)."""

from __future__ import annotations

import datetime as dt

from backend.app.backtesting.ranking_comparison import load_settled_predictions_with_kickoff
from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.normalization.competitions import COMPETITIONS
from scripts.debug_walk_forward_by_segment import main


def _insert_settled_prediction(
    db, match_id: int, kickoff_utc: dt.datetime, competition_id: int, season_id: int, model_version_id: int
) -> None:
    db.add(
        Match(
            id=match_id,
            provider="test",
            provider_id=f"walk-forward-by-segment-test-{match_id}",
            competition_id=competition_id,
            season_id=season_id,
            kickoff_utc=kickoff_utc,
            home_team_id=1,
            away_team_id=1,
            status="finished",
        )
    )
    db.flush()
    prediction = Prediction(
        match_id=match_id,
        market="home_win",
        selection="home",
        model_version_id=model_version_id,
        model_probability=0.5,
        market_odds=2.0,
        edge=0.05,
        fair_odds=2.0,
        risk_adjusted_expected_value=0.1,
        uncertainty_low=0.4,
        uncertainty_high=0.6,
        confidence=0.7,
        data_quality=0.7,
        explanation={"factors": []},
        features_used={"feature_names": []},
        created_at=dt.datetime.utcnow(),
        signal_score=0.1,
    )
    db.add(prediction)
    db.flush()
    db.add(PredictionResult(prediction_id=prediction.id, outcome=True))


def test_load_settled_predictions_with_kickoff_filters_by_min_and_max_kickoff():
    competition_code = "walk-forward-by-segment-test-league"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    with session_scope() as db:
        comp = Competition(code=competition_code, name=competition_code, country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2024-2025")
        db.add(season)
        model_version = ModelVersion(
            name="walk-forward-by-segment-test",
            version="v1",
            market_family="goals",
            features=[],
            hyperparameters={},
            metrics={},
            dataset_version="test",
        )
        db.add(model_version)
        db.flush()

        _insert_settled_prediction(
            db, 98000, dt.datetime(2024, 1, 1), comp.id, season.id, model_version.id
        )
        _insert_settled_prediction(
            db, 98001, dt.datetime(2024, 6, 1), comp.id, season.id, model_version.id
        )
        _insert_settled_prediction(
            db, 98002, dt.datetime(2024, 12, 1), comp.id, season.id, model_version.id
        )

    with session_scope() as db:
        all_rows = load_settled_predictions_with_kickoff(db, competition_code=competition_code)
        assert len(all_rows) == 3

        older_only = load_settled_predictions_with_kickoff(
            db, competition_code=competition_code, max_kickoff=dt.datetime(2024, 6, 1)
        )
        assert {k.month for _p, _r, k in older_only} == {1}

        recent_only = load_settled_predictions_with_kickoff(
            db, competition_code=competition_code, min_kickoff=dt.datetime(2024, 6, 1)
        )
        assert {k.month for _p, _r, k in recent_only} == {6, 12}


def test_main_runs_end_to_end_without_raising(capsys):
    main()

    output = capsys.readouterr().out
    assert "Total predicciones liquidadas con cuota real:" in output
    assert "TODO EL HISTORICO" in output
    assert "COMPETICION=" in output
    assert "PRIMERA MITAD CRONOLOGICA" in output
    assert "SEGUNDA MITAD CRONOLOGICA" in output
