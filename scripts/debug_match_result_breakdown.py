"""Diagnostico puntual: para UN partido concreto, descompone la
probabilidad final de 1X2/doble oportunidad en sus dos ingredientes
SEPARADOS (Dixon-Coles puro vs clasificador ML puro) y el peso de mezcla
aprendido entre ambos -- para saber de donde sale realmente una
probabilidad final que parece demasiado alta (pedido real del usuario,
caso real: "modelo 81.9% en doble oportunidad 1X frente a 41.9% de
mercado, ¿es de verdad tan probable?").

El informe de calibracion (`football-edge calibration-report`) ya
descarto que el propio Dixon-Coles este mal calibrado (calibrar su salida
NO mejora el Brier/log-loss/ECE fuera de muestra en LaLiga para
home_win/draw/double_chance_1x) -- este script mira el OTRO sospechoso:
el peso de la mezcla y el componente ML, que `calibration-report` no
evalua (solo prueba Dixon-Coles aislado, nunca el ensemble real que usa
produccion).

Uso (dentro del contenedor):
    docker compose exec backend python scripts/debug_match_result_breakdown.py <match_id>
"""

from __future__ import annotations

import sys

import numpy as np

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion
from backend.app.features.goals import build_match_feature_table
from backend.app.prediction.market_labels import (
    MUTUALLY_EXCLUSIVE_GROUPS,
    renormalize_mutually_exclusive_groups,
)
from backend.app.prediction.probability import market_probabilities
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import load_model_artifact

MARKETS_OF_INTEREST = [
    "home_win",
    "draw",
    "away_win",
    "double_chance_1x",
    "double_chance_x2",
    "double_chance_12",
]


def main(match_id: int) -> None:
    with session_scope() as db:
        match = db.get(Match, match_id)
        if match is None:
            print(f"No existe ningun partido con id={match_id}.")
            return
        home, away = match.home_team.canonical_name, match.away_team.canonical_name
        competition = db.get(Competition, match.competition_id)
        print(f"Partido: {home} vs {away} ({competition.code}, id={match_id})")

        model_version = (
            db.query(ModelVersion)
            .filter(ModelVersion.dataset_version.like(f"{competition.code}:%"))
            .order_by(ModelVersion.created_at.desc())
            .first()
        )
        if model_version is None:
            print("No hay ningun modelo entrenado para esta competicion.")
            return
        artifact = load_model_artifact(model_version)

        matches = load_matches_dataframe(db, competition.id)

    table = build_match_feature_table(matches)
    row_df = table[table["match_id"] == match_id]
    if row_df.empty:
        print("Este partido no esta en la tabla de features (raro: deberia estarlo).")
        return

    dixon_coles = artifact["dixon_coles"]
    ml_model = artifact.get("ml_classifier")
    ensemble_weights = artifact.get("ensemble_weights") or {}

    stat_probs = market_probabilities(dixon_coles, row_df)
    ml_probs = ml_model.predict_market_probabilities(row_df) if ml_model is not None else {}

    print(
        "\n(p_stat = Dixon-Coles puro | p_ml = clasificador ML puro | "
        "w = peso de Dixon-Coles en la mezcla, aprendido en el holdout | "
        "blended = w*p_stat + (1-w)*p_ml, ANTES de renormalizar el grupo)\n"
    )

    blended_by_market: dict[str, np.ndarray] = {}
    for market_key in MARKETS_OF_INTEREST:
        if market_key not in stat_probs:
            continue
        p_stat = float(stat_probs[market_key][0])
        p_ml = float(ml_probs[market_key][0]) if market_key in ml_probs else None
        w = ensemble_weights.get(market_key)
        has_mix = p_ml is not None and w is not None
        blended = w * p_stat + (1 - w) * p_ml if has_mix else p_stat
        blended_by_market[market_key] = np.array([blended])
        p_ml_str = "—" if p_ml is None else f"{p_ml:.3f}"
        w_str = "—" if w is None else f"{w:.2f}"
        print(
            f"  {market_key:20s} p_stat={p_stat:.3f}  p_ml={p_ml_str}  "
            f"w_stat={w_str}  blended={blended:.3f}"
        )

    print("\n--- Tras renormalizar el grupo mutuamente excluyente (lo que ve el usuario) ---")
    for group in MUTUALLY_EXCLUSIVE_GROUPS:
        if not all(m in blended_by_market for m in group):
            continue
        renorm = renormalize_mutually_exclusive_groups({m: blended_by_market[m] for m in group})
        for m in group:
            print(f"  {m:20s} {renorm[m][0]:.3f}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Uso: python scripts/debug_match_result_breakdown.py <match_id>")
        sys.exit(1)
    main(int(sys.argv[1]))
