"""Diagnostico puntual (seccion 15.8 de docs/architecture_audit.md): el
usuario ejecuto `compare-ranking-strategies-walk-forward` sobre su BD real
(n=1133) y `v2`/`v2_edge_cap`/`v2_edge_cap_longshot` salieron BYTE-A-BYTE
identicos en TODOS los top-N -- un resultado mucho mas fuerte que "el techo
rara vez actua", que es lo que mostraban mis dos reproducciones sinteticas
(una con EV optimista, otra con EV Wilson-conservador como el real) al
construir `compare_ranking_strategies_walk_forward` -- ver el comentario de
esa funcion para la metodologia.

Este script NUNCA modifica nada (solo lee la BD) y reutiliza EXACTAMENTE la
misma logica de produccion (`fit_edge_reliability_curve_from_observations`,
`fit_longshot_calibration_curve_from_observations`,
`reality_capped_expected_value`, `signal_score_v2`) -- nunca reimplementa el
calculo, para que la instrumentacion no pueda divergir de lo que
`compare_ranking_strategies_walk_forward` hace realmente.

Lo que cuenta, fila a fila, durante el MISMO recorrido walk-forward:
  1. cuantas filas evaluadas tienen `risk_adjusted_expected_value` crudo
     (Wilson-conservador, ver prediction/uncertainty.py) ya <= 0 -- ahi
     ningun techo puede cambiar nada (`max(capped, 0)` ya daba 0 sin techo).
  2. de las que tienen EV crudo > 0, cuantas tienen el techo de edge
     realmente activo (`capped_ev < raw_ev`, estrictamente) en el momento en
     que se puntuan.
  3. lo mismo para el techo de la cola (longshot).
  4. especificamente dentro del TOP-100 de v2 (recalculado sobre las
     puntuaciones ya obtenidas): cuantas de esas predicciones tendrian un
     score distinto bajo v2_edge_cap/v2_edge_cap_longshot.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_walk_forward_cap_binding.py
    docker compose exec backend python scripts/debug_walk_forward_cap_binding.py \
        --competition primera_division
"""

from __future__ import annotations

import argparse

import numpy as np

from backend.app.backtesting.ranking_comparison import (
    WALK_FORWARD_REFIT_EVERY,
    load_settled_predictions_with_kickoff,
)
from backend.app.db.database import session_scope
from backend.app.prediction.ranking import (
    fit_edge_reliability_curve_from_observations,
    fit_longshot_calibration_curve_from_observations,
    reality_capped_expected_value,
    signal_score_v2,
)


