"""Reporte de calibracion antes/despues de aplicar un calibrador post-hoc."""

from __future__ import annotations

import numpy as np

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
