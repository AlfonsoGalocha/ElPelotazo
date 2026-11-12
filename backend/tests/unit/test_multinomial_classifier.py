from __future__ import annotations

import numpy as np
import pytest

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import ModelVersion
from backend.app.features.goals import build_match_feature_table
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.models.calibration.metrics import log_loss_score
from backend.app.models.goals.ml_classifier import MarketClassifierModel
from backend.app.models.goals.model_selection import SelectedMarketClassifier, pick_best_candidate
from backend.app.models.goals.multinomial_classifier import MultinomialGroupClassifier
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.prediction.market_labels import label_for_market
from backend.app.services.data_service import ingest_matches
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import load_model_artifact, train_competition_models
from backend.tests.fixtures.synthetic import generate_synthetic_matches


@pytest.fixture(scope="module")
def synthetic_table():
    matches = generate_synthetic_matches(n_teams=10, n_seasons=4, seed=11)
    return build_match_feature_table(matches)


@pytest.fixture(scope="module")
def fitted_multinomial(synthetic_table):
    train = synthetic_table[
        synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]
    ]
    return MultinomialGroupClassifier().fit(train)


def test_match_result_group_sums_to_one_by_construction(synthetic_table, fitted_multinomial):
    """La ventaja real del clasificador conjunto sobre tres binarios
    independientes: aqui la suma=1 no depende de ninguna renormalizacion
    posterior, es una propiedad de predict_proba de un unico modelo."""
    probs = fitted_multinomial.predict_market_probabilities(synthetic_table.head(30))
    total = probs["home_win"] + probs["draw"] + probs["away_win"]
    np.testing.assert_allclose(total, 1.0, atol=1e-9)


def test_over_under_2_5_group_sums_to_one_exactly(synthetic_table, fitted_multinomial):
    probs = fitted_multinomial.predict_market_probabilities(synthetic_table.head(30))
    total = probs["over_2_5"] + probs["under_2_5"]
    np.testing.assert_allclose(total, 1.0, atol=1e-12)


def test_ungrouped_markets_are_not_covered(synthetic_table, fitted_multinomial):
    """btts/over_1_5/over_3_5/team_over_* no pertenecen a ningun grupo
    mutuamente excluyente: no hay nada que este modelo aporte sobre el
    clasificador binario independiente para ellos, asi que no se entrenan
    aqui (MarketClassifierModel sigue siendo el unico candidato ML)."""
    probs = fitted_multinomial.predict_market_probabilities(synthetic_table.head(5))
    assert "btts" not in probs
    assert "over_1_5" not in probs


def test_explanation_hooks_point_to_the_right_class(fitted_multinomial):
    assert fitted_multinomial.class_label_for_explanation("home_win") == "home_win"
    assert fitted_multinomial.class_label_for_explanation("draw") == "draw"
    assert fitted_multinomial.invert_for_market("under_2_5") is True
    assert fitted_multinomial.invert_for_market("over_2_5") is False


def test_pick_best_candidate_chooses_lower_log_loss():
    y_true = np.array([1, 1, 0, 0, 1, 0, 1, 0])
    good = np.array([0.9, 0.8, 0.1, 0.2, 0.9, 0.1, 0.9, 0.1])
    bad = np.full(8, 0.5)
    winner = pick_best_candidate(
        y_true, [("good_model", object(), good), ("bad_model", object(), bad)]
    )
    assert winner[0] == "good_model"


def test_pick_best_candidate_returns_none_when_no_candidate_available():
    y_true = np.array([1, 0, 1])
    assert pick_best_candidate(y_true, [("only_candidate", object(), None)]) is None


