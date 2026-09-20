from __future__ import annotations

import numpy as np

from backend.app.backtesting.metrics import (
    market_strategy_metrics,
    performance_by_edge_bucket,
)


def test_performance_by_edge_bucket_groups_by_edge_range():
    """Bucket 0-5pp: edge pequenho, cerca del 50% de acierto esperado por
    construccion. Bucket 20+: edge enorme, deberia acertar casi siempre en
    este dataset sintetico -- construido para que el resultado sea
    predecible y verificable, no una caja negra."""
    rng = np.random.default_rng(42)
    n = 2000
    market_odds = rng.uniform(1.5, 4.0, size=n)
    implied = 1.0 / market_odds
    # Genera model_probability = implied + un edge controlado, y luego el
    # resultado REAL segun esa probabilidad exacta (para que el hit rate
    # de cada bucket sea comprobable, no ruido puro).
    true_edge_pp = rng.choice([2.0, 7.0, 12.0, 17.0, 25.0], size=n)
    model_probability = np.clip(implied + true_edge_pp / 100.0, 0.01, 0.99)
    y_true = rng.uniform(0, 1, size=n) < model_probability

    buckets = performance_by_edge_bucket(y_true, model_probability, market_odds)
    ranges = {b["edge_range_pp"] for b in buckets}
    assert ranges == {"0-5", "5-10", "10-15", "15-20", "20+"}
    assert sum(b["n"] for b in buckets) == n

    # Bucket de mayor edge deberia mostrar mayor hit rate que el de menor
    # edge EN ESTE dataset sintetico (construido asi a proposito) -- pero
    # la funcion en si no asume nada, solo agrega y reporta lo que hay.
    by_range = {b["edge_range_pp"]: b for b in buckets}
    assert by_range["20+"]["hit_rate"] > by_range["0-5"]["hit_rate"]


def test_performance_by_edge_bucket_empty_without_valid_odds():
    y_true = np.array([True, False, True])
    y_prob = np.array([0.6, 0.4, 0.7])
    market_odds = np.array([np.nan, np.nan, np.nan])
    assert performance_by_edge_bucket(y_true, y_prob, market_odds) == []


def test_performance_by_edge_bucket_reports_brier_and_roi_per_bucket():
    y_true = np.array([True, True, False, False])
    y_prob = np.array([0.60, 0.60, 0.60, 0.60])
    market_odds = np.array([2.0, 2.0, 2.0, 2.0])  # implied 0.5 -> edge = 10pp para las 4
    buckets = performance_by_edge_bucket(y_true, y_prob, market_odds)
    assert len(buckets) == 1
    bucket = buckets[0]
    assert bucket["edge_range_pp"] == "5-10" or bucket["edge_range_pp"] == "10-15"
    assert bucket["n"] == 4
    assert "brier_score" in bucket and "roi" in bucket and "hit_rate" in bucket
    assert bucket["hit_rate"] == 0.5


def test_market_strategy_metrics_still_works_alongside_edge_buckets():
    """Regresion: anhadir performance_by_edge_bucket no debe cambiar el
    comportamiento de market_strategy_metrics, que ya existia."""
    y_true = np.array([True, False, True, False])
    y_prob = np.array([0.7, 0.3, 0.6, 0.5])
    market_odds = np.array([2.0, 2.0, 2.0, 2.0])
    result = market_strategy_metrics(y_true, y_prob, market_odds)
    assert result is not None
    assert "roi" in result
