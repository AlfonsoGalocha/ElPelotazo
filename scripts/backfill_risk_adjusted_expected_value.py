"""Backfill puntual (seccion 15.9 de docs/architecture_audit.md): rellena
`Prediction.risk_adjusted_expected_value` para predicciones YA LIQUIDADAS
que quedaron con esa columna NULL -- no por ningun error de calculo, sino
porque el campo se desplego el 2026-11-17 (commit "EV ajustado al riesgo +
anomalia de alta incertidumbre"), SEIS DIAS despues que
`uncertainty_low`/`uncertainty_high` (commit "Anade cuantificacion de
incertidumbre", 2026-11-11): toda prediccion creada en ese hueco de seis
dias ya tenia `uncertainty_low` calculado correctamente, pero
`risk_adjusted_expected_value` sencillamente no existia todavia como
campo. Al estar CONGELADO en el momento de crear cada prediccion (ver
docstring de `backtesting/ranking_comparison.py`), nunca se recalcula solo
-- por eso `scripts/debug_walk_forward_cap_binding.py` encontro el 100%
de las 1103 predicciones liquidadas evaluadas con esta columna NULL
(confirmado con el usuario, ver seccion 15.8), lo que colapsaba
`signal_score_v2` (y por tanto TODAS sus variantes con techo) a cero para
absolutamente todo el historico real: no habia ningun EV ajustado al
riesgo con el que puntuar nada.

Este backfill NO inventa ni altera ningun dato: `uncertainty_low` y
`market_odds` ya estaban correctamente calculados y guardados para esas
filas (nunca se toca ninguno de los dos aqui); `risk_adjusted_expected_value`
es una funcion PURA y DETERMINISTA de ambos
(`compute_expected_value(uncertainty_low, market_odds)`, en
`prediction/edge.py` -- la MISMA formula que usa `predictor.py` para cada
prediccion nueva desde que el campo existe). Nunca toca
`model_probability`, `market_probability`, `edge`, el resultado real
liquidado, ni ninguna fila que YA tenga `risk_adjusted_expected_value`
poblado (nunca "corrige" un valor ya calculado por el pipeline en vivo,
solo completa el hueco real). Idempotente: una segunda ejecucion no
actualiza nada (la condicion NULL ya no se cumple).

Deliberadamente deja intactas las filas sin `uncertainty_low` o sin
`market_odds` -- ahi no hay suficiente dato para derivar nada sin
inventarlo (seccion 9 del pedido original del usuario: "no inventes
datos si el registro ya no esta disponible").

Uso (dentro del contenedor):
    docker compose exec backend python scripts/backfill_risk_adjusted_expected_value.py

Tras correrlo, re-ejecutar
`football-edge compare-ranking-strategies-walk-forward` para ver si
`v2_edge_cap`/`v2_edge_cap_longshot` por fin difieren de `v2` con datos
reales -- esa es la validacion out-of-sample pendiente desde la seccion 15.6.
"""

from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.edge import compute_expected_value


def main() -> None:
    with session_scope() as db:
        rows = (
            db.query(Prediction)
            .filter(Prediction.risk_adjusted_expected_value.is_(None))
            .filter(Prediction.uncertainty_low.isnot(None))
            .filter(Prediction.market_odds.isnot(None))
            .all()
        )

        if not rows:
            print(
                "Ninguna prediccion con risk_adjusted_expected_value NULL y "
                "uncertainty_low/market_odds disponibles -- nada que rellenar."
            )
            return

        updated = 0
        for prediction in rows:
            prediction.risk_adjusted_expected_value = compute_expected_value(
                prediction.uncertainty_low, prediction.market_odds
            )
            updated += 1

        print(
            f"Rellenadas {updated} filas de Prediction.risk_adjusted_expected_value "
            "(unicamente via compute_expected_value(uncertainty_low, market_odds); "
            "ningun otro campo tocado)."
        )


if __name__ == "__main__":
    main()
