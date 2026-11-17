"""`scripts/debug_walk_forward_cap_binding.py` -- verificado de verdad
contra la BD de test (mismo motivo que los demas scripts `debug_*`: un
`DetachedInstanceError` no lo detectan `py_compile`/`ruff`).

Construye un escenario DELIBERADAMENTE disenhado para que el techo de edge
SI actue (edge grande -> raramente acierta, desde el principio de la serie
cronologica, con `risk_adjusted_expected_value` crudo positivo) -- asi la
instrumentacion puede demostrar que detecta un "binding" real cuando
ocurre, no solo que informa cero cuando no hay nada que detectar (ese caso
ya lo cubre `test_main_runs_without_a_target_match_id`-style en el otro
script)."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.normalization.competitions import COMPETITIONS
from scripts.debug_walk_forward_cap_binding import main


def _insert_settled_prediction(
    db,
    match_id: int,
    model_probability: float,
    market_odds: float,
    edge: float,
    risk_adjusted_expected_value: float,
    outcome: bool,
    kickoff_utc: dt.datetime,
    competition_id: int,
    season_id: int,
    model_version_id: int,
) -> None:
    db.add(
        Match(
            id=match_id,
            provider="test",
            provider_id=f"walk-forward-cap-binding-test-{match_id}",
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
        model_probability=model_probability,
        market_probability=model_probability - edge,
        market_odds=market_odds,
        market_odds_min=market_odds,
        market_odds_max=market_odds,
        market_odds_median=market_odds,
        edge=edge,
        fair_odds=1.0 / model_probability,
        expected_value=model_probability * market_odds - 1.0,
        risk_adjusted_expected_value=risk_adjusted_expected_value,
        uncertainty_low=max(0.01, model_probability - 0.1),
        uncertainty_high=min(0.99, model_probability + 0.1),
        confidence=0.7,
        data_quality=0.7,
        bookmakers_used=6,
        explanation={"factors": []},
        features_used={"feature_names": []},
        created_at=dt.datetime.utcnow(),
        signal_score=0.1234,
    )
    db.add(prediction)
    db.flush()
    db.add(PredictionResult(prediction_id=prediction.id, outcome=outcome))


def test_main_runs_end_to_end_and_detects_a_real_edge_cap_binding(capsys):
    competition_code = "walk-forward-cap-binding-test-league"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    base = dt.datetime(2024, 1, 1)
    with session_scope() as db:
        comp = Competition(code=competition_code, name=competition_code, country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2024-2025")
        db.add(season)
        model_version = ModelVersion(
            name="walk-forward-cap-binding-test",
            version="v1",
            market_family="goals",
            features=[],
            hyperparameters={},
            metrics={},
            dataset_version="test",
        )
        db.add(model_version)
        db.flush()

        # Relacion edge-vs-acierto fuertemente decreciente desde el
        # principio de la serie: edge pequenho (0.05) acierta siempre;
        # edge grande (0.35) casi nunca -- exactamente el patron real de
        # docs/architecture_audit.md seccion 13.2, pero mas extremo para
        # que el techo tenga que actuar de forma inequivoca en el test.
        for i in range(90):
            is_small_edge = i % 2 == 0
            edge = 0.05 if is_small_edge else 0.35
            model_probability = 0.5
            market_odds = 1.0 / max(model_probability - edge, 0.05)
            outcome = True if is_small_edge else (i % 10 == 0)  # edge grande acierta ~10%
            # EV crudo OPTIMISTA (model_probability*odds-1, siempre > 0 con
            # estos numeros) para que el techo de edge tenga algo que
            # recortar en las filas de edge grande.
            raw_ev = model_probability * market_odds - 1.0
            _insert_settled_prediction(
                db,
                match_id=96000 + i,
                model_probability=model_probability,
                market_odds=market_odds,
                edge=edge,
                risk_adjusted_expected_value=raw_ev,
                outcome=outcome,
                kickoff_utc=base + dt.timedelta(hours=i),
                competition_id=comp.id,
                season_id=season.id,
                model_version_id=model_version.id,
            )

    main(competition=competition_code, min_train_n=30)

    output = capsys.readouterr().out
    assert "n_con_mercado=90" in output
    assert "evaluadas=60" in output  # 90 - min_train_n(30)
    # El propio escenario garantiza EV crudo > 0 en TODAS las filas
    # evaluadas (ver `raw_ev` arriba) -- la unica forma de que el techo de
    # edge actue es que de verdad compare contra la evidencia empirica.
    assert "raw_risk_adjusted_ev > 0 (unicas donde un techo PODRIA actuar): 60" in output
    assert "techo de edge REALMENTE activo (capped < crudo): 0" not in output


def test_main_reports_nothing_to_evaluate_below_minimum_sample(capsys):
    competition_code = "walk-forward-cap-binding-test-league-empty"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    main(competition=competition_code, min_train_n=30)
    output = capsys.readouterr().out
    assert "n_con_mercado=0" in output
    assert "nada que evaluar" in output
