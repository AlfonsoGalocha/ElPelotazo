"""Seccion 2/27 del brief: mercados mutuamente excluyentes no deben poder
mostrarse simultaneamente como senhales fuertes contradictorias.

Cubre dos niveles:
1. `renormalize_mutually_exclusive_groups` en aislado (unidad pura).
2. El pipeline completo `predict_markets_for_table` con un `ml_model` de
   mentira que deliberadamente devuelve probabilidades ML DISTINTAS de las
   estadisticas para forzar exactamente el escenario que rompia la suma a 1
   antes del fix (ensemble = mezcla de dos fuentes independientes).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.app.prediction.market_labels import (
    MARKET_DEFINITIONS,
    MUTUALLY_EXCLUSIVE_GROUPS,
    renormalize_mutually_exclusive_groups,
)
from backend.app.prediction.predictor import predict_markets_for_table


def test_renormalize_forces_group_to_sum_to_one_even_if_inputs_disagree():
    # Antes del fix: over_2_5=0.72 y under_2_5=0.68 podian coexistir (suma
    # 1.40) exactamente el ejemplo del brief (seccion 2).
    probs = {"over_2_5": np.array([0.72]), "under_2_5": np.array([0.68])}
    out = renormalize_mutually_exclusive_groups(probs)
    assert out["over_2_5"] + out["under_2_5"] == pytest.approx(1.0)
    # La direccion relativa se conserva: over seguia siendo mas probable.
    assert out["over_2_5"] > out["under_2_5"]


def test_renormalize_match_result_group_sums_to_one():
    probs = {
        "home_win": np.array([0.5, 0.1]),
        "draw": np.array([0.3, 0.1]),
        "away_win": np.array([0.4, 0.1]),  # suma 1.2 y 0.3 respectivamente
    }
    out = renormalize_mutually_exclusive_groups(probs)
    total = out["home_win"] + out["draw"] + out["away_win"]
    np.testing.assert_allclose(total, 1.0, atol=1e-9)


def test_renormalize_leaves_ungrouped_markets_untouched():
    probs = {"btts": np.array([0.55]), "home_win": np.array([0.4]), "draw": np.array([0.3]), "away_win": np.array([0.3])}
    out = renormalize_mutually_exclusive_groups(probs)
    assert out["btts"][0] == pytest.approx(0.55)


def test_renormalize_ignores_incomplete_groups():
    # Si solo esta presente un miembro del grupo (p.ej. llamando sobre un
    # subconjunto de mercados), no se toca -- no se puede normalizar un
    # grupo incompleto sin inventar los mercados que faltan.
    probs = {"over_2_5": np.array([0.9])}
    out = renormalize_mutually_exclusive_groups(probs)
    assert out["over_2_5"][0] == pytest.approx(0.9)


class _ConstantGoalsModel:
    """Modelo estadistico de mentira: siempre la misma matriz de marcador,
    para poder controlar exactamente `p_stat` en el test. No hereda de
    `GoalsModel` (ABC) a proposito -- `get_score_matrix` solo necesita
    `predict_score_matrix` via duck typing (`hasattr`), igual que
    Dixon-Coles en produccion."""

    def __init__(self, score_matrix: np.ndarray) -> None:
        self._matrix = score_matrix

    def predict_score_matrix(self, table: pd.DataFrame) -> np.ndarray:
        return np.repeat(self._matrix[np.newaxis, :, :], len(table), axis=0)


class _DisagreeingMLModel:
    """Clasificador ML de mentira que fuerza el escenario exacto del brief:
    devuelve probabilidades por mercado que, sumadas, NO dan 1 para los
    mercados mutuamente excluyentes -- simulando clasificadores binarios
    independientes entrenados por separado (la causa raiz real, ver
    market_labels.py)."""

    def __init__(self, overrides: dict[str, float]) -> None:
        self._overrides = overrides
        self.pipelines_: dict[str, object] = {}  # duck-typed: predictor.py solo mira si el market_key esta aqui

    def predict_market_probabilities(self, table: pd.DataFrame) -> dict[str, np.ndarray]:
        n = len(table)
        return {key: np.full(n, value) for key, value in self._overrides.items()}


def _toy_score_matrix() -> np.ndarray:
    k = 4
    rng = np.random.default_rng(7)
    raw = rng.random((k + 1, k + 1))
    return raw / raw.sum()


def test_predict_markets_for_table_is_internally_consistent_despite_disagreeing_ml_model():
    """Reproduce el bug de raiz end-to-end: un `ml_model` que predice
    over_2_5=0.80 y under_2_5=0.70 (ejemplo literal del brief, seccion 18)
    independientemente del score_matrix estadistico. Tras el ensemble +
    renormalizacion, el resultado SERVIDO debe sumar 1 en cada grupo mutuamente
    excluyente para cada partido."""
    table = pd.DataFrame(
        {
            "match_id": [1],
            "home_goals_for_n_prior": [10],
            "away_goals_for_n_prior": [10],
        }
    )
    statistical_model = _ConstantGoalsModel(_toy_score_matrix())
    ml_model = _DisagreeingMLModel(
        {
            "over_2_5": 0.80,
            "under_2_5": 0.70,  # suma 1.50 -- justo el ejemplo del brief
            "home_win": 0.55,
            "draw": 0.30,
            "away_win": 0.40,  # suma 1.25
        }
    )
    ensemble_weights = {k: 0.5 for k in ml_model._overrides}  # 50/50 estadistico/ML

    outputs = predict_markets_for_table(
        table, statistical_model=statistical_model, ml_model=ml_model, ensemble_weights=ensemble_weights
    )
    by_market = {o.market: o.model_probability for o in outputs}

    assert by_market["over_2_5"] + by_market["under_2_5"] == pytest.approx(1.0, abs=1e-9)
    assert by_market["home_win"] + by_market["draw"] + by_market["away_win"] == pytest.approx(1.0, abs=1e-9)

    # No deben coexistir como "senhales fuertes" simultaneas del mismo lado:
    # con el fix, si over sube, under necesariamente baja de forma
    # proporcional (nunca ambas por encima del 50% para un grupo de 2).
    assert not (by_market["over_2_5"] > 0.5 and by_market["under_2_5"] > 0.5)


def test_all_defined_mutually_exclusive_groups_are_subsets_of_real_markets():
    for group in MUTUALLY_EXCLUSIVE_GROUPS:
        assert set(group) <= set(MARKET_DEFINITIONS)