def test_selected_classifier_delegates_to_the_winning_source_per_market(synthetic_table):
    train = synthetic_table[
        synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]
    ]
    independent = MarketClassifierModel().fit(train)
    joint = MultinomialGroupClassifier().fit(train)

    eval_table = synthetic_table[
        synthetic_table["season_label"] == synthetic_table["season_label"].iloc[-1]
    ]
    y_true = label_for_market(eval_table, "over_2_5").to_numpy()
    p_independent = independent.predict_market_probabilities(eval_table)["over_2_5"]
    p_joint = joint.predict_market_probabilities(eval_table)["over_2_5"]

    selected = SelectedMarketClassifier(independent.feature_cols_)
    winner = pick_best_candidate(
        y_true,
        [
            ("logistic_independent", independent, p_independent),
            ("multinomial_joint", joint, p_joint),
        ],
    )
    winner_name, winner_source, _ = winner
    selected.register("over_2_5", winner_source, winner_name, winner_source.pipelines_["over_2_5"])

    delegated = selected.predict_market_probabilities(eval_table)["over_2_5"]
    expected = p_independent if winner_name == "logistic_independent" else p_joint
    np.testing.assert_allclose(delegated, expected)
    assert selected.winner_by_market["over_2_5"] == winner_name
    assert (
        log_loss_score(y_true, delegated)
        <= min(log_loss_score(y_true, p_independent), log_loss_score(y_true, p_joint)) + 1e-9
    )


class _FakeProvider(DataProvider):
    name = "multinomial_selection_test_provider"

    def __init__(self, synthetic):
        self._synthetic = synthetic

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        rows = self._synthetic[self._synthetic["season_label"] == season_label]
        return [
            RawMatchRecord(
                provider=self.name,
                provider_id=f"multinomial-select-{r.match_id}",
                competition_code=competition_code,
                season_label=season_label,
                date=r.date.date().isoformat(),
                home_team_raw=f"MultiTeam{int(r.home_team_id)}",
                away_team_raw=f"MultiTeam{int(r.away_team_id)}",
                home_goals=int(r.home_goals),
                away_goals=int(r.away_goals),
                home_shots=int(r.home_shots),
                away_shots=int(r.away_shots),
                odds=[RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.9)],
            )
            for _, r in rows.iterrows()
        ]


def test_train_competition_models_picks_one_coherent_winner_per_group():
    """End-to-end: entrenar de verdad (via el servicio real, no un mock)
    debe elegir UN SOLO candidato ML por GRUPO mutuamente excluyente (nunca
    uno distinto para "home_win" y otro para "draw" dentro del mismo grupo
    -- eso reintroduciria la propia inconsistencia que el clasificador
    conjunto existe para evitar)."""
    competition_code = "multinomial-select-test-league"
    COMPETITIONS.setdefault(competition_code, ("Multinomial Select Test League", "Testland"))
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=4, seed=55)
    provider = _FakeProvider(synthetic)

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"
        market_metrics = result["metrics"]["markets"]

        for group in (("home_win", "draw", "away_win"), ("over_2_5", "under_2_5")):
            winners = {market_metrics[m]["ml_winner"] for m in group}
            assert len(winners) == 1, f"ganadores distintos dentro del mismo grupo: {winners}"
            assert winners.pop() in (
                "logistic_independent",
                "multinomial_joint",
                "gradient_boosting",
            )

        model_version = db.query(ModelVersion).filter_by(id=result["model_version_id"]).one()
        artifact = load_model_artifact(model_version)
        selected = artifact["ml_classifier"]

    # Si el ganador del grupo de 3 fue "multinomial_joint", las tres
    # probabilidades crudas del modelo ML vienen del MISMO pipeline
    # multinomial y suman 1 por construccion, sin depender de la
    # renormalizacion que se aplica mas adelante en predictor.py.
    if market_metrics["home_win"]["ml_winner"] == "multinomial_joint":
        with session_scope() as db:
            comp = db.query(Competition).filter_by(code=competition_code).one()
            matches = load_matches_dataframe(db, comp.id)
            table = build_match_feature_table(matches).dropna(subset=["home_goals", "away_goals"])
        probs = selected.predict_market_probabilities(table.head(10))
        np.testing.assert_allclose(
            probs["home_win"] + probs["draw"] + probs["away_win"], 1.0, atol=1e-9
        )
