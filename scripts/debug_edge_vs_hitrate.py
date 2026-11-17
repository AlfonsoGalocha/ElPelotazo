"""Diagnostico puntual: ¿el edge grande (model_probability - probabilidad
implicita CRUDA del mercado) realmente predice mejor acierto, o es al
reves? Pedido real del usuario ("revision motor 2.0", tras activar
signal_score_v2): el caso Getafe-Atletico (match_id=61) seguia arriba del
ranking porque su `uncertainty_low` (Wilson, basado en tamanho de
muestra) es ESTRECHO -- el modelo tiene mucha muestra historica y por
tanto esta "seguro" de su propia estimacion, aunque esa estimacion
discrepe muchisimo del mercado. Esa incertidumbre mide ruido de muestra,
nunca desacuerdo con una fuente independiente fuerte (el mercado) -- son
dos cosas distintas.

Antes de anhadir una penalizacion por discrepancia con el mercado (y
elegir CUALQUIER umbral/forma para ella), hace falta evidencia real:
¿un edge enorme en este sistema realmente acierta MENOS que uno modesto,
sobre todo cuando el mercado esta bien respaldado (muchas casas)? Si la
respuesta es si, eso justifica penalizar la discrepancia en si; si no,
seria inventar una correccion sin base.

Reutiliza backtesting/metrics.py::performance_by_edge_bucket (ya
existente, nunca una metrica nueva) sobre TODAS las predicciones YA
LIQUIDADAS con cuota real del sistema, agrupadas tambien por calidad de
mercado (numero de casas).

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_edge_vs_hitrate.py
"""

from __future__ import annotations

import numpy as np

from backend.app.backtesting.metrics import performance_by_edge_bucket
from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult


def _print_buckets(y_true: np.ndarray, y_prob: np.ndarray, market_odds: np.ndarray) -> None:
    buckets = performance_by_edge_bucket(y_true, y_prob, market_odds)
    if not buckets:
        print("  (sin datos)")
        return
    for bucket in buckets:
        print(
            f"  edge {bucket['edge_range_pp']:>6s}pp  n={bucket['n']:4d}  "
            f"hit_rate={bucket['hit_rate']:.1%}  roi={bucket['roi']:+.3f}  "
            f"brier={bucket['brier_score']:.4f}"
        )


def main() -> None:
    with session_scope() as db:
        rows = (
            db.query(Prediction, PredictionResult)
            .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
            .join(Match, Match.id == Prediction.match_id)
            .filter(Prediction.market_odds.isnot(None))
            .all()
        )

    print(f"Total predicciones liquidadas con cuota: {len(rows)}\n")
    if not rows:
        return

    y_true = np.array([bool(r.outcome) for _p, r in rows])
    y_prob = np.array([p.model_probability for p, _r in rows])
    market_odds = np.array([p.market_odds for p, _r in rows])
    bookmakers_used = np.array([p.bookmakers_used or 0 for p, _r in rows])

    print("=== TODAS (cualquier numero de casas) ===")
    _print_buckets(y_true, y_prob, market_odds)

    print("\n=== SOLO mercado bien respaldado (>=4 casas) ===")
    well_backed = bookmakers_used >= 4
    _print_buckets(y_true[well_backed], y_prob[well_backed], market_odds[well_backed])

    print("\n=== SOLO mercado con poco respaldo (<4 casas) ===")
    poorly_backed = ~well_backed
    _print_buckets(y_true[poorly_backed], y_prob[poorly_backed], market_odds[poorly_backed])


if __name__ == "__main__":
    main()
