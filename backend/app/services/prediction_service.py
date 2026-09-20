"""Genera y persiste predicciones para partidos programados de una competicion."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.features.goals import build_match_feature_table
from backend.app.market.consensus import compute_market_consensus
from backend.app.prediction.market_labels import DOUBLE_CHANCE_COMPONENTS
from backend.app.prediction.predictor import (
    predict_markets_for_table,
    predict_secondary_markets_for_table,
)
from backend.app.prediction.ranking import signal_score
from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import load_model_artifact
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

MARKET_TO_ODDS_LOOKUP = {
    # market_key -> (market en match_odds, line, selection "positiva")
    "over_1_5": ("over_under_goals", 1.5, "over"),
    "over_2_5": ("over_under_goals", 2.5, "over"),
    "under_2_5": ("over_under_goals", 2.5, "under"),
    "over_3_5": ("over_under_goals", 3.5, "over"),
    "btts": ("btts", None, "yes"),
    # 1X2: mismo mercado "match_result" que ya traen h2h (The Odds API) y
    # el dataset historico (Bet365) desde el principio -- solo faltaba
    # definirlo como mercado del modelo (ver market_labels.py) para que
    # compitiera en el ranking de senhales igual que los de goles.
    "home_win": ("match_result", None, "home"),
    "draw": ("match_result", None, "draw"),
    "away_win": ("match_result", None, "away"),
    # Doble oportunidad: ningun proveedor actual la trae como mercado
    # propio, por eso este lookup nunca encuentra filas reales en
    # MatchOdds para "double_chance" -- la cuota real se DERIVA sumando
    # los dos componentes de h2h (ver _add_derived_double_chance_quotes
    # mas abajo), que se ejecuta DESPUES de este lookup y solo rellena el
    # hueco si no hay ya una cuota real para este market_key (si algun dia
    # se integra una fuente que sí trae "double_chance" como mercado
    # propio, esa cuota real tiene prioridad sobre la derivada).
    "double_chance_1x": ("double_chance", None, "1x"),
    "double_chance_x2": ("double_chance", None, "x2"),
    "double_chance_12": ("double_chance", None, "12"),
    # Goles de un equipo: NINGUNA fuente actual trae esta cuota (ver
    # market_labels.py) -- este lookup nunca encontrara filas en
    # MatchOdds para "home_team_total_goals"/"away_team_total_goals" hoy,
    # asi que market_probability queda siempre None (nunca se inventa),
    # pero deja el mercado listo para cuando se integre una fuente que si
    # la traiga, sin tocar mas codigo que este diccionario.
    "home_team_over_0_5": ("home_team_total_goals", 0.5, "over"),
    "home_team_over_1_5": ("home_team_total_goals", 1.5, "over"),
    "away_team_over_0_5": ("away_team_total_goals", 0.5, "over"),
    "away_team_over_1_5": ("away_team_total_goals", 1.5, "over"),
}

# Tarjetas/corners: The Odds API no las ofrece (ver docs/data_sources.md),
# pero API-Football si puede traerlas (ver ingestion/api_football/odds_provider.py)
# guardadas bajo "cards_total"/"corners_total" en MatchOdds. Se combina con
# el lookup de goles para que el mismo bucle de mas abajo construya el
# consenso de mercado de TODOS los mercados sin duplicar logica.
SECONDARY_MARKET_TO_ODDS_LOOKUP = {
    key: (f"{spec.stat_family}_total", spec.line, spec.kind) for key, spec in SECONDARY_MARKET_DEFINITIONS.items()
}
ALL_MARKET_TO_ODDS_LOOKUP = {**MARKET_TO_ODDS_LOOKUP, **SECONDARY_MARKET_TO_ODDS_LOOKUP}


def _market_line_and_selection(market_key: str) -> tuple[float | None, str]:
    if market_key in MARKET_TO_ODDS_LOOKUP:
        _, line, selection = MARKET_TO_ODDS_LOOKUP[market_key]
        return line, selection
    spec = SECONDARY_MARKET_DEFINITIONS[market_key]
    return spec.line, spec.kind


def _add_derived_double_chance_quotes(market_quotes: dict, match_ids) -> None:
    """Doble oportunidad NUNCA depende de que una casa concreta ofrezca
    ese mercado exacto (a diferencia de btts/alternate_totals, que si):
    su cuota de mercado se DERIVA sumando las probabilidades sin vig ya
    calculadas para sus dos componentes de h2h (ver
    market_labels.py::DOUBLE_CHANCE_COMPONENTS) -- los 3 resultados de
    1X2 son mutuamente excluyentes, asi que la probabilidad de "1 o X" es
    exactamente P(1) + P(X) sin necesidad de ninguna cuota adicional.
    Solo rellena el hueco si el mercado NO tiene ya una cuota real (por si
    algun dia una fuente trae "double_chance" como mercado propio, esa
    cuota real prevalece sobre la derivada).

    Honestidad: `market_odds` resultante es un precio JUSTO derivado del
    consenso (1 / probabilidad), no una cuota realmente ofrecida por
    ninguna casa -- una casa real cobraria su propio margen sobre doble
    oportunidad, normalmente menor que en 1X2 pero no cero. Se marca con
    `market_probability_source="derived_double_chance"` para que quede
    claro en la UI/logs que no es una cuota observada.
    """
    for match_id in match_ids:
        for dc_key, (component_a, component_b) in DOUBLE_CHANCE_COMPONENTS.items():
            if (match_id, dc_key) in market_quotes:
                continue  # cuota real ya presente, no se sobreescribe
            quote_a = market_quotes.get((match_id, component_a))
            quote_b = market_quotes.get((match_id, component_b))
            if quote_a is None or quote_b is None:
                continue
            probability = min(quote_a["market_probability"] + quote_b["market_probability"], 1.0)
            if probability <= 0:
                continue
            market_quotes[(match_id, dc_key)] = {
                "market_probability": probability,
                "market_odds": 1.0 / probability,
                "vig_removed": quote_a["vig_removed"] and quote_b["vig_removed"],
                "market_probability_source": "derived_double_chance",
                "bookmakers_count": min(quote_a["bookmakers_count"], quote_b["bookmakers_count"]),
                "bookmakers_used": min(quote_a["bookmakers_used"], quote_b["bookmakers_used"]),
                "market_odds_min": None,
                "market_odds_max": None,
                "market_odds_median": None,
                "market_odds_average": None,
            }


def generate_predictions_for_competition(
    db: Session, competition_code: str, target_date: dt.date | None = None
) -> list[Prediction]:
    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        raise ValueError(f"Competicion no encontrada: {competition_code}")

    model_versions = (
        db.query(ModelVersion)
        .filter(ModelVersion.dataset_version.like(f"{competition_code}:%"))
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    if model_versions is None:
        logger.warning("prediction_service.no_model", extra={"competition": competition_code})
        return []

    artifact = load_model_artifact(model_versions)
    matches = load_matches_dataframe(db, competition.id)
    table = build_match_feature_table(matches)

    target_matches = table[table["home_goals"].isna()]
    if target_date is not None:
        target_matches = target_matches[target_matches["date"].dt.date == target_date]
    if target_matches.empty:
        return []

    match_odds_rows = {
        m.id: m.odds for m in db.query(Match).filter(Match.id.in_(target_matches["match_id"].tolist()))
    }
    market_quotes = {}
    for match_id, odds_rows in match_odds_rows.items():
        for market_key, (market, line, selection) in ALL_MARKET_TO_ODDS_LOOKUP.items():
            consensus = compute_market_consensus(odds_rows, market, line, selection)
            if consensus.market_probability is None:
                continue
            market_quotes[(match_id, market_key)] = {
                "market_probability": consensus.market_probability,
                "market_odds": consensus.market_odds,
                "vig_removed": consensus.market_probability_source == "consensus_no_vig"
                or consensus.market_probability_source == "single_book_no_vig",
                "market_probability_source": consensus.market_probability_source,
                "bookmakers_count": consensus.bookmakers_count,
                "bookmakers_used": consensus.bookmakers_used,
                "market_odds_min": consensus.min_odds,
                "market_odds_max": consensus.max_odds,
                "market_odds_median": consensus.median_odds,
                "market_odds_average": consensus.average_odds,
            }
            if consensus.outliers_removed:
                logger.info(
                    "market_consensus.outliers_removed: match_id=%d market=%s bookmakers=%s",
                    match_id,
                    market_key,
                    consensus.outliers_removed,
                )

    _add_derived_double_chance_quotes(market_quotes, match_odds_rows.keys())

    outputs = predict_markets_for_table(
        target_matches,
        statistical_model=artifact["dixon_coles"],
        ml_model=artifact["ml_classifier"],
        ensemble_weights=artifact.get("ensemble_weights"),
        market_quotes=market_quotes,
        calibrators=artifact.get("calibrators"),
    )
    if "cards_model" in artifact and "corners_model" in artifact:
        outputs += predict_secondary_markets_for_table(
            target_matches, artifact["cards_model"], artifact["corners_model"], market_quotes=market_quotes
        )

    # Sin esto, cada `predict`/`predict-upcoming`/`refresh` que se ejecuta
    # sobre los MISMOS partidos programados (algo normal: es el flujo pensado
    # para refrescar el dashboard varias veces al dia) va ACUMULANDO filas
    # nuevas en vez de sustituir las anteriores, porque `Prediction` nunca
    # tenia una clave unica por (match, market). El sintoma visible es un
    # mismo partido apareciendo duplicado (a veces triplicado) en "Las 5
    # mejores predicciones" o en el detalle de partido. Como estos partidos
    # aun no se han jugado, no hay ninguna razon para conservar la
    # prediccion vieja: se borra y se sustituye siempre por la mas reciente.
    target_match_ids = target_matches["match_id"].tolist()
    if target_match_ids:
        db.query(Prediction).filter(Prediction.match_id.in_(target_match_ids)).delete(
            synchronize_session=False
        )

    predictions = []
    for output in outputs:
        line, selection = _market_line_and_selection(output.market)
        prediction = Prediction(
            match_id=output.match_id,
            market=output.market,
            line=line,
            selection=selection,
            model_version_id=model_versions.id,
            model_probability=output.model_probability,
            market_probability=output.market_probability,
            market_odds=output.market_odds,
            fair_odds=output.fair_odds,
            edge=output.edge,
            expected_value=output.expected_value,
            confidence=output.confidence,
            data_quality=output.data_quality,
            explanation={"factors": output.explanation},
            features_used={"feature_names": [f["feature"] for f in output.explanation]},
            market_probability_source=output.market_probability_source,
            bookmakers_count=output.bookmakers_count,
            bookmakers_used=output.bookmakers_used,
            market_odds_min=output.market_odds_min,
            market_odds_max=output.market_odds_max,
            market_odds_median=output.market_odds_median,
            market_odds_average=output.market_odds_average,
            calibrated_probability=output.calibrated_probability,
        )
        # `signal_score` se calcula y CONGELA aqui, con los datos de mercado
        # vigentes en este momento (seccion 9 del brief: snapshot inmutable).
        # Nunca se recalcula despues con umbrales/pesos mas nuevos: si la
        # formula cambia, solo afecta a predicciones generadas A PARTIR de
        # ese cambio, no reescribe el pasado.
        prediction.signal_score = signal_score(prediction)
        db.add(prediction)
        predictions.append(prediction)

    db.commit()
    return predictions
