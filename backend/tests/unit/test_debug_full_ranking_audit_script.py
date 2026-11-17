"""`scripts/debug_full_ranking_audit.py` -- verificado de verdad contra la BD
de test (mismo motivo que `test_debug_edge_vs_hitrate_script.py`: un
`DetachedInstanceError` de SQLAlchemy no lo detectan `py_compile`/`ruff`, y
aqui el script ademas hace un INNER JOIN con `Competition` -- sin una fila
de competicion real, ese join devuelve cero filas aunque las predicciones
existan)."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.normalization.competitions import COMPETITIONS
from scripts.debug_full_ranking_audit import main


def _insert_settled_prediction(
    db,
    match_id: int,
    market: str,
    model_probability: float,
    market_odds: float,
    edge: float,
    outcome: bool,
    bookmakers_used: int,
    competition_id: int,
    season_id: int,
    model_version_id: int,
) -> None:
    db.add(
        Match(
            id=match_id,
            provider="test",
            provider_id=f"full-ranking-audit-test-{match_id}",
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
        market=market,
        line=2.5,
        selection="over",
        model_version_id=model_version_id,
        model_probability=model_probability,
        market_odds=market_odds,
        market_odds_min=market_odds,
        market_odds_max=market_odds,
        market_odds_median=market_odds,
        edge=edge,
        fair_odds=1.0 / model_probability,
        confidence=0.7,
        data_quality=0.7,
        bookmakers_used=bookmakers_used,
        explanation={"factors": []},
        features_used={"feature_names": []},
        created_at=dt.datetime.utcnow(),
        signal_score=0.1234,
    )
    db.add(prediction)
    db.flush()
    db.add(PredictionResult(prediction_id=prediction.id, outcome=outcome))


def test_main_runs_end_to_end_against_real_settled_predictions(capsys):
    competition_code = "full-ranking-audit-test-league"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    with session_scope() as db:
        comp = Competition(code=competition_code, name=competition_code, country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2024-2025")
        db.add(season)
        model_version = ModelVersion(
            name="full-ranking-audit-test",
            version="v1",
            market_family="goals",
            features=[],
            hyperparameters={},
            metrics={},
            dataset_version="test",
        )
        db.add(model_version)
        db.flush()

        for i in range(35):
            _insert_settled_prediction(
                db,
                match_id=95000 + i,
                market="over_2_5",
                model_probability=0.60,
                market_odds=1.90,
                edge=0.05,
                outcome=(i % 2 == 0),
                bookmakers_used=6,
                competition_id=comp.id,
                season_id=season.id,
                model_version_id=model_version.id,
            )
        _insert_settled_prediction(
            db,
            match_id=95100,
            market="home_win",
            model_probability=0.33,
            market_odds=5.50,
            edge=0.15,
            outcome=False,
            bookmakers_used=2,
            competition_id=comp.id,
            season_id=season.id,
            model_version_id=model_version.id,
        )

    main(target_match_id=95100)  # no debe lanzar DetachedInstanceError ni ningun otro error

    output = capsys.readouterr().out
    assert "Total predicciones liquidadas con cuota real:" in output
    assert "1. HIT RATE / ROI POR BUCKET DE EDGE" in output
    assert "2. HIT RATE / ROI POR BUCKET DE CUOTA" in output
    assert "3. HIT RATE / ROI POR MERCADO" in output
    assert "4. HIT RATE / ROI POR COMPETICION" in output
    assert "5. HIT RATE / ROI POR RANGO DE PROBABILIDAD" in output
    assert "6. HIT RATE / ROI SEGUN NUMERO DE BOOKMAKERS" in output
    assert "7. HIT RATE / ROI POR CALIDAD DE MERCADO" in output
    assert "8. CASO match_id=95100" in output
    assert "9. TOP 10 JORNADA ACTUAL" in output


def test_main_runs_without_a_target_match_id(capsys):
    """`--match-id` es opcional -- sin el, la seccion 8 no se imprime pero
    el resto del informe se genera igual (nunca lanza por `target is None`)."""
    main(target_match_id=None)
    output = capsys.readouterr().out
    assert "Total predicciones liquidadas con cuota real:" in output
    assert "8. CASO" not in output


def test_section_9_ranking_call_matches_production_never_passes_odds_curve():
    """Regresion real (seccion 14 de docs/architecture_audit.md): el
    usuario corrio este script tras el rollback de emergencia del techo
    de cuota y el top-10 seguia saliendo con score=0.0000 en TODOS los
    casos -- no porque produccion siguiera rota, sino porque este MISMO
    script seguia pasando `odds_reliability_curve` a `rank_signals_v2`
    en la seccion 9, sin reflejar el rollback. Verificacion estatica
    (nunca mas sutil que leer el texto fuente) de que la llamada de
    ranking de la jornada actual nunca vuelve a incluir ese parametro
    mientras el techo de cuota siga desconectado de produccion."""
    import inspect

    import scripts.debug_full_ranking_audit as module

    source = inspect.getsource(module.main)
    call_start = source.index("included, _ = rank_signals_v2(")
    call_end = source.index(")", call_start)
    call_text = source[call_start:call_end]
    assert "odds_reliability_curve" not in call_text
