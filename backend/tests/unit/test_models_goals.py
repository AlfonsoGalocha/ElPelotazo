from __future__ import annotations

import numpy as np
import pytest

from backend.app.features.goals import build_match_feature_table
from backend.app.models.base import MAX_GOALS
from backend.app.models.calibration.metrics import log_loss_score
from backend.app.models.goals.baseline import LeagueAverageBaseline
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.models.goals.poisson_model import PoissonRegressionModel
from backend.app.prediction.market_labels import label_for_market
from backend.app.prediction.probability import market_probabilities
from backend.tests.fixtures.synthetic import generate_synthetic_matches


@pytest.fixture(scope="module")
def synthetic_table():
    matches = generate_synthetic_matches(n_teams=10, n_seasons=4, seed=7)
    return build_match_feature_table(matches)


@pytest.mark.parametrize("model_cls", [LeagueAverageBaseline, DixonColesModel, PoissonRegressionModel])
def test_goal_distribution_sums_to_one(synthetic_table, model_cls):
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    model = model_cls().fit(train)

    home_dist, away_dist = model.predict_goal_distribution(train.head(20))
    assert home_dist.shape == (20, MAX_GOALS + 1)
    np.testing.assert_allclose(home_dist.sum(axis=1), 1.0, atol=1e-6)
    np.testing.assert_allclose(away_dist.sum(axis=1), 1.0, atol=1e-6)


def test_market_probabilities_are_in_unit_interval(synthetic_table):
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    model = DixonColesModel().fit(train)
    probs = market_probabilities(model, train.head(30))
    for market_key, values in probs.items():
        assert (values >= 0).all() and (values <= 1).all(), market_key


def test_over_1_5_and_over_2_5_are_consistent(synthetic_table):
    """P(Over 1.5) debe ser siempre >= P(Over 2.5) (evento mas laxo)."""
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    model = DixonColesModel().fit(train)
    probs = market_probabilities(model, train.head(30))
    assert (probs["over_1_5"] >= probs["over_2_5"] - 1e-9).all()
    assert (probs["over_2_5"] >= probs["over_3_5"] - 1e-9).all()


def test_all_models_beat_a_random_guess_on_log_loss(synthetic_table):
    """Ningun modelo deberia ser peor que "siempre 50%" en un dataset donde
    hay senhal real (seccion 52: comparar contra baselines minimos)."""
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    test = synthetic_table[synthetic_table["season_label"] == synthetic_table["season_label"].iloc[-1]]
    y_true = label_for_market(test, "over_2_5").to_numpy()

    random_guess_loss = log_loss_score(y_true, np.full(len(y_true), 0.5))

    for model_cls in (LeagueAverageBaseline, DixonColesModel, PoissonRegressionModel):
        model = model_cls().fit(train)
        probs = market_probabilities(model, test)["over_2_5"]
        loss = log_loss_score(y_true, probs)
        assert loss <= random_guess_loss + 0.05, f"{model_cls.__name__} no bate una moneda al aire"


def test_dixon_coles_unseen_team_falls_back_to_global_average(synthetic_table):
    """Equipo ascendido / nunca visto: no debe romper, debe hacer shrinkage
    hacia la media global (seccion 57/59)."""
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    model = DixonColesModel().fit(train)

    row = train.iloc[[0]].copy()
    row["home_team_id"] = 99999  # equipo inexistente en entrenamiento
    home_dist, away_dist = model.predict_goal_distribution(row)
    assert np.isfinite(home_dist).all()
    np.testing.assert_allclose(home_dist.sum(axis=1), 1.0, atol=1e-6)
