"""Auditoria completa del ranking pedida por el usuario tras dos correcciones
reales que, verificadas en vivo, no bastaron (ver docs/architecture_audit.md
secciones 12 y 13): reune en un solo sitio toda la evidencia real que las
secciones 3/4/6/10 de esa auditoria piden, reutilizando SIEMPRE herramientas
ya existentes (`performance_by_edge_bucket`, `performance_by_odds_bucket`,
`performance_by_probability_bucket`, `market_quality_tier`,
`fit_edge_reliability_curve`, `fit_odds_reliability_curve`,
`rank_signals_v2`) -- nunca una metrica nueva inventada para esta auditoria.

NUNCA modifica nada: solo lee la BD y escribe resultados por pantalla.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_full_ranking_audit.py
    docker compose exec backend python scripts/debug_full_ranking_audit.py --match-id 61
"""

from __future__ import annotations

import argparse

import numpy as np

from backend.app.backtesting.metrics import (
    PROBABILITY_BUCKETS,
    performance_by_edge_bucket,
    performance_by_odds_bucket,
)
from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.prediction.market_quality import market_quality_tier
from backend.app.prediction.ranking import rank_signals_v2
from backend.app.services.evaluation_service import (
    fit_edge_reliability_curve,
    fit_odds_reliability_curve,
)
from backend.app.services.round_service import get_current_round


def _print_bucket_table(buckets: list[dict], range_key: str) -> None:
    if not buckets:
        print("  (sin datos)")
        return
    for b in buckets:
        print(
            f"  {b[range_key]:>8s}  n={b['n']:4d}  hit_rate={b['hit_rate']:.1%}  "
            f"roi={b['roi']:+.3f}  brier={b['brier_score']:.4f}"
        )


def _section(title: str) -> None:
    print(f"\n{'=' * 10} {title} {'=' * 10}")


