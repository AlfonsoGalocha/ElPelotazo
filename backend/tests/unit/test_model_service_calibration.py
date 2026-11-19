"""`_fit_validated_isotonic_calibrator` (services/model_service.py) -- bug
real reportado por el usuario en produccion (captura de pantalla,
2026-11-19): Bundesliga, "Ambos equipos marcan" -- el modelo crudo
estimaba 78.5%, pero `calibrated_probability` salia en 97.2% (+18.7pp),
sin que esa correccion estuviera respaldada por ninguna mejora real fuera
de muestra. Antes de este fix, el pipeline de entrenamiento ajustaba
Isotonic Regression SIEMPRE que hubiera suficientes partidos en el
holdout GLOBAL, sin comprobar nunca si calibrar mejoraba algo para ESE
mercado concreto -- Isotonic puede sobreajustar un cluster pequenho de la
zona de probabilidad alta y quedarse pegado a ese valor extremo por
monotonicidad."""

from __future__ import annotations

import numpy as np

from backend.app.services.model_service import (
    MIN_CALIBRATION_VALIDATION_SAMPLES,
    _fit_validated_isotonic_calibrator,
)


def test_calibrator_rejected_when_validation_cluster_does_not_generalize():
    """Reproduce el bug real: en el sub-split de VALIDACION, todas las
    predicciones con p=0.5 acertaron (por casualidad/muestra pequenha) --
    Isotonic aprenderia a mapear 0.5 -> ~0.93. En el sub-split de TEST
    (datos nuevos, nunca vistos por el calibrador), la tasa real en p=0.5
    es 50/50 de verdad: aplicar esa calibracion empeora el Brier score
    frente a dejar la probabilidad cruda tal cual -- debe rechazarse."""
    n_val, n_test = 30, 70
    p_val = np.full(n_val, 0.5)
    y_val = np.array([True] * 28 + [False] * 2)  # cluster "casi perfecto" por azar
    p_test = np.full(n_test, 0.5)
    y_test = np.array([i % 2 == 0 for i in range(n_test)])  # 50/50 real

    p_ensemble = np.concatenate([p_val, p_test])
    y_true = np.concatenate([y_val, y_test])

    calibrator, report = _fit_validated_isotonic_calibrator(p_ensemble, y_true)

    assert calibrator is None
    assert report["accepted"] is False
    assert report["brier_after"] > report["brier_before"]


def test_calibrator_accepted_when_it_genuinely_improves_brier_score():
    """Caso contrario: el modelo esta SISTEMATICAMENTE mal calibrado (no
    solo en el sub-split de validacion) -- la correccion isotonic SI debe
    generalizar al sub-split de test y reducir el Brier score de verdad.
    Mismo patron de datos que
    test_calibration.py::test_compare_calibration_reports_before_and_after_on_disjoint_split."""
    rng = np.random.default_rng(2)
    n = 2000
    true_prob = rng.uniform(0, 1, size=n)
    y_true = rng.uniform(0, 1, size=n) < true_prob
    miscalibrated = np.clip(true_prob * 1.8 - 0.4, 0.01, 0.99)

    calibrator, report = _fit_validated_isotonic_calibrator(miscalibrated, y_true)

    assert calibrator is not None
    assert report["accepted"] is True
    assert report["brier_after"] < report["brier_before"]


def test_calibrator_rejected_with_insufficient_sample_for_validation_split():
    """Menos observaciones que MIN_CALIBRATION_VALIDATION_SAMPLES en
    cualquiera de los dos sub-splits: nunca se inventa una validacion
    sobre una muestra demasiado pequenha para ser fiable."""
    n = 2 * MIN_CALIBRATION_VALIDATION_SAMPLES - 1  # deja al menos un lado corto
    p_ensemble = np.linspace(0.1, 0.9, n)
    y_true = p_ensemble > 0.5

    calibrator, report = _fit_validated_isotonic_calibrator(p_ensemble, y_true)

    assert calibrator is None
    assert report["accepted"] is False
    assert "insuficiente" in report["reason"]


def test_isotonic_temporal_split_does_not_break_simultaneous_kickoffs():
    y = np.tile([0, 1], 30)
    p = np.tile([0.25, 0.75], 30)
    dates = np.repeat(np.arange(6), 10)
    _, report = _fit_validated_isotonic_calibrator(p, y, dates)
    assert report["n_validation"] == 10
    assert report["n_test"] == 50
    assert report["log_loss_after"] <= report["log_loss_before"]
