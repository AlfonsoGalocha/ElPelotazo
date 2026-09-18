"""Compara predicciones YA HECHAS contra el resultado REAL de partidos que
ya se jugaron ("cuando acabe la jornada, ver si el modelo acerto").

Por que esto no necesita infraestructura nueva de "aprendizaje online": los
modelos de este proyecto (Dixon-Coles/Poisson/clasificador ML) se
REENTRENAN por lotes desde cero cada vez que se llama a `train` (nunca
mantienen estado entre llamadas), leyendo TODOS los partidos ya jugados de
la BD. Asi que "que el modelo aprenda de la realidad" ya ocurre en cuanto
`update` trae los resultados reales de la jornada y se reentrena -- lo que
faltaba (ver `refresh_cache()` en `history_dataset.py`, bug real corregido
en el mismo cambio que este modulo) era que esos resultados reales
llegaran a la BD en absoluto, y esto: un informe explicito de "como de
bien predijo el modelo ANTES de saber el resultado", para poder verlo y
decidir con datos si hace falta ajustar algo (features, pesos del
ensemble, umbrales de ranking...) en vez de reentrenar a ciegas.

Las predicciones de un partido que ya se jugo NUNCA se borran cuando
termina (`generate_predictions_for_competition` solo borra/regenera las de
partidos SIN jugar): siguen en la BD tal y como se calcularon antes del
pitido inicial, listas para compararse contra el resultado real sin haber
tenido ninguna oportunidad de "hacer trampa" mirando el futuro.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from sqlalchemy.orm import Session

from backend.app.backtesting.metrics import market_strategy_metrics, model_quality_metrics
from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import Prediction
from backend.app.features.goals import build_match_feature_table
from backend.app.prediction.market_labels import MARKET_DEFINITIONS, label_for_market
from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS, label_for_secondary_market
from backend.app.services.match_service import load_matches_dataframe


def _label_for_any_market(table, market_key: str):
    if market_key in MARKET_DEFINITIONS:
        return label_for_market(table, market_key)
    if market_key in SECONDARY_MARKET_DEFINITIONS:
        return label_for_secondary_market(table, market_key)
    return None


def evaluate_settled_predictions(db: Session, competition_code: str | None = None) -> dict:
    """Para cada (competicion, mercado) con partidos YA FINALIZADOS que
    tenian una prediccion guardada, calcula Brier score / log loss /
    calibracion (siempre) y ROI hipotetico (solo si habia cuota de mercado
    real, para no mezclar "el modelo predice bien" con "esta cuota concreta
    habria dado dinero" -- ver backtesting/metrics.py).
    """
    competitions = (
        [db.query(Competition).filter_by(code=competition_code).one_or_none()]
        if competition_code
        else db.query(Competition).all()
    )
    report: dict = {"competitions": {}}

    for competition in competitions:
        if competition is None:
            continue
        matches = load_matches_dataframe(db, competition.id)
        table = build_match_feature_table(matches)
        finished = table[table["home_goals"].notna()]
        if finished.empty:
            continue

        finished_match_ids = finished["match_id"].tolist()
        predictions = db.query(Prediction).filter(Prediction.match_id.in_(finished_match_ids)).all()
        if not predictions:
            continue

        by_market: dict[str, list[Prediction]] = defaultdict(list)
        for prediction in predictions:
            by_market[prediction.market].append(prediction)

        table_by_match_id = finished.set_index("match_id")
        market_reports: dict = {}
        for market, preds in by_market.items():
            rows = table_by_match_id.loc[[p.match_id for p in preds]]
            y_true_series = _label_for_any_market(rows, market)
            if y_true_series is None:
                continue  # mercado desconocido (no deberia pasar, pero nunca se inventa una etiqueta)
            y_true = y_true_series.to_numpy().astype(bool)
            y_prob = np.array([p.model_probability for p in preds])

            quality = model_quality_metrics(y_true, y_prob)

            has_odds = np.array([p.market_odds is not None for p in preds])
            strategy = None
            if has_odds.any():
                strategy = market_strategy_metrics(
                    y_true[has_odds],
                    y_prob[has_odds],
                    np.array([p.market_odds for p in preds if p.market_odds is not None]),
                )

            market_reports[market] = {
                "n_predictions": len(preds),
                "n_with_market_odds": int(has_odds.sum()),
                "model_quality": quality,
                "market_strategy": strategy,
            }

        if market_reports:
            report["competitions"][competition.code] = market_reports

    return report
