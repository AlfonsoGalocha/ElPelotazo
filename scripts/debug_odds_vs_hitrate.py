"""Diagnostico puntual: tras corregir el caso de edge enorme (Getafe-Atletico,
seccion 12 de docs/architecture_audit.md), el usuario reporto un patron
RELACIONADO pero distinto: "victoria local" a cuota 5.50/6.00 seguia saliendo
como mejor oportunidad, con `model_probability` bajo (33.5%, 28.3%) -- "una
cuota 5 o una cuota 6 no es una buena prediccion".

Esto es la hipotesis del "favorite-longshot bias", bien documentada en la
literatura de apuestas deportivas: los mercados (y potencialmente un modelo
entrenado contra ellos) tienden a sobrevalorar las selecciones poco probables
("longshots", cuota alta) -- pagan mas de lo que deberian, pero tambien
aciertan menos de lo que su propia cuota/edge sugiere. Es DISTINTO del
problema de "edge grande" ya corregido: aqui la pregunta es si la CUOTA en
si (independientemente del tamanho del edge) predice peor acierto.

Antes de anhadir cualquier correccion nueva, hace falta evidencia real: ¿las
cuotas altas de este sistema realmente aciertan menos de lo que su cuota
implica, incluso cuando el edge nominal parece grande? Si la respuesta es
si, eso justifica una correccion sobre la cuota/probabilidad, igual que
`debug_edge_vs_hitrate.py` justifico la correccion sobre el tamanho del
edge; si no, no se toca nada.

Reutiliza backtesting/metrics.py::performance_by_odds_bucket (nuevo, mismo
patron que performance_by_edge_bucket) sobre TODAS las predicciones YA
LIQUIDADAS con cuota real del sistema.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_odds_vs_hitrate.py
"""

from __future__ import annotations

import numpy as np

from backend.app.backtesting.metrics import performance_by_odds_bucket
from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult


def _print_buckets(y_true: np.ndarray, y_prob: np.ndarray, market_odds: np.ndarray) -> None:
    buckets = performance_by_odds_bucket(y_true, y_prob, market_odds)
    if not buckets:
        print("  (sin datos)")
        return
    for bucket in buckets:
        print(
            f"  cuota {bucket['odds_range']:>8s}  n={bucket['n']:4d}  "
            f"model_prob_media={bucket['average_model_probability']:.1%}  "
            f"hit_rate={bucket['hit_rate']:.1%}  roi={bucket['roi']:+.3f}  "
            f"brier={bucket['brier_score']:.4f}"
        )


def main() -> None:
    # Mismo patron de extraccion DENTRO del `with` que debug_edge_vs_hitrate.py
    # (DetachedInstanceError real, visto la primera vez que se corrio ese script).
    with session_scope() as db:
        rows = (
            db.query(Prediction, PredictionResult)
            .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
            .join(Match, Match.id == Prediction.match_id)
            .filter(Prediction.market_odds.isnot(None))
            .all()
        )
        values = [
            (
                bool(r.outcome),
                p.model_probability,
                p.market_odds,
                p.edge if p.edge is not None else float("nan"),
            )
            for p, r in rows
        ]

    print(f"Total predicciones liquidadas con cuota: {len(values)}\n")
    if not values:
        return

    y_true = np.array([v[0] for v in values])
    y_prob = np.array([v[1] for v in values])
    market_odds = np.array([v[2] for v in values])
    edge = np.array([v[3] for v in values])

    print("=== TODAS (cualquier edge) ===")
    _print_buckets(y_true, y_prob, market_odds)

    print("\n=== SOLO con edge >= 15pp (donde el usuario vio el patron) ===")
    high_edge = edge >= 0.15
    _print_buckets(y_true[high_edge], y_prob[high_edge], market_odds[high_edge])

    print("\n=== SOLO con edge < 15pp ===")
    low_edge = ~high_edge
    _print_buckets(y_true[low_edge], y_prob[low_edge], market_odds[low_edge])


if __name__ == "__main__":
    main()
