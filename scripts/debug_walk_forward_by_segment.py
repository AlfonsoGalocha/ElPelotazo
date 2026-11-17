"""Investigacion pedida por el usuario (seccion 15.10 de
docs/architecture_audit.md) tras ver que, con `risk_adjusted_expected_value`
ya rellenado para el historico real (seccion 15.9), `v1` le gana a TODA la
familia `v2` (incluida `v2_edge_cap`, produccion actual) en absolutamente
todos los top-N del walk-forward global -- un hallazgo serio que exige
comprobar si es real y estable, o un artefacto de un tramo concreto del
historico (una liga con pocos datos todavia, un periodo de un modelo mas
viejo), ANTES de proponer ningun cambio de produccion ("no modifiques
codigo hasta haber identificado la causa" sigue vigente).

Repite `compare_ranking_strategies_walk_forward` (nunca una formula
nueva) sobre varios RECORTES del mismo historico, siempre con la misma
metodologia walk-forward (ninguna curva ve nunca su propio resultado ni
el futuro):

  1. TODO el historico (linea base, igual que
     `compare-ranking-strategies-walk-forward` sin filtros).
  2. Cada competicion por separado (`normalization/competitions.py`).
  3. Cada mercado por separado (los que de verdad aparecen liquidados).
  4. Primera mitad cronologica vs segunda mitad cronologica (partido por
     la fecha mediana de kickoff real, para que ambas mitades tengan
     tamanho de muestra comparable) -- comprueba si la diferencia v1/v2
     es estable en el tiempo o se concentra en un tramo concreto.

Solo imprime v1/v2/v2_edge_cap (la pregunta real: v1 vs produccion
actual) en los cortes top-20/top-50 (bastante muestra para no ser puro
ruido, sin llegar a "all", que por construccion da el mismo ROI en
cualquier estrategia). `v2_edge_cap_longshot` ya quedo descartada en la
seccion 15.9 (empataba con produccion en los cortes mas selectivos, solo
mejoraba algo -- y seguia en negativo -- en top-50/100), no se repite
aqui.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_walk_forward_by_segment.py
"""

from __future__ import annotations

from backend.app.backtesting.ranking_comparison import (
    RankingComparisonReport,
    compare_ranking_strategies_walk_forward,
    load_settled_predictions_with_kickoff,
)
from backend.app.db.database import session_scope
from backend.app.normalization.competitions import COMPETITIONS

SEGMENT_TOP_N = (20, 50)
SEGMENT_STRATEGIES = ("v1", "v2", "v2_edge_cap")


def _print_segment_report(label: str, report: RankingComparisonReport) -> None:
    print(f"\n--- {label} (n_con_mercado={report.n_settled_with_market}) ---")
    if not report.slices:
        print("  (insuficiente para evaluar out-of-sample -- menos de min_train_n liquidadas)")
        return
    by_key = {(s.strategy, s.top_n): s for s in report.slices}
    for strategy in SEGMENT_STRATEGIES:
        for top_n in SEGMENT_TOP_N:
            s = by_key.get((strategy, str(top_n)))
            if s is None or s.n_bets == 0:
                print(
                    f"  {strategy:14s} top-{top_n:>3d}: n=0 "
                    "(sin suficiente muestra en este recorte)"
                )
                continue
            win_rate_str = f"{s.win_rate:.1%}" if s.win_rate is not None else "—"
            roi_str = f"{s.roi:+.3f}" if s.roi is not None else "—"
            print(
                f"  {strategy:14s} top-{top_n:>3d}: n={s.n_bets:4d}  roi={roi_str}  "
                f"win_rate={win_rate_str}  pnl={s.total_pnl:+.2f}"
            )


def main() -> None:
    with session_scope() as db:
        all_rows = load_settled_predictions_with_kickoff(db)
        print(f"Total predicciones liquidadas con cuota real: {len(all_rows)}")

        _print_segment_report(
            "TODO EL HISTORICO", compare_ranking_strategies_walk_forward(all_rows)
        )

        for competition_code in COMPETITIONS:
            rows = load_settled_predictions_with_kickoff(db, competition_code=competition_code)
            _print_segment_report(
                f"COMPETICION={competition_code}",
                compare_ranking_strategies_walk_forward(rows),
            )

        markets_present = sorted({p.market for p, _r, _k in all_rows})
        for market in markets_present:
            rows = load_settled_predictions_with_kickoff(db, market=market)
            _print_segment_report(
                f"MERCADO={market}", compare_ranking_strategies_walk_forward(rows)
            )

        if all_rows:
            kickoffs = sorted(k for _p, _r, k in all_rows)
            median_kickoff = kickoffs[len(kickoffs) // 2]
            older_rows = load_settled_predictions_with_kickoff(db, max_kickoff=median_kickoff)
            recent_rows = load_settled_predictions_with_kickoff(db, min_kickoff=median_kickoff)
            _print_segment_report(
                f"PRIMERA MITAD CRONOLOGICA (antes de {median_kickoff.date()})",
                compare_ranking_strategies_walk_forward(older_rows),
            )
            _print_segment_report(
                f"SEGUNDA MITAD CRONOLOGICA (desde {median_kickoff.date()})",
                compare_ranking_strategies_walk_forward(recent_rows),
            )


if __name__ == "__main__":
    main()
