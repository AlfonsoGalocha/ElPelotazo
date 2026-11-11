from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.monitoring.drift import (
    PSI_MODERATE_THRESHOLD,
    PSI_SIGNIFICANT_THRESHOLD,
    feature_drift_report,
    population_stability_index,
    psi_tier,
)


def test_psi_is_near_zero_for_identical_distributions():
    rng = np.random.default_rng(0)
    reference = rng.normal(0, 1, 1000)
    current = rng.normal(0, 1, 1000)
    psi = population_stability_index(reference, current)
    assert psi < PSI_MODERATE_THRESHOLD


def test_psi_is_large_for_a_shifted_distribution():
    rng = np.random.default_rng(1)
    reference = rng.normal(0, 1, 1000)
    current = rng.normal(5, 1, 1000)  # desplazamiento grande, sin solapamiento real
    psi = population_stability_index(reference, current)
    assert psi >= PSI_SIGNIFICANT_THRESHOLD


def test_psi_grows_monotonically_with_shift_magnitude():
    rng = np.random.default_rng(2)
    reference = rng.normal(0, 1, 2000)
    small_shift = population_stability_index(reference, rng.normal(0.2, 1, 2000))
    large_shift = population_stability_index(reference, rng.normal(2.0, 1, 2000))
    assert large_shift > small_shift


def test_psi_is_nan_when_reference_has_no_variability():
    reference = np.full(100, 5.0)
    current = np.array([1.0, 2.0, 3.0])
    psi = population_stability_index(reference, current)
    assert psi != psi  # nan


def test_psi_is_nan_on_empty_arrays():
    assert population_stability_index(
        np.array([]), np.array([1.0, 2.0])
    ) != population_stability_index(np.array([]), np.array([1.0, 2.0]))


def test_psi_tier_boundaries():
    assert psi_tier(0.0) == "STABLE"
    assert psi_tier(0.05) == "STABLE"
    assert psi_tier(0.15) == "MODERATE"
    assert psi_tier(0.30) == "SIGNIFICANT"
    assert psi_tier(float("nan")) == "UNKNOWN"


def _table_with_feature(values: np.ndarray) -> pd.DataFrame:
    n = len(values)
    return pd.DataFrame(
        {
            "match_id": range(n),
            "date": pd.date_range("2020-01-01", periods=n, freq="D"),
            "home_goals": np.ones(n),
            "away_goals": np.zeros(n),
            "some_feature": values,
        }
    )


def test_feature_drift_report_insufficient_data_below_double_window():
    table = _table_with_feature(np.zeros(50))
    report = feature_drift_report(table, ["some_feature"], current_window=50)
    assert report["status"] == "insufficient_data"
    assert report["required"] == 100


def test_feature_drift_report_flags_a_shifted_feature():
    rng = np.random.default_rng(3)
    stable_part = rng.normal(0, 1, 150)
    shifted_part = rng.normal(6, 1, 50)  # los ultimos 50 partidos cambian de regimen
    values = np.concatenate([stable_part, shifted_part])
    table = _table_with_feature(values)

    report = feature_drift_report(table, ["some_feature"], current_window=50)
    assert report["status"] == "ok"
    assert "some_feature" in report["features_with_significant_drift"]
    assert report["features"]["some_feature"]["tier"] == "SIGNIFICANT"


def test_feature_drift_report_no_drift_for_a_stable_feature():
    rng = np.random.default_rng(4)
    values = rng.normal(0, 1, 200)
    table = _table_with_feature(values)

    report = feature_drift_report(table, ["some_feature"], current_window=50)
    assert report["status"] == "ok"
    assert "some_feature" not in report["features_with_significant_drift"]


def test_feature_drift_report_ignores_unfinished_matches():
    rng = np.random.default_rng(5)
    values = rng.normal(0, 1, 200)
    table = _table_with_feature(values)
    table.loc[0, "home_goals"] = np.nan  # partido sin jugar: no debe contar en ninguna ventana

    report = feature_drift_report(table, ["some_feature"], current_window=50)
    assert report["n_reference"] + report["n_current"] == 199