def run_instrumented_walk_forward(settled_with_kickoff, min_train_n: int = 30) -> None:
    with_market = [(p, r, k) for p, r, k in settled_with_kickoff if p.market_odds is not None]
    with_market.sort(key=lambda row: row[2])

    print(f"n_con_mercado={len(with_market)}  min_train_n={min_train_n}")
    if len(with_market) <= min_train_n:
        print("  (nada que evaluar -- igual que compare_ranking_strategies_walk_forward)")
        return

    prior_edge_pp: list[float] = []
    prior_edge_outcome: list[float] = []
    prior_longshot_probability: list[float] = []
    prior_longshot_outcome: list[float] = []
    edge_curve = None
    longshot_curve = None

    n_evaluated = 0
    n_raw_ev_nonpositive = 0
    n_raw_ev_positive = 0
    n_raw_ev_is_null = 0
    n_raw_ev_is_zero_not_null = 0
    n_raw_ev_negative = 0
    n_uncertainty_low_is_null = 0
    sample_rows: list[tuple[int, object, object, object, object]] = []
    n_edge_curve_available = 0
    n_edge_cap_binding = 0  # EV crudo > 0 Y el techo de edge lo reduce
    n_longshot_curve_available = 0
    # EV crudo > 0 Y el techo de la cola lo reduce (sobre el ya capado por edge)
    n_longshot_cap_binding = 0

    v2_scores: dict[int, float] = {}
    v2_edge_cap_scores: dict[int, float] = {}
    v2_edge_cap_longshot_scores: dict[int, float] = {}

    for i, (prediction, result, _kickoff) in enumerate(with_market):
        if i >= min_train_n:
            if edge_curve is None or i % WALK_FORWARD_REFIT_EVERY == 0:
                edge_curve = (
                    fit_edge_reliability_curve_from_observations(
                        np.array(prior_edge_pp), np.array(prior_edge_outcome)
                    )
                    if len(prior_edge_pp) >= min_train_n
                    else None
                )
                longshot_curve = (
                    fit_longshot_calibration_curve_from_observations(
                        np.array(prior_longshot_probability), np.array(prior_longshot_outcome)
                    )
                    if len(prior_longshot_probability) >= min_train_n
                    else None
                )

            n_evaluated += 1
            if prediction.risk_adjusted_expected_value is None:
                n_raw_ev_is_null += 1
            if prediction.uncertainty_low is None:
                n_uncertainty_low_is_null += 1
            if len(sample_rows) < 10:
                sample_rows.append(
                    (
                        prediction.id,
                        prediction.risk_adjusted_expected_value,
                        prediction.uncertainty_low,
                        prediction.model_probability,
                        prediction.market_odds,
                    )
                )

            raw_ev = prediction.risk_adjusted_expected_value or 0.0
            if raw_ev <= 0.0:
                n_raw_ev_nonpositive += 1
                if prediction.risk_adjusted_expected_value is not None:
                    if prediction.risk_adjusted_expected_value == 0.0:
                        n_raw_ev_is_zero_not_null += 1
                    else:
                        n_raw_ev_negative += 1
            else:
                n_raw_ev_positive += 1

                if edge_curve is not None:
                    n_edge_curve_available += 1
                    edge_only_capped = reality_capped_expected_value(
                        raw_ev,
                        prediction.edge,
                        prediction.market_odds,
                        edge_curve,
                    )
                    if edge_only_capped < raw_ev:
                        n_edge_cap_binding += 1

                if longshot_curve is not None and prediction.market_odds is not None:
                    n_longshot_curve_available += 1
                    edge_then_longshot_capped = reality_capped_expected_value(
                        raw_ev,
                        prediction.edge,
                        prediction.market_odds,
                        edge_curve,
                        None,
                        prediction.model_probability,
                        longshot_curve,
                    )
                    edge_only_capped_for_compare = reality_capped_expected_value(
                        raw_ev,
                        prediction.edge,
                        prediction.market_odds,
                        edge_curve,
                    )
                    if edge_then_longshot_capped < edge_only_capped_for_compare:
                        n_longshot_cap_binding += 1

            v2_scores[prediction.id] = signal_score_v2(prediction)
            v2_edge_cap_scores[prediction.id] = signal_score_v2(
                prediction, edge_reliability_curve=edge_curve
            )
            v2_edge_cap_longshot_scores[prediction.id] = signal_score_v2(
                prediction,
                edge_reliability_curve=edge_curve,
                longshot_calibration_curve=longshot_curve,
            )

        if prediction.edge is not None:
            prior_edge_pp.append(prediction.edge * 100.0)
            prior_edge_outcome.append(float(result.outcome))
        if prediction.market_odds is not None and prediction.market_odds >= 4.0:
            prior_longshot_probability.append(prediction.model_probability)
            prior_longshot_outcome.append(float(result.outcome))

    print(f"evaluadas={n_evaluated}")
    print(f"  raw_risk_adjusted_ev <= 0 (ningun techo puede cambiar nada): {n_raw_ev_nonpositive}")
    print(f"    de las cuales columna NULL (-> tratada como 0.0 por 'or 0.0'): {n_raw_ev_is_null}")
    print(f"    de las cuales columna == 0.0 explicito (no NULL): {n_raw_ev_is_zero_not_null}")
    print(f"    de las cuales columna negativa (no NULL): {n_raw_ev_negative}")
    print(f"  uncertainty_low columna NULL: {n_uncertainty_low_is_null}")
    print(f"  raw_risk_adjusted_ev > 0 (unicas donde un techo PODRIA actuar): {n_raw_ev_positive}")
    print(
        "  muestra de las primeras 10 filas evaluadas (id, risk_adjusted_ev, "
        "uncertainty_low, model_probability, market_odds):"
    )
    for row in sample_rows:
        print(f"    {row}")
    print(
        f"    con curva de edge disponible en ese momento: {n_edge_curve_available}  "
        f"-- techo de edge REALMENTE activo (capped < crudo): {n_edge_cap_binding}"
    )
    print(
        f"    con curva de cola disponible en ese momento: {n_longshot_curve_available}  "
        f"-- techo de cola REALMENTE activo (sobre lo ya capado por edge): {n_longshot_cap_binding}"
    )

    top_n = 100
    ranked_v2 = sorted(v2_scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    n_top_changed_by_edge_cap = sum(
        1 for pid, score in ranked_v2 if v2_edge_cap_scores[pid] != score
    )
    n_top_changed_by_longshot_cap = sum(
        1
        for pid, score in ranked_v2
        if v2_edge_cap_longshot_scores[pid] != v2_edge_cap_scores[pid]
    )
    print(f"\ndentro del TOP-{top_n} de v2 (n={len(ranked_v2)}):")
    print(f"  con score distinto bajo v2_edge_cap: {n_top_changed_by_edge_cap}")
    print(
        f"  con score distinto bajo v2_edge_cap_longshot (vs v2_edge_cap): "
        f"{n_top_changed_by_longshot_cap}"
    )


def main(competition: str | None = None, market: str | None = None, min_train_n: int = 30) -> None:
    with session_scope() as db:
        settled = load_settled_predictions_with_kickoff(
            db, competition_code=competition, market=market
        )
        run_instrumented_walk_forward(settled, min_train_n=min_train_n)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", default=None)
    parser.add_argument("--market", default=None)
    parser.add_argument("--min-train-n", type=int, default=30)
    args = parser.parse_args()
    main(competition=args.competition, market=args.market, min_train_n=args.min_train_n)
