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

# En puntos porcentuales (edge_pp = (model_probability - market_implied_probability) * 100).
# El ultimo bucket es abierto (20+) para no descartar los edges extremos.
EDGE_BUCKETS_PP = [(0.0, 5.0), (5.0, 10.0), (10.0, 15.0), (15.0, 20.0), (20.0, float("inf"))]


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


def performance_by_edge_bucket(y_true: np.ndarray, y_prob: np.ndarray, market_odds: np.ndarray) -> list[dict]:
    """Comportamiento historico REAL por rango de edge (seccion 18 del
    pedido de revision integral: "NO asumas que mayor edge = mejor").

    edge_pp = (model_probability - cuota_implicita_CRUDA_del_mercado) * 100
    (misma cuota que ve `market_strategy_metrics` para decidir si "se
    apuesta"; no quita el vig aqui a proposito, para que el bucket de edge
    coincida exactamente con el criterio que usa la simulacion de ROI).

    Por cada bucket se calcula hit rate, Brier score y ROI hipotetico
    reales sobre datos historicos -- si un bucket de edge alto no muestra
    mejor ROI/hit-rate que uno de edge bajo, eso es evidencia real de que
    ese edge no era fiable (cuota mal identificada, modelo mal calibrado
    para esa zona, muestra pequenha...), no una suposicion.
    """
    valid = ~np.isnan(market_odds)
    if valid.sum() == 0:
        return []

    y_true_v, y_prob_v, odds_v = y_true[valid], y_prob[valid], market_odds[valid]
    implied = 1.0 / odds_v
    edge_pp = (y_prob_v - implied) * 100.0

    buckets = []
    for low, high in EDGE_BUCKETS_PP:
        mask = (edge_pp >= low) & (edge_pp < high)
        n = int(mask.sum())
        if n == 0:
            continue
        y_t, y_p, o = y_true_v[mask], y_prob_v[mask], odds_v[mask]
        pnl = np.where(y_t, o - 1.0, -1.0)
        buckets.append(
            {
                "edge_range_pp": f"{low:.0f}-{high:.0f}" if high != float("inf") else f"{low:.0f}+",
                "n": n,
                "average_edge_pp": float(np.mean(edge_pp[mask])),
                "hit_rate": float(np.mean(y_t)),
                "brier_score": brier_score(y_t, y_p),
                "roi": float(pnl.sum() / n),
            }
        )
    return buckets


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
