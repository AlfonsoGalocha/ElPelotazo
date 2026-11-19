"""Genera y persiste predicciones para partidos programados de una competicion."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.features import TeamMatchFeatures
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionSnapshot
from backend.app.features.goals import build_match_feature_table, feature_columns
from backend.app.market.consensus import compute_market_consensus
from backend.app.prediction.market_labels import DOUBLE_CHANCE_COMPONENTS
from backend.app.prediction.predictor import (
    predict_markets_for_table,
    predict_secondary_markets_for_table,
)
from backend.app.prediction.ranking import signal_score_v2
from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS
from backend.app.services.evaluation_service import (
    fit_edge_reliability_curve,
    market_track_records,
)
from backend.app.services.match_service import load_matches_dataframe
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
    # Doble oportunidad: API-Football SI trae este mercado como propio
    # (bug real corregido, ver ingestion/api_football/odds_provider.py --
    # el comentario anterior aqui decia "ningun proveedor lo trae", nunca
    # confirmado contra una respuesta real). Se guarda como 3 mercados
    # independientes de una sola cara, nunca 3 selecciones de un unico
    # "double_chance" (ver docstring de DOUBLE_CHANCE_VALUE_MAP alli: esas
    # 3 selecciones se SOLAPAN, no son un par complementario valido para
    # quitar el vig). `_add_derived_double_chance_quotes` (mas abajo) solo
    # rellena el hueco si esta fuente no trajo nada para un partido
    # concreto -- la cuota real de aqui SIEMPRE tiene prioridad.
    "double_chance_1x": ("double_chance_1x", None, "yes"),
    "double_chance_x2": ("double_chance_x2", None, "yes"),
    "double_chance_12": ("double_chance_12", None, "yes"),
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

# Corners: The Odds API no las ofrece (ver docs/data_sources.md), pero
# API-Football si puede traerlas (ver ingestion/api_football/odds_provider.py)
# guardadas bajo "corners_total" en MatchOdds. Se combina con el lookup de
# goles para que el mismo bucle de mas abajo construya el consenso de
# mercado de TODOS los mercados sin duplicar logica. (Tarjetas se quito
# del todo -- pedido real del usuario, ver secondary_markets.py: ninguna
# casa agregada por API-Football ofrece ese mercado para estas ligas.)
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
    """Red de seguridad, no la fuente principal: API-Football SI trae
    doble oportunidad como mercado propio (ver
    ingestion/api_football/odds_provider.py), y el lookup de mas arriba ya
    la recoge con prioridad. Esta funcion solo rellena el HUECO cuando esa
    fuente no trajo nada para un partido concreto (The Odds API no lo
    cubre en absoluto, o la casa no ofrecio el mercado esa vez): se deriva
    sumando las probabilidades sin vig ya calculadas para sus dos
    componentes de h2h (ver market_labels.py::DOUBLE_CHANCE_COMPONENTS) --
    los 3 resultados de 1X2 son mutuamente excluyentes, asi que la
    probabilidad de "1 o X" es exactamente P(1) + P(X). Nunca sobreescribe
    una cuota real ya presente.

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


def _json_safe_feature_value(raw: object) -> float | None:
    """`NaN` de pandas no es JSON valido (ausencia real de dato -- p.ej.
    un equipo recien ascendido sin partidos previos para calcular una
    media movil -- nunca se inventa un 0.0 u otro valor)."""
    return None if pd.isna(raw) else float(raw)


def _persist_team_match_features(
    db: Session, target_matches: pd.DataFrame, feature_cols: list[str], feature_set_version: str
) -> None:
    """Mision "historico propio": archiva, para cada partido que se esta
    prediciendo, exactamente las features (forma, fuerza, descanso...) que
    el modelo tenia disponibles en ESTE momento -- `TeamMatchFeatures`
    (db/models/features.py) ya existia en el esquema para esto
    ("Snapshot de features conocidas ANTES del kickoff... materializadas
    para reproducibilidad") pero nunca se habia conectado a ningun
    pipeline real (tabla muerta, confirmado por grep exhaustivo).

    `feature_set_version` se genera NUEVO en cada llamada (timestamp real,
    nunca reutilizado) a proposito: aunque el modelo activo no cambie,
    las features SI pueden cambiar entre una regeneracion y la siguiente
    (nuevos partidos jugados alimentan las medias moviles de forma) --
    reutilizar la misma version para refrescos sucesivos perderia esa
    evolucion real, igual que `PredictionSnapshot` para las predicciones.
    Solo usa las columnas de `feature_columns()` (la MISMA lista ya
    probada y usada para alimentar el clasificador ML real) filtradas por
    prefijo `home_`/`away_` -- nunca las columnas crudas del partido
    (`home_goals`, etc.), que `feature_columns()` ya excluye explicitamente
    por ser leakage severo (ver features/goals.py)."""
    shared_cols = [c for c in feature_cols if not c.startswith(("home_", "away_"))]
    home_cols = [c for c in feature_cols if c.startswith("home_")]
    away_cols = [c for c in feature_cols if c.startswith("away_")]
    as_of = dt.datetime.utcnow()
    for _, row in target_matches.iterrows():
        match_id = int(row["match_id"])
        home_features = {c[len("home_") :]: _json_safe_feature_value(row[c]) for c in home_cols}
        home_features["match_features"] = {c: _json_safe_feature_value(row[c]) for c in shared_cols}
        away_features = {c[len("away_") :]: _json_safe_feature_value(row[c]) for c in away_cols}
        db.add(
            TeamMatchFeatures(
                match_id=match_id,
                team_id=int(row["home_team_id"]),
                is_home=True,
                feature_set_version=feature_set_version,
                as_of=as_of,
                features=home_features,
            )
        )
        db.add(
            TeamMatchFeatures(
                match_id=match_id,
                team_id=int(row["away_team_id"]),
                is_home=False,
                feature_set_version=feature_set_version,
                as_of=as_of,
                features=away_features,
            )
        )
    db.flush()