def main(target_match_id: int | None = None) -> None:
    # Mismo patron de extraccion DENTRO del `with` que ya costo un
    # DetachedInstanceError real en debug_edge_vs_hitrate.py: TODO lo que se
    # usa fuera del bloque se convierte a tipos nativos aqui dentro.
    with session_scope() as db:
        rows = (
            db.query(Prediction, PredictionResult, Match, Competition)
            .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
            .join(Match, Match.id == Prediction.match_id)
            .join(Competition, Competition.id == Match.competition_id)
            .filter(Prediction.market_odds.isnot(None))
            .all()
        )
        settled = [
            {
                "prediction_id": p.id,
                "match_id": p.match_id,
                "market": p.market,
                "competition": comp.code,
                "model_probability": p.model_probability,
                "market_odds": p.market_odds,
                "edge": p.edge if p.edge is not None else float("nan"),
                "bookmakers_used": p.bookmakers_used or 0,
                "bookmakers_count": p.bookmakers_count,
                "market_odds_min": p.market_odds_min,
                "market_odds_max": p.market_odds_max,
                "market_odds_median": p.market_odds_median,
                "signal_score_stored": p.signal_score,
                "outcome": bool(r.outcome),
            }
            for p, r, m, comp in rows
        ]

        target = None
        if target_match_id is not None:
            target_rows = (
                db.query(Prediction)
                .filter(Prediction.match_id == target_match_id)
                .all()
            )
            target = [
                {
                    "id": p.id,
                    "market": p.market,
                    "model_probability": p.model_probability,
                    "market_probability": p.market_probability,
                    "market_odds": p.market_odds,
                    "edge": p.edge,
                    "expected_value": p.expected_value,
                    "risk_adjusted_expected_value": p.risk_adjusted_expected_value,
                    "uncertainty_low": p.uncertainty_low,
                    "uncertainty_high": p.uncertainty_high,
                    "confidence": p.confidence,
                    "data_quality": p.data_quality,
                    "bookmakers_used": p.bookmakers_used,
                    "signal_score_stored": p.signal_score,
                }
                for p in target_rows
            ]

        edge_curve = fit_edge_reliability_curve(db)
        odds_curve = fit_odds_reliability_curve(db)

        current_round_predictions = []
        for comp in db.query(Competition).all():
            round_info = get_current_round(db, comp.code)
            if round_info is None or not round_info.match_ids:
                continue
            current_round_predictions.extend(
                db.query(Prediction)
                .filter(Prediction.match_id.in_(round_info.match_ids))
                .all()
            )
        # IMPORTANTE (seccion 14 de docs/architecture_audit.md): el techo
        # de cuota (odds_reliability_curve) esta DESCONECTADO de produccion
        # (rollback de emergencia -- colapsaba el ranking entero a score=0).
        # Esta seccion debe reflejar EXACTAMENTE lo que produccion hace hoy,
        # nunca lo que el mecanismo PODRIA hacer si se reactivara -- por
        # eso solo se pasa `edge_curve` aqui, aunque `odds_curve` se siga
        # ajustando arriba para informar si existe.
        included, _ = rank_signals_v2(
            current_round_predictions,
            edge_reliability_curve=edge_curve,
        )
        top10_live = [
            {
                "match_id": s.prediction.match_id,
                "market": s.prediction.market,
                "live_score": s.score,
                "stored_signal_score": s.prediction.signal_score,
                "edge": s.prediction.edge,
                "market_odds": s.prediction.market_odds,
                "model_probability": s.prediction.model_probability,
            }
            for s in included[:10]
        ]

    print(f"Total predicciones liquidadas con cuota real: {len(settled)}")
    print(f"Curva de edge ajustada: {edge_curve is not None}")
    odds_curve_note = "NO usada en el ranking -- ver seccion 14"
    print(f"Curva de cuota ajustada: {odds_curve is not None} ({odds_curve_note})")

    y_true = np.array([row["outcome"] for row in settled])
    y_prob = np.array([row["model_probability"] for row in settled])
    market_odds = np.array([row["market_odds"] for row in settled])
    edge = np.array([row["edge"] for row in settled])
    bookmakers_used = np.array([row["bookmakers_used"] for row in settled])

    _section("1. HIT RATE / ROI POR BUCKET DE EDGE (TODAS)")
    _print_bucket_table(performance_by_edge_bucket(y_true, y_prob, market_odds), "edge_range_pp")

    _section("2. HIT RATE / ROI POR BUCKET DE CUOTA (TODAS)")
    _print_bucket_table(performance_by_odds_bucket(y_true, y_prob, market_odds), "odds_range")

    _section("3. HIT RATE / ROI POR MERCADO (n real por mercado)")
    markets = sorted({row["market"] for row in settled})
    for market in markets:
        mask = [row["market"] == market for row in settled]
        n = sum(mask)
        if n == 0:
            continue
        yt = y_true[mask]
        mo = market_odds[mask]
        pnl = np.where(yt, mo - 1.0, -1.0)
        avg_edge_pp = float(np.nanmean(edge[mask]) * 100)
        print(
            f"  {market:<20s} n={n:4d}  hit_rate={float(np.mean(yt)):.1%}  "
            f"roi={float(pnl.sum() / n):+.3f}  avg_edge_pp={avg_edge_pp:+.1f}"
        )

    _section("4. HIT RATE / ROI POR COMPETICION (n real por competicion)")
    competitions = sorted({row["competition"] for row in settled})
    for comp_code in competitions:
        mask = [row["competition"] == comp_code for row in settled]
        n = sum(mask)
        if n == 0:
            continue
        yt = y_true[mask]
        mo = market_odds[mask]
        pnl = np.where(yt, mo - 1.0, -1.0)
        print(
            f"  {comp_code:<20s} n={n:4d}  hit_rate={float(np.mean(yt)):.1%}  "
            f"roi={float(pnl.sum() / n):+.3f}"
        )

    _section("5. HIT RATE / ROI POR RANGO DE PROBABILIDAD DEL MODELO")
    for low, high in PROBABILITY_BUCKETS:
        mask = (y_prob >= low) & (y_prob < high)
        n = int(mask.sum())
        if n == 0:
            continue
        yt = y_true[mask]
        mo = market_odds[mask]
        pnl = np.where(yt, mo - 1.0, -1.0)
        label = f"{low:.0%}-{high:.0%}" if high <= 1 else f"{low:.0%}+"
        print(
            f"  {label:>8s}  n={n:4d}  hit_rate={float(np.mean(yt)):.1%}  "
            f"roi={float(pnl.sum() / n):+.3f}  model_prob_media={float(np.mean(y_prob[mask])):.1%}"
        )

    _section("6. HIT RATE / ROI SEGUN NUMERO DE BOOKMAKERS (umbral >=4)")
    well_backed = bookmakers_used >= 4
    backing_groups = (
        ("bien respaldado (>=4 casas)", well_backed),
        ("poco respaldo (<4 casas)", ~well_backed),
    )
    for label, mask in backing_groups:
        n = int(mask.sum())
        if n == 0:
            continue
        yt = y_true[mask]
        mo = market_odds[mask]
        pnl = np.where(yt, mo - 1.0, -1.0)
        roi = float(pnl.sum() / n)
        print(f"  {label:<28s} n={n:4d}  hit_rate={float(np.mean(yt)):.1%}  roi={roi:+.3f}")

    _section("7. HIT RATE / ROI POR CALIDAD DE MERCADO (market_quality_tier)")
    tiers = [
        market_quality_tier(
            row["bookmakers_used"],
            row["market_odds_min"],
            row["market_odds_max"],
            row["market_odds_median"],
        )
        for row in settled
    ]
    for tier in ("HIGH", "MEDIUM", "LOW"):
        mask = [t == tier for t in tiers]
        n = sum(mask)
        if n == 0:
            continue
        yt = y_true[mask]
        mo = market_odds[mask]
        pnl = np.where(yt, mo - 1.0, -1.0)
        roi = float(pnl.sum() / n)
        print(f"  {tier:<10s} n={n:4d}  hit_rate={float(np.mean(yt)):.1%}  roi={roi:+.3f}")

    if target_match_id is not None:
        _section(f"8. CASO match_id={target_match_id} (paso a paso)")
        if not target:
            print(f"  No se encontraron predicciones para match_id={target_match_id}.")
        else:
            for p in target:
                print(f"  market={p['market']}")
                for key in (
                    "model_probability",
                    "market_probability",
                    "market_odds",
                    "edge",
                    "expected_value",
                    "risk_adjusted_expected_value",
                    "uncertainty_low",
                    "uncertainty_high",
                    "confidence",
                    "data_quality",
                    "bookmakers_used",
                    "signal_score_stored",
                ):
                    print(f"    {key:<28s} = {p[key]}")

    _section("9. TOP 10 JORNADA ACTUAL: score ALMACENADO (congelado) vs score EN VIVO")
    print("  (ambos usan la MISMA formula signal_score_v2; difieren si el")
    print("   stored se congelo ANTES de que existieran las curvas de edge/cuota,")
    print("   o si la curva ha cambiado desde entonces con mas liquidaciones)")
    for row in top10_live:
        stored = row["stored_signal_score"]
        stored_str = f"{stored:.4f}" if stored is not None else "None"
        print(
            f"  match={row['match_id']:>4} market={row['market']:<18} "
            f"live={row['live_score']:.4f}  stored={stored_str}  "
            f"model_p={row['model_probability']:.3f}  edge={(row['edge'] or 0):.3f}  "
            f"odds={row['market_odds']}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-id", type=int, default=None)
    args = parser.parse_args()
    main(args.match_id)
