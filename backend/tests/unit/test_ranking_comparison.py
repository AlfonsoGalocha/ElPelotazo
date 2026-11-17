"""Fase 7 de la revision "motor profesional 2.0": comparar ROI/win-rate/
Brier real de v1 (`signal_score` ya congelado) vs v2 (`signal_score_v2`)
sobre predicciones YA LIQUIDADAS, restringido al TOP-N de cada estrategia
-- sin reentrenar nada, sin riesgo de leakage (ver docstring del modulo).
"""

from __future__ import annotations

import datetime as dt

from backend.app.backtesting.ranking_comparison import compare_ranking_strategies
from backend.app.db.models.modeling import Prediction, PredictionResult


def _prediction(id: int, **overrides) -> Prediction:
    defaults = dict(
        id=id,
        match_id=id,
        market="over_2_5",
        line=2.5,
        selection="over",
        model_version_id=1,
        model_probability=0.6,
        market_probability=0.5,
        market_odds=1.9,
        fair_odds=1.67,
        edge=0.10,
        expected_value=0.14,
        risk_adjusted_expected_value=0.05,
        confidence=0.7,
        data_quality=0.7,
        uncertainty_low=0.55,
        uncertainty_high=0.65,
        explanation={"factors": []},
        features_used={"feature_names": []},
        created_at=dt.datetime.utcnow(),
        signal_score=0.1,
    )
    defaults.update(overrides)
    return Prediction(**defaults)


def _result(outcome: bool) -> PredictionResult:
    return PredictionResult(outcome=outcome)


def test_compare_ranking_strategies_reports_both_v1_and_v2_slices():
    pairs = [
        (_prediction(1, signal_score=0.30), _result(True)),
        (_prediction(2, signal_score=0.10), _result(False)),
        (_prediction(3, signal_score=0.05), _result(True)),
    ]
    report = compare_ranking_strategies(pairs, top_n_values=(2,))

    strategies = {s.strategy for s in report.slices}
    assert strategies == {"v1", "v2"}
    top_n_labels = {s.top_n for s in report.slices}
    assert top_n_labels == {"2", "all"}


def test_compare_ranking_strategies_excludes_predictions_without_market_odds():
    pairs = [
        (_prediction(1, market_odds=None), _result(True)),
        (_prediction(2), _result(True)),
    ]
    report = compare_ranking_strategies(pairs, top_n_values=(5,))
    assert report.n_settled_with_market == 1
    all_slice_v1 = next(s for s in report.slices if s.strategy == "v1" and s.top_n == "all")
    assert all_slice_v1.n_bets == 1


def test_compare_ranking_strategies_top_n_uses_the_highest_scored_first():
    """El TOP-1 de v1 (por `signal_score` congelado) debe ser exactamente
    la prediccion con mayor `signal_score`, acertada o no."""
    winner_high_score = _prediction(1, signal_score=0.9)
    loser_low_score = _prediction(2, signal_score=0.1)
    pairs = [(winner_high_score, _result(False)), (loser_low_score, _result(True))]

    report = compare_ranking_strategies(pairs, top_n_values=(1,))
    top1_v1 = next(s for s in report.slices if s.strategy == "v1" and s.top_n == "1")
    assert top1_v1.n_bets == 1
    assert top1_v1.win_rate == 0.0  # el de mayor score perdio -- se refleja tal cual, no se maquilla


def test_compare_ranking_strategies_v2_can_rank_differently_from_v1():
    """Construye un caso donde v1 (score congelado alto) y v2 (recalculado
    con EV ajustado al riesgo negativo) discrepan sobre cual es la "mejor"
    -- exactamente el escenario que motivo signal_score_v2 (ver
    test_ranking.py::test_signal_score_v2_never_favors_the_getafe_style_extreme_case)."""
    extreme_but_shaky = _prediction(
        1,
        signal_score=0.50,  # v1: alto, domina el ranking v1
        model_probability=0.819,
        market_odds=2.39,
        risk_adjusted_expected_value=-0.04,  # v2: EV ajustado negativo -> score_v2 = 0
        uncertainty_low=0.40,
        uncertainty_high=0.95,
    )
    solid_and_supported = _prediction(
        2,
        signal_score=0.05,  # v1: bajo
        model_probability=0.65,
        market_odds=1.75,
        risk_adjusted_expected_value=0.05,  # v2: EV ajustado positivo -> score_v2 > 0
        uncertainty_low=0.60,
        uncertainty_high=0.70,
    )
    pairs = [(extreme_but_shaky, _result(False)), (solid_and_supported, _result(True))]

    report = compare_ranking_strategies(pairs, top_n_values=(1,))
    top1_v1 = next(s for s in report.slices if s.strategy == "v1" and s.top_n == "1")
    top1_v2 = next(s for s in report.slices if s.strategy == "v2" and s.top_n == "1")

    assert top1_v1.win_rate == 0.0  # v1 eligio la extrema (perdio)
    assert top1_v2.win_rate == 1.0  # v2 eligio la solida (gano)


def test_compare_ranking_strategies_computes_pnl_and_drawdown():
    pairs = [
        (_prediction(1, market_odds=2.0, signal_score=0.9), _result(True)),  # pnl +1.0
        (_prediction(2, market_odds=2.0, signal_score=0.8), _result(False)),  # pnl -1.0
    ]
    report = compare_ranking_strategies(pairs, top_n_values=(2,))
    slice_all_v1 = next(s for s in report.slices if s.strategy == "v1" and s.top_n == "2")
    assert slice_all_v1.total_pnl == 0.0
    assert slice_all_v1.roi == 0.0
    assert slice_all_v1.max_drawdown <= 0.0


def test_compare_ranking_strategies_reports_empty_when_nothing_settled_with_market():
    report = compare_ranking_strategies([], top_n_values=(5,))
    assert report.n_settled_with_market == 0
    assert report.slices == []
