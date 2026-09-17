from __future__ import annotations

from backend.app.backtesting.splits import expanding_window_splits, rolling_window_splits
from backend.app.features.goals import build_match_feature_table
from backend.tests.fixtures.synthetic import generate_synthetic_matches


def _table():
    matches = generate_synthetic_matches(n_teams=8, n_seasons=5, seed=11)
    return build_match_feature_table(matches)


def test_expanding_window_train_set_grows_each_fold():
    table = _table()
    folds = expanding_window_splits(table, min_train_seasons=2)
    assert len(folds) == 3  # 5 temporadas, min_train=2 -> 3 folds de test

    sizes = [len(f.train_index) for f in folds]
    assert sizes == sorted(sizes)  # el train nunca encoge


def test_expanding_window_never_lets_test_leak_into_train():
    table = _table()
    folds = expanding_window_splits(table, min_train_seasons=2)
    for fold in folds:
        assert set(fold.train_index).isdisjoint(set(fold.test_index))
        max_train_date = table.loc[fold.train_index, "date"].max()
        min_test_date = table.loc[fold.test_index, "date"].min()
        assert max_train_date < min_test_date


def test_rolling_window_keeps_train_size_bounded():
    table = _table()
    folds = rolling_window_splits(table, window_size=2)
    train_seasons_counts = [len(f.train_seasons) for f in folds]
    assert all(c == 2 for c in train_seasons_counts)
