"""Metricas de backtesting, separando explicitamente (seccion 27):

- MODEL QUALITY: Brier, Log Loss, calibracion. No depende de que exista
  mercado ni de cuotas.
- MARKET STRATEGY PERFORMANCE: ROI hipotetico, yield, edge medio. Solo se
  calcula cuando hay cuotas de mercado disponibles, y se muestra por
  separado para no confundir "el modelo predice bien" con "esta estrategia
  de apuestas hubiera ganado dinero".
"""

from __future__ import annotations

import numpy as np

from backend.app.models.calibration.metrics import (
    brier_score,
    expected_calibration_error,
    log_loss_score,
    reliability_curve,
)

PROBABILITY_BUCKETS = [(0.0, 0.5), (0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 1.01)]


def model_quality_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict:
    return {
        "n_predictions": int(len(y_true)),
        "brier_score": brier_score(y_true, y_prob),
        "log_loss": log_loss_score(y_true, y_prob),
        "expected_calibration_error": expected_calibration_error(y_true, y_prob),
        "reliability_curve": reliability_curve(y_true, y_prob),
        "accuracy_secondary_only": float(np.mean((y_prob >= 0.5) == y_true)),
    }


def market_strategy_metrics(
    y_true: np.ndarray, y_prob: np.ndarray, market_odds: np.ndarray, stake: float = 1.0
) -> dict | None:
    """Simula apostar `stake` a la seleccion predicha cuando hay edge positivo
    frente a la cuota de mercado disponible. Solo informativo/hipotetico
    (seccion 54): resultados pasados no garantizan resultados futuros.
    """
    valid = ~np.isnan(market_odds)
    if valid.sum() == 0:
        return None

    y_true_v, y_prob_v, odds_v = y_true[valid], y_prob[valid], market_odds[valid]
    implied = 1.0 / odds_v
    bet_mask = y_prob_v > implied  # solo se "apuesta" cuando el modelo ve edge positivo

    n_bets = int(bet_mask.sum())
    if n_bets == 0:
        return {"n_opportunities": 0, "n_bets": 0}

    pnl = np.where(y_true_v[bet_mask], (odds_v[bet_mask] - 1) * stake, -stake)
    cumulative = np.cumsum(pnl)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max

    return {
        "n_opportunities": int(valid.sum()),
        "n_bets": n_bets,
        "total_staked": float(n_bets * stake),
        "total_pnl": float(pnl.sum()),
        "roi": float(pnl.sum() / (n_bets * stake)),
        "average_edge": float(np.mean(y_prob_v[bet_mask] - implied[bet_mask])),
        "max_drawdown": float(drawdown.min()),
    }


def performance_by_probability_bucket(y_true: np.ndarray, y_prob: np.ndarray) -> list[dict]:
    buckets = []
    for low, high in PROBABILITY_BUCKETS:
        mask = (y_prob >= low) & (y_prob < high)
        n = int(mask.sum())
        if n == 0:
            continue
        buckets.append(
            {
                "range": f"{low:.0%}-{high:.0%}" if high <= 1 else f"{low:.0%}+",
                "n": n,
                "predicted_probability_mean": float(np.mean(y_prob[mask])),
                "empirical_frequency": float(np.mean(y_true[mask])),
            }
        )
    return buckets
