"""Motor de backtest walk-forward del ENSEMBLE real (ver
backend/app/backtesting/ensemble_engine.py) -- a diferencia de
`run_walk_forward_backtest` (que solo sabe entrenar un `GoalsModel` suelto,
ver test_calibration.py), este SI replica Dixon-Coles + clasificador ML +
pesos de mezcla aprendidos + calibracion (isotonic y Platt), fold a fold,
tal y como lo sirve produccion (services/model_service.py)."""

from __future__ import annotations

import numpy as np

from backend.app.backtesting.calibration import ensemble_calibration_report
from backend.app.backtesting.ensemble_engine import (
    _group_for_market,
    run_ensemble_walk_forward_backtest,
)
from backend.app.features.goals import build_match_feature_table
from backend.tests.fixtures.synthetic import generate_synthetic_matches


# n_teams=10 (90 partidos/temporada) para superar MIN_SELECTION_MATCHES
# (=MIN_CALIBRATION_MATCHES=60, ver model_service.py): con menos partidos
# por temporada, NINGUN fold produciria resultados (comportamiento
# correcto -- se prefiere "sin folds" a calibrar sobre una muestra
# insuficiente), pero entonces no se podria probar el camino feliz.
def _synthetic_table(n_seasons: int = 5, seed: int = 11):
    matches = generate_synthetic_matches(n_teams=10, n_seasons=n_seasons, seed=seed)
    return build_match_feature_table(matches)


def test_group_for_market_identifies_match_result_group():
    assert _group_for_market("home_win") == ("home_win", "draw", "away_win")
    assert _group_for_market("draw") == ("home_win", "draw", "away_win")


def test_group_for_market_identifies_over_under_group():
    assert _group_for_market("over_2_5") == ("over_2_5", "under_2_5")


def test_group_for_market_treats_ungrouped_market_as_singleton():
    assert _group_for_market("btts") == ("btts",)


def test_run_ensemble_backtest_produces_one_fold_per_eligible_test_season():
    table = _synthetic_table(n_seasons=5)
    results = run_ensemble_walk_forward_backtest(table, "home_win", min_train_seasons=2)

    # 5 temporadas, min_train_seasons=2 -> folds de test en las temporadas
    # 3a, 4a y 5a (la 1a y 2a solo sirven para entrenar/seleccionar).
    assert len(results) == 3
    test_seasons = [r.fold.test_season for r in results]
    assert test_seasons == sorted(test_seasons)  # orden cronologico


def test_run_ensemble_backtest_never_leaks_test_fold_into_fit_or_selection():
    """El fold de test de una iteracion nunca puede ser parte de
    `train_seasons` de un fold ANTERIOR (walk-forward real, no aleatorio)."""
    table = _synthetic_table(n_seasons=5)
    results = run_ensemble_walk_forward_backtest(table, "home_win", min_train_seasons=2)

    for result in results:
        assert result.fold.test_season not in result.fold.train_seasons


def test_run_ensemble_backtest_raw_probabilities_are_valid_probabilities():
    table = _synthetic_table(n_seasons=5)
    results = run_ensemble_walk_forward_backtest(table, "home_win", min_train_seasons=2)
    assert len(results) > 0
    for result in results:
        assert np.all(result.p_raw >= 0.0)
        assert np.all(result.p_raw <= 1.0)
        assert len(result.p_raw) == len(result.y_true)
        for method, p_calibrated in result.p_calibrated.items():
            assert method in ("isotonic", "platt")
            assert len(p_calibrated) == len(result.y_true)


def test_run_ensemble_backtest_calls_on_fold_start_for_every_fold():
    """Bug real reportado por el usuario: sin señal de progreso, el
    comando CLI parecia colgado contra datos reales (reentrena el
    ensemble entero por fold) y lo cancelo con Ctrl+C antes de terminar."""
    table = _synthetic_table(n_seasons=5)
    calls: list[tuple[int, int, str]] = []

    run_ensemble_walk_forward_backtest(
        table,
        "home_win",
        min_train_seasons=2,
        on_fold_start=lambda idx, total, season: calls.append((idx, total, season)),
    )

    assert len(calls) == 3  # mismos 3 folds que produce resultados (ver test de arriba)
    assert [c[0] for c in calls] == [1, 2, 3]
    assert all(c[1] == 3 for c in calls)
    assert [c[2] for c in calls] == sorted(c[2] for c in calls)  # orden cronologico


def test_run_ensemble_backtest_skips_folds_without_enough_data():
    table = _synthetic_table(n_seasons=1)
    results = run_ensemble_walk_forward_backtest(table, "home_win", min_train_seasons=2)
    assert results == []


def test_run_ensemble_backtest_works_for_an_ungrouped_market():
    """btts no pertenece a ningun MUTUALLY_EXCLUSIVE_GROUPS -- ejercita el
    camino de `pick_best_candidate` en vez de `_choose_ml_source_per_group`."""
    table = _synthetic_table(n_seasons=5)
    results = run_ensemble_walk_forward_backtest(table, "btts", min_train_seasons=2)
    assert len(results) == 3
    for result in results:
        assert np.all((result.p_raw >= 0.0) & (result.p_raw <= 1.0))


def test_ensemble_calibration_report_reports_error_when_no_folds():
    table = _synthetic_table(n_seasons=1)
    report = ensemble_calibration_report(table, "home_win", min_train_seasons=2)
    assert "error" in report


def test_ensemble_calibration_report_compares_raw_isotonic_and_platt():
    table = _synthetic_table(n_seasons=5)
    report = ensemble_calibration_report(table, "home_win", min_train_seasons=2)

    assert "error" not in report
    assert report["market"] == "home_win"
    assert report["n_folds"] == 3
    for block_name in ("raw", "isotonic", "platt"):
        block = report[block_name]
        assert "error" not in block, f"{block_name}: {block.get('error')}"
        # Bug real detectado al correr el comando CLI contra datos reales:
        # el bloque "raw" no llevaba su propio "n_test" (solo el
        # `report["n_test"]` de nivel superior), y `cli.py::_fmt` asume
        # que CUALQUIER bloque sin "error" lo tiene -> KeyError en
        # produccion. Los 3 bloques deben tener la MISMA forma.
        assert "n_test" in block, f"{block_name}: falta n_test"
        for metric in ("brier_score", "log_loss", "expected_calibration_error"):
            assert block[metric] >= 0.0
