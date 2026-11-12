from __future__ import annotations

import numpy as np
import pytest

from backend.app.features.goals import build_match_feature_table
from backend.app.models.calibration.metrics import log_loss_score
from backend.app.models.goals.baseline import LeagueAverageBaseline
from backend.app.models.goals.gradient_boosting import GradientBoostingMarketModel
from backend.app.models.goals.model_selection import SelectedMarketClassifier
from backend.app.prediction.market_labels import MARKET_DEFINITIONS, label_for_market
from backend.app.prediction.predictor import predict_markets_for_table
from backend.tests.fixtures.synthetic import generate_synthetic_matches


@pytest.fixture(scope="module")
def synthetic_table():
    matches = generate_synthetic_matches(n_teams=10, n_seasons=4, seed=23)
    return build_match_feature_table(matches)


@pytest.fixture(scope="module")
def fitted_gbm(synthetic_table):
    train = synthetic_table[
        synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]
    ]
    return GradientBoostingMarketModel().fit(train)


def test_covers_every_market_definition(synthetic_table, fitted_gbm):
    probs = fitted_gbm.predict_market_probabilities(synthetic_table.head(5))
    for market_key in MARKET_DEFINITIONS:
        assert market_key in probs, market_key


def test_probabilities_are_in_unit_interval(synthetic_table, fitted_gbm):
    probs = fitted_gbm.predict_market_probabilities(synthetic_table.head(30))
    for market_key, values in probs.items():
        assert (values >= 0).all() and (values <= 1).all(), market_key


def test_beats_random_guess_on_log_loss(synthetic_table, fitted_gbm):
    eval_table = synthetic_table[
        synthetic_table["season_label"] == synthetic_table["season_label"].iloc[-1]
    ]
    y_true = label_for_market(eval_table, "over_2_5").to_numpy()
    p = fitted_gbm.predict_market_probabilities(eval_table)["over_2_5"]
    random_guess_loss = log_loss_score(y_true, np.full(len(y_true), 0.5))
    assert log_loss_score(y_true, p) <= random_guess_loss


def test_pipelines_are_raw_estimators_without_named_steps(fitted_gbm):
    """Contrato que predictor.py usa para elegir SHAP en vez del camino
    lineal (ver explain_logistic_pipeline / explain_lightgbm_estimator)."""
    estimator = fitted_gbm.pipelines_["over_2_5"]
    assert not hasattr(estimator, "named_steps")
    assert hasattr(estimator, "predict_proba")


def test_predictor_explains_a_gradient_boosting_backed_market_via_shap(synthetic_table, fitted_gbm):
    """End-to-end sobre predict_markets_for_table (la funcion real que sirve
    produccion): cuando el modelo ML seleccionado para un mercado es
    LightGBM "pelado" (sin sklearn.Pipeline), la explicacion debe venir de
    SHAP sin que el pipeline lineal reviente por falta de `named_steps`."""
    train = synthetic_table[
        synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]
    ]
    baseline = LeagueAverageBaseline().fit(train)

    selected = SelectedMarketClassifier(fitted_gbm.feature_cols_)
    selected.register(
        "over_2_5", fitted_gbm, "gradient_boosting", fitted_gbm.pipelines_["over_2_5"]
    )

    outputs = predict_markets_for_table(
        synthetic_table.head(5), baseline, ml_model=selected, ensemble_weights={"over_2_5": 0.5}
    )
    over_outputs = [o for o in outputs if o.market == "over_2_5"]
    assert len(over_outputs) == 5
    for output in over_outputs:
        assert 0.0 <= output.model_probability <= 1.0
        assert output.explanation, "SHAP deberia devolver al menos un factor"
        for factor in output.explanation:
            assert factor["feature"] in fitted_gbm.feature_cols_
            assert factor["direction"] in ("increases_probability", "decreases_probability")
            assert isinstance(factor["contribution"], float)
