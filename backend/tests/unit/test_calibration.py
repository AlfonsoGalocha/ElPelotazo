from __future__ import annotations

import numpy as np

from backend.app.models.calibration.calibrators import IsotonicCalibrator
from backend.app.models.calibration.metrics import (
    brier_score,
    expected_calibration_error,
    log_loss_score,
    reliability_curve,
)


def test_brier_score_perfect_predictions_is_zero():
    y_true = np.array([True, False, True, False])
    y_prob = np.array([1.0, 0.0, 1.0, 0.0])
    assert brier_score(y_true, y_prob) == 0.0


def test_brier_score_worst_case_is_one():
    y_true = np.array([True, False])
    y_prob = np.array([0.0, 1.0])
    assert brier_score(y_true, y_prob) == 1.0


def test_log_loss_penalizes_confident_wrong_predictions_more():
    y_true = np.array([True, True])
    confident_right = log_loss_score(y_true, np.array([0.99, 0.99]))
    confident_wrong = log_loss_score(y_true, np.array([0.01, 0.01]))
    assert confident_wrong > confident_right


def test_reliability_curve_reports_sample_sizes():
    rng = np.random.default_rng(0)
    y_prob = rng.uniform(0, 1, size=200)
    y_true = rng.uniform(0, 1, size=200) < y_prob  # perfectamente calibrado por construccion
    curve = reliability_curve(y_true, y_prob, n_bins=5)
    assert sum(p["n"] for p in curve) == 200
    for point in curve:
        assert abs(point["predicted_probability"] - point["empirical_frequency"]) < 0.35


def test_isotonic_calibration_improves_a_badly_calibrated_model():
    rng = np.random.default_rng(1)
    true_prob = rng.uniform(0, 1, size=2000)
    y_true = rng.uniform(0, 1, size=2000) < true_prob

    # Modelo mal calibrado: siempre demasiado extremo (overconfident)
    miscalibrated = np.clip(true_prob * 1.8 - 0.4, 0.01, 0.99)

    val, test = miscalibrated[:1000], miscalibrated[1000:]
    y_val, y_test = y_true[:1000], y_true[1000:]

    calibrator = IsotonicCalibrator().fit(val, y_val)
    calibrated_test = calibrator.transform(test)

    ece_before = expected_calibration_error(y_test, test)
    ece_after = expected_calibration_error(y_test, calibrated_test)
    assert ece_after < ece_before