def generate_predictions_for_competition(
    db: Session, competition_code: str, target_date: dt.date | None = None
) -> list[Prediction]:
    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        raise ValueError(f"Competicion no encontrada: {competition_code}")

    # Seccion "Rendimiento del modelo": la version ACTIVA (ver
    # services/promotion_service.py), nunca simplemente "la mas reciente"
    # -- un entrenamiento nuevo que no demostro mejoras fuera de muestra
    # queda guardado pero `is_active=False`, y production sigue usando la
    # anterior. `isnot(False)` trata `None` (ModelVersion de antes de
    # este gate) como activa, para no desactivar en silencio todo el
    # historico ya entrenado de un despliegue existente.
    model_versions = (
        db.query(ModelVersion)
        .filter(ModelVersion.dataset_version.like(f"{competition_code}:%"))
        .filter(ModelVersion.is_active.isnot(False))
        .order_by(ModelVersion.created_at.desc())
        .first()
    )
    if model_versions is None:
        logger.warning("prediction_service.no_model", extra={"competition": competition_code})
        return []

    artifact_bytes = Path(model_versions.artifact_path).read_bytes()
    model_artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    artifact = joblib.load(io.BytesIO(artifact_bytes))
    matches = load_matches_dataframe(db, competition.id)
    if matches.empty:
        return []
    table = build_match_feature_table(matches)

    target_matches = table[
        (table["status"] == "scheduled")
        & (table["date"] > dt.datetime.utcnow())
        & table["home_goals"].isna()
        & table["away_goals"].isna()
    ]
    if target_date is not None:
        target_matches = target_matches[target_matches["date"].dt.date == target_date]
    if target_matches.empty:
        return []

    feature_set_version = f"{model_versions.version}@{dt.datetime.utcnow().isoformat()}"
    _persist_team_match_features(
        db,
        target_matches,
        feature_columns(table),
        feature_set_version=feature_set_version,
    )

    match_odds_rows = {
        m.id: m.odds for m in db.query(Match).filter(Match.id.in_(target_matches["match_id"].tolist()))
    }
    market_quotes = {}
    for match_id, odds_rows in match_odds_rows.items():
        for market_key, (market, line, selection) in ALL_MARKET_TO_ODDS_LOOKUP.items():
            market_rows = [
                odds for odds in odds_rows
                if odds.market == market and odds.line == line and odds.snapshot_type == "pre_match"
            ]
            consensus = compute_market_consensus(market_rows, market, line, selection)
            if consensus.market_probability is None:
                continue
            market_quotes[(match_id, market_key)] = {
                "odds_origin_at": min(o.provider_updated_at for o in market_rows)
                if market_rows and all(
                    o.provider_updated_at is not None and o.fetched_at is not None
                    and o.recorded_at is not None
                    and o.provider_updated_at <= o.fetched_at <= o.recorded_at
                    for o in market_rows
                ) else None,
                "history_quote_ids": sorted(o.history_quote_id for o in market_rows)
                if all(o.history_quote_id is not None for o in market_rows) else None,
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
    if "corners_model" in artifact:
        outputs += predict_secondary_markets_for_table(
            target_matches, artifact["corners_model"], market_quotes=market_quotes
        )

    # Sin esto, cada `predict`/`predict-upcoming`/`refresh` que se ejecuta
    # sobre los MISMOS partidos programados (algo normal: es el flujo pensado
    # para refrescar el dashboard varias veces al dia) va ACUMULANDO filas
    # nuevas en vez de sustituir las anteriores, porque `Prediction` nunca
    # tenia una clave unica por (match, market). El sintoma visible es un
    # mismo partido apareciendo duplicado (a veces triplicado) en "Las 5
    # mejores predicciones" o en el detalle de partido. Como estos partidos
    # aun no se han jugado, no hay ninguna razon para conservar la
    # prediccion vieja COMO "LA ACTUAL": se borra y se sustituye siempre
    # por la mas reciente.
    #
    # Pero "no conservarla como la actual" no es lo mismo que "perderla
    # para siempre" (pedido real del usuario, mision "historico inmutable
    # propio"): antes de borrarla, se archiva tal cual en
    # `PredictionSnapshot` (append-only, nunca se borra ni se actualiza --
    # ver ese modelo). Asi, si la cuota se mueve de 1.65 a 1.52 entre el
    # lunes y el viernes, la prediccion del lunes sigue consultable
    # despues (que probabilidad, que cuota, que version de modelo) aunque
    # ya no sea "la actual" -- nunca se inventa ni se reconstruye a partir
    # de informacion posterior, es una copia exacta de como estaba en ese
    # momento.
    target_match_ids = target_matches["match_id"].tolist()
    if target_match_ids:
        superseded = (
            db.query(Prediction).filter(Prediction.match_id.in_(target_match_ids)).all()
        )
        for old in superseded:
            db.add(
                PredictionSnapshot(
                    original_prediction_id=old.id,
                    match_id=old.match_id,
                    market=old.market,
                    line=old.line,
                    selection=old.selection,
                    model_version_id=old.model_version_id,
                    model_probability=old.model_probability,
                    calibrated_probability=old.calibrated_probability,
                    uncertainty_low=old.uncertainty_low,
                    uncertainty_high=old.uncertainty_high,
                    market_probability=old.market_probability,
                    market_odds=old.market_odds,
                    fair_odds=old.fair_odds,
                    edge=old.edge,
                    expected_value=old.expected_value,
                    risk_adjusted_expected_value=old.risk_adjusted_expected_value,
                    confidence=old.confidence,
                    data_quality=old.data_quality,
                    explanation=old.explanation,
                    features_used=old.features_used,
                    market_probability_source=old.market_probability_source,
                    bookmakers_count=old.bookmakers_count,
                    bookmakers_used=old.bookmakers_used,
                    market_odds_min=old.market_odds_min,
                    market_odds_max=old.market_odds_max,
                    market_odds_median=old.market_odds_median,
                    market_odds_average=old.market_odds_average,
                    odds_origin_at=old.odds_origin_at,
                    feature_set_version=old.feature_set_version,
                    signal_score=old.signal_score,
                    created_at=old.created_at,
                )
            )
        db.flush()
        db.query(Prediction).filter(Prediction.match_id.in_(target_match_ids)).delete(
            synchronize_session=False
        )

    # `market_track_record` (ver prediction/ranking.py::signal_score) para el
    # `signal_score` CONGELADO: se calcula UNA VEZ por competicion con el
    # conocimiento real disponible en este momento, para que el snapshot
    # historico refleje lo mismo que el ranking en vivo habria usado -- no
    # el valor neutro por defecto.
    track_records = market_track_records(db, competition_code)
    edge_reliability_curve = fit_edge_reliability_curve(db)

    predictions = []
    for output in outputs:
        line, selection = _market_line_and_selection(output.market)
        captured_market = market_quotes.get((output.match_id, output.market), {})
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
            features_used={
                "feature_names": [f["feature"] for f in output.explanation],
                "model_artifact_sha256": model_artifact_sha256,
                "consensus_input_history_ids": captured_market.get("history_quote_ids"),
            },
            market_probability_source=output.market_probability_source,
            bookmakers_count=output.bookmakers_count,
            bookmakers_used=output.bookmakers_used,
            market_odds_min=output.market_odds_min,
            market_odds_max=output.market_odds_max,
            market_odds_median=output.market_odds_median,
            market_odds_average=output.market_odds_average,
            odds_origin_at=captured_market.get("odds_origin_at"),
            feature_set_version=feature_set_version,
            calibrated_probability=output.calibrated_probability,
            uncertainty_low=output.uncertainty_low,
            uncertainty_high=output.uncertainty_high,
            risk_adjusted_expected_value=output.risk_adjusted_expected_value,
        )
        # `signal_score` se calcula y CONGELA aqui, con los datos de mercado
        # vigentes en este momento (seccion 9 del brief: snapshot inmutable).
        # Nunca se recalcula despues con umbrales/pesos mas nuevos: si la
        # formula cambia, solo afecta a predicciones generadas A PARTIR de
        # ese cambio, no reescribe el pasado.
        #
        # Usa `signal_score_v2` (revision "motor 2.0", activada en
        # produccion tras evidencia real: `ensemble-calibration-report`
        # descarto calibracion en los 4 mercados centrales de LaLiga, y
        # `compare-ranking-strategies` mostro a v1 favoreciendo edges
        # enormes con probabilidad poco respaldada -- el sintoma real
        # reportado por el usuario. Ver docs/architecture_audit.md seccion
        # 11). `signal_score` (v1) se mantiene como funcion aparte, nunca
        # borrada, para seguir pudiendo comparar contra ella si en el
        # futuro hace falta.
        track_record = track_records.get((competition_code, output.market))
        prediction.signal_score = signal_score_v2(
            prediction,
            market_track_record=track_record,
            edge_reliability_curve=edge_reliability_curve,
        )
        db.add(prediction)
        predictions.append(prediction)

    from backend.app.services.publication_service import capture_publications

    capture_publications(db, predictions, track_records)
    db.commit()
    return predictions
