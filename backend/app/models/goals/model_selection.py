"""Selecciona, MERCADO A MERCADO, cual de varios clasificadores ML
candidatos generaliza mejor en el holdout de evaluacion -- nunca sustituye
un modelo por otro a ciegas (ver docs/architecture_audit.md, seccion 6,
punto 6: "requiere su propio backtest de validacion antes de reemplazar el
actual"). Usado por services/model_service.py para decidir, por mercado,
entre MarketClassifierModel (binario independiente por mercado),
MultinomialGroupClassifier (conjunto por grupo mutuamente excluyente) y,
cuando esta disponible, GradientBoostingMarketModel (LightGBM, ver
models/goals/gradient_boosting.py).
"""

from __future__ import annotations

import numpy as np

from backend.app.models.calibration.metrics import log_loss_score


class SelectedMarketClassifier:
    """Expone la MISMA interfaz que cualquier modelo ML de este paquete
    (`.pipelines_`, `.feature_cols_`, `.predict_market_probabilities`) para
    que predictor.py/explanation.py no necesiten saber cual candidato gano
    para cada mercado -- "duck typing" deliberado: los candidatos
    (LogisticRegression independiente, conjunto multinomial, LightGBM) no
    comparten ninguna clase base real, asi que la unica forma honesta de
    tratarlos de forma uniforme es por la forma, no por herencia forzada.
    """

    name = "selected_per_market_classifier"

    def __init__(self, feature_cols: list[str]) -> None:
        self.feature_cols_ = feature_cols
        self.pipelines_: dict[str, object] = {}
        self._class_label_for_market: dict[str, object] = {}
        self._invert_for_market: dict[str, bool] = {}
        self._source_by_market: dict[str, object] = {}
        # Solo para transparencia/metrics (seccion 34: "nunca sobrescribir
        # en silencio" -- que gano y por que queda trazable en el
        # ModelVersion.metrics persistido, no solo en memoria).
        self.winner_by_market: dict[str, str] = {}

    def register(
        self,
        market_key: str,
        source_model: object,
        winner_name: str,
        pipeline: object,
    ) -> None:
        self.pipelines_[market_key] = pipeline
        self._source_by_market[market_key] = source_model
        self.winner_by_market[market_key] = winner_name
        label_getter = getattr(source_model, "class_label_for_explanation", lambda _k: None)
        class_label = label_getter(market_key)
        if class_label is not None:
            self._class_label_for_market[market_key] = class_label
        if getattr(source_model, "invert_for_market", lambda _k: False)(market_key):
            self._invert_for_market[market_key] = True

    def predict_market_probabilities(self, table) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        cache: dict[int, dict[str, np.ndarray]] = {}
        for market_key, source_model in self._source_by_market.items():
            cache_key = id(source_model)
            if cache_key not in cache:
                cache[cache_key] = source_model.predict_market_probabilities(table)
            out[market_key] = cache[cache_key][market_key]
        return out

    def class_label_for_explanation(self, market_key: str) -> object | None:
        return self._class_label_for_market.get(market_key)

    def invert_for_market(self, market_key: str) -> bool:
        return self._invert_for_market.get(market_key, False)


def pick_best_candidate(
    y_true: np.ndarray,
    candidates: list[tuple[str, object, np.ndarray]],
) -> tuple[str, object, np.ndarray] | None:
    """`candidates`: lista de (nombre, modelo_origen, probabilidades) ya
    calculadas en el holdout PARA UN MERCADO. Devuelve el candidato con
    menor log loss, o `None` si ninguno es valido (el mercado se queda solo
    con el modelo estadistico, nunca se inventa una probabilidad ML)."""
    valid = [c for c in candidates if c[2] is not None]
    if not valid:
        return None
    return min(valid, key=lambda c: log_loss_score(y_true, c[2]))
