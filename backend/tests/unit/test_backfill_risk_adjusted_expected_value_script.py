"""`scripts/backfill_risk_adjusted_expected_value.py` -- verificado contra
la BD de test real (mismo motivo que los demas scripts `debug_*`/de
mutacion puntual: hay que probar el comportamiento end-to-end, no solo que
compile). Cubre exactamente las garantias prometidas en el docstring del
script: rellena SOLO lo que esta NULL con suficiente dato, nunca toca lo
ya poblado, nunca inventa nada sin uncertainty_low/market_odds, y es
idempotente."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.prediction.edge import compute_expected_value
from scripts.backfill_risk_adjusted_expected_value import main


def _setup_competition_season_model(db, competition_code: str):
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    comp = Competition(code=competition_code, name=competition_code, country="Testland")
    db.add(comp)
    db.flush()
    season = Season(competition_id=comp.id, label="2024-2025")
    db.add(season)
    model_version = ModelVersion(
        name="backfill-test",
        version="v1",
        market_family="goals",
        features=[],
        hyperparameters={},
        metrics={},
        dataset_version="test",
    )
    db.add(model_version)
    db.flush()
    return comp, season, model_version


def _insert_prediction(
    db,
    match_id: int,
    competition_id: int,
    season_id: int,
    model_version_id: int,
    *,
    uncertainty_low: float | None,
    market_odds: float | None,
    risk_adjusted_expected_value: float | None,
) -> int:
    db.add(
        Match(
            id=match_id,
            provider="test",
            provider_id=f"backfill-test-{match_id}",
            competition_id=competition_id,
            season_id=season_id,
            kickoff_utc=dt.datetime.utcnow(),
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
        market_odds=market_odds,
        edge=0.05,
        fair_odds=2.0,
        uncertainty_low=uncertainty_low,
        uncertainty_high=0.9,
        risk_adjusted_expected_value=risk_adjusted_expected_value,
        confidence=0.7,
        data_quality=0.7,
        explanation={"factors": []},
        features_used={"feature_names": []},
        created_at=dt.datetime.utcnow(),
        signal_score=0.1,
    )
    db.add(prediction)
    db.flush()
    return prediction.id


def test_backfill_fills_null_rows_using_the_exact_production_formula(capsys):
    competition_code = "backfill-test-league-fills-null"
    with session_scope() as db:
        comp, season, model_version = _setup_competition_season_model(db, competition_code)
        pre_feature_id = _insert_prediction(
            db,
            match_id=97000,
            competition_id=comp.id,
            season_id=season.id,
            model_version_id=model_version.id,
            uncertainty_low=0.40,
            market_odds=2.50,
            risk_adjusted_expected_value=None,  # hueco real: predicha antes del despliegue
        )

    main()

    with session_scope() as db:
        refreshed = db.get(Prediction, pre_feature_id)
        assert refreshed.risk_adjusted_expected_value == compute_expected_value(0.40, 2.50)

    output = capsys.readouterr().out
    assert "Rellenadas" in output


def test_backfill_never_overwrites_an_already_populated_value(capsys):
    competition_code = "backfill-test-league-preserves-populated"
    with session_scope() as db:
        comp, season, model_version = _setup_competition_season_model(db, competition_code)
        already_populated_id = _insert_prediction(
            db,
            match_id=97100,
            competition_id=comp.id,
            season_id=season.id,
            model_version_id=model_version.id,
            uncertainty_low=0.40,
            market_odds=2.50,
            # Valor YA calculado por el pipeline en vivo -- aunque no
            # coincida con compute_expected_value(0.40, 2.50), el
            # backfill NUNCA debe tocarlo (nunca "corrige" produccion).
            risk_adjusted_expected_value=0.9999,
        )

    main()

    with session_scope() as db:
        refreshed = db.get(Prediction, already_populated_id)
        assert refreshed.risk_adjusted_expected_value == 0.9999


def test_backfill_never_invents_a_value_without_uncertainty_low_or_market_odds(capsys):
    competition_code = "backfill-test-league-no-invent"
    with session_scope() as db:
        comp, season, model_version = _setup_competition_season_model(db, competition_code)
        missing_uncertainty_id = _insert_prediction(
            db,
            match_id=97200,
            competition_id=comp.id,
            season_id=season.id,
            model_version_id=model_version.id,
            uncertainty_low=None,
            market_odds=2.50,
            risk_adjusted_expected_value=None,
        )
        missing_odds_id = _insert_prediction(
            db,
            match_id=97201,
            competition_id=comp.id,
            season_id=season.id,
            model_version_id=model_version.id,
            uncertainty_low=0.40,
            market_odds=None,
            risk_adjusted_expected_value=None,
        )

    main()

    with session_scope() as db:
        assert db.get(Prediction, missing_uncertainty_id).risk_adjusted_expected_value is None
        assert db.get(Prediction, missing_odds_id).risk_adjusted_expected_value is None


def test_backfill_is_idempotent(capsys):
    """La propiedad real de idempotencia es sobre LA FILA, no sobre el
    conteo global impreso (que, contra el fichero SQLite compartido de
    toda la sesion de pytest, puede incluir filas de OTROS tests que
    tambien cumplan la condicion -- mismo motivo documentado en
    `_isolated_db_session` de `test_evaluation_service.py`): tras la
    primera pasada la fila queda rellenada; una segunda pasada no debe
    cambiarla."""
    competition_code = "backfill-test-league-idempotent"
    with session_scope() as db:
        comp, season, model_version = _setup_competition_season_model(db, competition_code)
        prediction_id = _insert_prediction(
            db,
            match_id=97300,
            competition_id=comp.id,
            season_id=season.id,
            model_version_id=model_version.id,
            uncertainty_low=0.40,
            market_odds=2.50,
            risk_adjusted_expected_value=None,
        )

    main()
    with session_scope() as db:
        value_after_first_run = db.get(Prediction, prediction_id).risk_adjusted_expected_value
    assert value_after_first_run == compute_expected_value(0.40, 2.50)

    main()  # segunda pasada: la condicion NULL ya no se cumple para esta fila
    with session_scope() as db:
        value_after_second_run = db.get(Prediction, prediction_id).risk_adjusted_expected_value
    assert value_after_second_run == value_after_first_run
