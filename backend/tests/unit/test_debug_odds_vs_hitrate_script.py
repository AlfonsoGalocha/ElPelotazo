"""`scripts/debug_odds_vs_hitrate.py` -- verificado de verdad contra la BD de
test (mismo motivo que `test_debug_edge_vs_hitrate_script.py`: un
`DetachedInstanceError` de SQLAlchemy no lo detectan `py_compile`/`ruff`)."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult
from scripts.debug_odds_vs_hitrate import main


def _insert_settled_prediction(
    match_id: int, model_probability: float, market_odds: float, edge: float, outcome: bool
) -> None:
    with session_scope() as db:
        db.add(
            Match(
                id=match_id,
                provider="test",
                provider_id=f"odds-vs-hitrate-test-{match_id}",
                competition_id=1,
                season_id=1,
                kickoff_utc=dt.datetime.utcnow(),
                home_team_id=1,
                away_team_id=1,
                status="finished",
            )
        )
        db.flush()
        prediction = Prediction(
            match_id=match_id,
            market="over_2_5",
            line=2.5,
            selection="over",
            model_version_id=1,
            model_probability=model_probability,
            market_odds=market_odds,
            edge=edge,
            fair_odds=1.0 / model_probability,
            confidence=0.7,
            data_quality=0.7,
            explanation={"factors": []},
            features_used={"feature_names": []},
            created_at=dt.datetime.utcnow(),
        )
        db.add(prediction)
        db.flush()
        db.add(PredictionResult(prediction_id=prediction.id, outcome=outcome))


def test_main_runs_end_to_end_against_real_settled_predictions(capsys):
    _insert_settled_prediction(90101, 0.60, 1.90, 0.07, True)
    _insert_settled_prediction(90102, 0.33, 5.50, 0.15, False)

    main()  # no debe lanzar DetachedInstanceError ni ningun otro error

    output = capsys.readouterr().out
    assert "Total predicciones liquidadas con cuota:" in output
    assert "SOLO con edge >= 15pp" in output
    assert "SOLO con edge < 15pp" in output
