from __future__ import annotations

from backend.app.prediction.confidence import ConfidenceInputs, confidence_score, signal_tier


def test_confidence_is_bounded_between_0_and_1():
    inputs = ConfidenceInputs(
        calibration_error=0.5, sample_size_score=0.5, model_agreement=0.5, data_quality=0.5
    )
    score = confidence_score(inputs)
    assert 0.0 <= score <= 1.0


def test_confidence_increases_with_better_calibration():
    good_calibration = ConfidenceInputs(0.02, 0.8, 0.8, 0.8)
    bad_calibration = ConfidenceInputs(0.4, 0.8, 0.8, 0.8)
    assert confidence_score(good_calibration) > confidence_score(bad_calibration)


def test_signal_tier_requires_both_confidence_and_data_quality():
    assert signal_tier(confidence=0.9, data_quality=0.9) == "HIGH_DATA_SUPPORT"
    assert signal_tier(confidence=0.9, data_quality=0.3) != "HIGH_DATA_SUPPORT"
    assert signal_tier(confidence=0.2, data_quality=0.2) == "LOW_DATA_SUPPORT"


def test_a_huge_edge_alone_does_not_imply_high_confidence():
    """Seccion 16: un edge grande de un modelo mal calibrado no debe traducirse
    en confianza alta solo por eso."""
    poorly_calibrated_but_big_edge = ConfidenceInputs(
        calibration_error=0.35, sample_size_score=0.2, model_agreement=0.1, data_quality=0.3
    )
    assert confidence_score(poorly_calibrated_but_big_edge) < 0.5
