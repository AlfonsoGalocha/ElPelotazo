"""Reporte de calibracion antes/despues de aplicar un calibrador post-hoc."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.models.calibration.calibrators import CALIBRATORS
from backend.app.models.calibration.metrics import (
    brier_score,
    expected_calibration_error,
    log_loss_score,
)


def compare_calibration(
    y_true: np.ndarray, y_prob_raw: np.ndarray, method: str = "isotonic", validation_fraction: float = 0.3
) -> dict:
    """Ajusta el calibrador en un sub-split de validacion y mide el efecto en
    un sub-split de test disjunto, para no medir la mejora sobre los mismos
    datos usados para ajustar el calibrador.
    """
    n = len(y_true)
    n_val = int(n * validation_fraction)
    if n_val < 10 or (n - n_val) < 10:
        return {"error": "muestra insuficiente para separar validacion/test de calibracion"}

    val_idx = np.arange(n_val)
    test_idx = np.arange(n_val, n)

    calibrator_cls = CALIBRATORS[method]
    calibrator = calibrator_cls().fit(y_prob_raw[val_idx], y_true[val_idx])
    calibrated_test = calibrator.transform(y_prob_raw[test_idx])

    y_true_test, y_prob_test = y_true[test_idx], y_prob_raw[test_idx]

    return {
        "method": method,
        "n_validation": int(n_val),
        "n_test": int(len(test_idx)),
        "before": {
            "brier_score": brier_score(y_true_test, y_prob_test),
            "log_loss": log_loss_score(y_true_test, y_prob_test),
            "expected_calibration_error": expected_calibration_error(y_true_test, y_prob_test),
        },
        "after": {
            "brier_score": brier_score(y_true_test, calibrated_test),
            "log_loss": log_loss_score(y_true_test, calibrated_test),
            "expected_calibration_error": expected_calibration_error(y_true_test, calibrated_test),
        },
    }


def calibration_report_for_table(
    table: pd.DataFrame,
    model_factory,
    market_key: str,
    method: str = "isotonic",
    min_train_seasons: int = 2,
) -> dict:
    """Punto de entrada real (antes `compare_calibration` existia pero
    nada la llamaba en todo el proyecto -- seccion 19 del pedido de
    revision integral). Usa las probabilidades OUT-OF-FOLD del backtest
    walk-forward (`run_walk_forward_backtest`), nunca probabilidades de un
    modelo entrenado con esos mismos partidos: si se comparara raw vs
    calibrado sobre probabilidades in-sample, el "antes" saldria
    artificialmente bien calibrado (el modelo ya vio esos resultados),
    sesgando la comparacion a favor de "la calibracion no hace falta".
    """
    # Import diferido: evita un ciclo (engine.py no necesita conocer este
    # modulo, pero este modulo si necesita el motor de backtest).
    from backend.app.backtesting.engine import run_walk_forward_backtest

    results = run_walk_forward_backtest(table, model_factory, market_key, min_train_seasons=min_train_seasons)
    if not results:
        return {"error": "sin folds de backtest suficientes para este mercado/competicion"}

    y_true = np.concatenate([r.y_true for r in results])
    y_prob_raw = np.concatenate([r.y_prob for r in results])
    report = compare_calibration(y_true, y_prob_raw, method=method)
    report["market"] = market_key
    report["n_folds"] = len(results)
    return report
