"""Entrenamiento y versionado de modelos (seccion 34: nunca sobrescribir en silencio)."""

from __future__ import annotations

import datetime as dt

import joblib
import numpy as np
from sqlalchemy.orm import Session

from backend.app.config.settings import get_settings
from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import ModelVersion
from backend.app.features.goals import build_match_feature_table, feature_columns
from backend.app.models.calibration.calibrators import IsotonicCalibrator
from backend.app.models.calibration.metrics import (
    brier_score,
    expected_calibration_error,
    log_loss_score,
    reliability_curve,
)
from backend.app.models.ensemble.ensemble import learn_ensemble_weights
from backend.app.models.goals.baseline import LeagueAverageBaseline
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.models.goals.gradient_boosting import GradientBoostingMarketModel
from backend.app.models.goals.ml_classifier import MarketClassifierModel
from backend.app.models.goals.model_selection import SelectedMarketClassifier, pick_best_candidate
from backend.app.models.goals.multinomial_classifier import MultinomialGroupClassifier
from backend.app.prediction.market_labels import (
    MARKET_DEFINITIONS,
    MUTUALLY_EXCLUSIVE_GROUPS,
    label_for_market,
    renormalize_mutually_exclusive_groups,
)
from backend.app.prediction.probability import market_probabilities
from backend.app.prediction.secondary_markets import (
    InsufficientSecondaryMarketDataError,
    TotalCountPoissonModel,
    has_real_count_data,
    label_for_secondary_market,
)
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.mlflow_tracking import log_training_run
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

MIN_HOLDOUT_MATCHES = 100  # una temporada en curso con pocos partidos jugados
# no es un holdout fiable (demasiado ruido estadistico); se usa la ultima
# temporada con muestra suficiente. Solo se ajusta con fechas anteriores.

MIN_CALIBRATION_MATCHES = 60  # ajustar un IsotonicRegression con menos
# puntos que esto sobreajusta al ruido del holdout (seccion 11 del brief):
# se prefiere NO calibrar (calibrated_probability queda None) a calibrar mal
# con una muestra insuficiente.

CALIBRATION_VALIDATION_FRACTION = 0.3  # mismo criterio que
# backtesting/calibration.py::compare_calibration
MIN_CALIBRATION_VALIDATION_SAMPLES = 10  # idem


def _fit_validated_isotonic_calibrator(
    p_ensemble: np.ndarray, y_true: np.ndarray, dates: np.ndarray | None = None
) -> tuple[IsotonicCalibrator | None, dict]:
    """Ajusta Isotonic Regression SOLO si demuestra mejorar el Brier score
    en un sub-split de validacion DISJUNTO del usado para ajustarlo --
    pedido real del usuario ("mision plataforma profesional", Bloque 1.4:
    "No elijas un metodo por moda. Compara su rendimiento mediante
    validacion temporal y datos fuera de muestra").

    Bug real confirmado en produccion (captura de pantalla del usuario,
    2026-11-19): Bundesliga, "Ambos equipos marcan" -- el modelo crudo
    estimaba 78.5%, pero `calibrated_probability` salia en 97.2% (+18.7pp),
    inflando el EV estimado a +42.9% de la nada. Causa real: antes de este
    fix, `IsotonicCalibrator` se ajustaba SIEMPRE que hubiera
    `MIN_CALIBRATION_MATCHES` partidos en el holdout GLOBAL, sin comprobar
    nunca si calibrar mejoraba algo para ESE mercado en particular.
    Isotonic Regression es muy flexible (sin sesgo, pero alta varianza):
    con una muestra modesta puede sobreajustar un cluster pequenho de la
    zona de probabilidad alta (p.ej. "las pocas veces que el modelo dio
    ~78%, todas acertaron por casualidad") y quedarse pegado a ese valor
    extremo para cualquier entrada posterior, por la propia monotonicidad
    del metodo.

    Nunca se corrige con un limite artificial (85%/90%, pedido EXPLICITO
    del usuario que NO hacer: "no quiero que simplemente anhadas un
    limite artificial") -- se corrige exigiendo evidencia real de que
    calibrar MEJORA el Brier score fuera de muestra, la MISMA logica ya
    implementada y probada en
    `backtesting/calibration.py::compare_calibration` (hasta ahora solo
    se usaba para informes offline bajo demanda, nunca para decidir si
    activar la calibracion real en produccion).

    Devuelve `(None, reporte)` si la muestra es insuficiente para validar
    o si calibrar NO mejora el Brier score (en ambos casos,
    `calibrated_probability` queda `None` para ese mercado -- el sistema
    ya usa `model_probability` cruda como fallback en todo el pipeline,
    nunca se inventa una calibracion sin evidencia). Si se valida, el
    calibrador final se REAJUSTA sobre TODO el holdout (maximiza los datos
    para el calibrador que entra en produccion) -- la decision de SI
    calibrar ya se tomo con evidencia honesta fuera de muestra."""
    n = len(y_true)
    n_val = int(n * CALIBRATION_VALIDATION_FRACTION)
    if dates is not None and n_val < n:
        n_val = int(np.searchsorted(dates, dates[n_val], side="left"))
    n_test = n - n_val
    if n_val < MIN_CALIBRATION_VALIDATION_SAMPLES or n_test < MIN_CALIBRATION_VALIDATION_SAMPLES:
        return None, {
            "accepted": False,
            "reason": "muestra insuficiente para validar la calibracion fuera de muestra",
            "n_holdout": n,
        }

    val_idx, test_idx = np.arange(n_val), np.arange(n_val, n)
    probe = IsotonicCalibrator().fit(p_ensemble[val_idx], y_true[val_idx])
    calibrated_test = probe.transform(p_ensemble[test_idx])
    brier_before = brier_score(y_true[test_idx], p_ensemble[test_idx])
    brier_after = brier_score(y_true[test_idx], calibrated_test)

    log_before = log_loss_score(y_true[test_idx], p_ensemble[test_idx])
    log_after = log_loss_score(y_true[test_idx], calibrated_test)
    report = {
        "accepted": bool(brier_after < brier_before and log_after <= log_before),
        "log_loss_before": log_before,
        "log_loss_after": log_after,
        "ece_before": expected_calibration_error(y_true[test_idx], p_ensemble[test_idx]),
        "ece_after": expected_calibration_error(y_true[test_idx], calibrated_test),
        "reliability_before": reliability_curve(y_true[test_idx], p_ensemble[test_idx]),
        "reliability_after": reliability_curve(y_true[test_idx], calibrated_test),
        "brier_before": brier_before,
        "brier_after": brier_after,
        "n_validation": int(n_val),
        "n_test": int(n_test),
    }
    if not report["accepted"]:
        return None, report
    return IsotonicCalibrator().fit(p_ensemble, y_true), report


def _corner_survival_beats_isotonic(
    corner_survival: dict | None,
    corner_survival_report: dict,
    over_markets: list[str],
    corner_calibration: dict[str, dict],
) -> bool:
    """Compara, de forma honesta y conservadora, la mejora de
    `corner_survival` (correccion monotona COMPARTIDA entre todas las
    lineas `_over_` de corners) contra la ya validada por
    `_fit_validated_isotonic_calibrator` para esas mismas lineas (cada
    una con su propio holdout). Los dos metodos se miden en bloques
    temporales distintos, asi que nunca se comparan sus valores
    absolutos de log loss -- se compara el RATIO de mejora (despues/
    antes de calibrar) de cada uno sobre su propia probabilidad cruda,
    y se exige que `corner_survival` mejore MAS que el mejor calibrador
    isotonico individual disponible para ganar."""
    if corner_survival is None:
        return False
    survival_before = corner_survival_report["original_holdout"]["log_loss"]
    survival_after = corner_survival_report["corrected_holdout"]["log_loss"]
    if not survival_before or survival_before <= 0:
        return False
    survival_ratio = survival_after / survival_before
    isotonic_ratios = []
    for market_key in over_markets:
        report = corner_calibration.get(market_key) or {}
        if not report.get("accepted"):
            continue
        before, after = report.get("log_loss_before"), report.get("log_loss_after")
        if before and before > 0:
            isotonic_ratios.append(after / before)
    if not isotonic_ratios:
        # Ningun calibrador isotonico individual demostro mejora para
        # estas lineas, pero corner_survival si (ya se comprobo arriba)
        # -- gana por ser la unica evidencia real disponible.
        return True
    return survival_ratio < min(isotonic_ratios)


def _choose_ml_source_per_group(
    candidate_models: dict[str, object],
    eval_finished,
) -> dict[str, tuple[str, object]]:
    """Decide, UN SOLO GANADOR POR GRUPO mutuamente excluyente (nunca
    mercado a mercado dentro del mismo grupo): si "home_win" usara un
    candidato y "draw" otro distinto, sus probabilidades volverian a poder
    no sumar 1 entre si -- exactamente la inconsistencia que
    `MultinomialGroupClassifier` existe para evitar. Comparando por la
    media de log loss de TODO el grupo se preserva esa coherencia por
    construccion, no por convencion.

    `candidate_models`: {nombre: modelo_ya_entrenado}, cualquier numero de
    candidatos (hoy: clasificador independiente, conjunto multinomial,
    LightGBM -- ver model_service.train_competition_models). Devuelve
    {market_key: (nombre_ganador, modelo_origen)} SOLO para los mercados
    que pertenecen a algun grupo. Los mercados fuera de cualquier grupo se
    resuelven aparte en el llamador.
    """
    # El primero es el fallback cuando la comparacion no es valida.
    default_name = next(iter(candidate_models))
    winners: dict[str, tuple[str, object]] = {}
    for group in MUTUALLY_EXCLUSIVE_GROUPS:
        losses: dict[str, list[float]] = {name: [] for name in candidate_models}
        complete = True
        for market_key in group:
            y_true = label_for_market(eval_finished, market_key).to_numpy()
            for name, model in candidate_models.items():
                p = model.predict_market_probabilities(eval_finished).get(market_key)
                if p is None:
                    complete = False
                    break
                losses[name].append(log_loss_score(y_true, p))
            if not complete:
                break
        # Si algun candidato no cubre el grupo entero (p.ej. una sola clase
        # en el holdout para algun mercado del grupo) no hay comparacion
        # valida posible: se usa el primer candidato (comportamiento
        # identico al de antes de anhadir mas candidatos), nunca un
        # ganador a medias.
        winner_name = default_name
        if complete:
            avg_loss = {name: float(np.mean(vals)) for name, vals in losses.items()}
            winner_name = min(avg_loss, key=avg_loss.get)
        for market_key in group:
            winners[market_key] = (winner_name, candidate_models[winner_name])
    return winners


def _pick_holdout_season(table) -> str | None:
    finished = table.dropna(subset=["home_goals", "away_goals"])
    if finished.empty:
        return None
    counts = finished.sort_values("date")["season_label"].value_counts()
    seasons_in_order = list(dict.fromkeys(finished.sort_values("date")["season_label"]))
    if len(seasons_in_order) < 2:
        return None

    for candidate in reversed(seasons_in_order[1:]):
        if counts[candidate] >= MIN_HOLDOUT_MATCHES:
            return candidate
    return seasons_in_order[-1]


def rolling_training_split(table):
    """Keep the newest evaluation reserve intact, including tied kickoffs."""
    dates = table["date"].sort_values()
    reserve = min(MIN_HOLDOUT_MATCHES, max(1, int(len(dates) * .30)))
    boundary = dates.iloc[-reserve]
    return table[table["date"] < boundary], table[table["date"] >= boundary]


def train_competition_models(db: Session, competition_code: str, *, rolling=False, skip_unchanged=False, closed_result_ids=None, open_round_result_ids=None) -> dict:
    """Entrena baseline + Dixon-Coles + clasificador ML para una competicion,
    evalua en la ultima temporada disponible como holdout, y persiste
    artefactos + ModelVersion. Devuelve un resumen (dict) del entrenamiento.
    """
    settings = get_settings()
    competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if competition is None:
        raise ValueError(f"Competicion no encontrada: {competition_code}")

    from hashlib import sha256

    observed_at = dt.datetime.utcnow()
    matches = load_matches_dataframe(db, competition.id)
    if matches.empty:
        return {"competition": competition_code, "status": "insufficient_data", "n_matches": 0}
    finished = matches[
        (matches["status"] == "finished") & (matches["date"] < observed_at)
    ].dropna(subset=["home_goals", "away_goals"])
    if rolling:
        finished = finished[(finished["home_goals"] >= 0) & (finished["away_goals"] >= 0)]
    if open_round_result_ids:
        finished = finished[~finished["match_id"].isin(open_round_result_ids)]
    if len(finished) < 50:
        return {"competition": competition_code, "status": "insufficient_data", "n_matches": len(finished)}

    frozen = finished.sort_values(["date", "match_id"]).reset_index(drop=True)
    records = frozen.to_json(orient="records", date_format="iso", double_precision=15)
    partition = "rolling-v1" if rolling else "season-v1"
    dataset_hash = sha256((partition + "\n" + records).encode()).hexdigest()
    previous = db.query(ModelVersion).filter(
        ModelVersion.dataset_version.like(f"learning:{competition_code}:%") if rolling
        else ModelVersion.dataset_version.like(f"{competition_code}:%"),
    ).order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).first()
    if rolling and previous is None:
        previous = db.query(ModelVersion).filter(
            ModelVersion.dataset_version.like(f"{competition_code}:%"),
        ).order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).first()
    previous_manifest = (previous.hyperparameters or {}).get("learning_manifest", {}) if previous else {}
    if skip_unchanged and previous_manifest.get("dataset_sha256") == dataset_hash:
        return {"competition": competition_code, "status": "unchanged", "model_version_id": previous.id}

    table = build_match_feature_table(frozen)
    holdout_season = None if rolling else _pick_holdout_season(table)
    if rolling:
        train_table, eval_table = rolling_training_split(table)
    elif holdout_season:
        eval_table = table[table["season_label"] == holdout_season]
        train_table = table[table["date"] < eval_table["date"].min()]
    else:
        split_date = table.iloc[int(len(table) * 0.7)]["date"]
        train_table = table[table["date"] < split_date]
        eval_table = table[table["date"] >= split_date]
    if train_table.empty or eval_table.empty:
        return {"competition": competition_code, "status": "insufficient_temporal_split"}

    baseline = LeagueAverageBaseline().fit(train_table)
    dixon_coles = DixonColesModel().fit(train_table)
    # Tres candidatos ML independientes, TODOS entrenados siempre: el
    # clasificador binario por mercado de siempre, el clasificador CONJUNTO
    # por grupo mutuamente excluyente (seccion 6, punto 6 de
    # docs/architecture_audit.md -- la correccion de raiz, no solo el
    # sintoma que ya corrige `renormalize_mutually_exclusive_groups`), y
    # LightGBM (seccion 7: "CatBoost/LightGBM anhadido al ensemble, nunca
    # sustituyendo a ciegas"). Se elige, mercado a mercado (o grupo a grupo
    # cuando aplica, ver mas abajo), el que generalice mejor en el holdout
    # real -- nunca se sustituye uno por otro sin ese backtest.
    ml_candidate_models: dict[str, object] = {
        "logistic_independent": MarketClassifierModel().fit(train_table),
        "multinomial_joint": MultinomialGroupClassifier().fit(train_table),
        "gradient_boosting": GradientBoostingMarketModel().fit(train_table),
    }
    selected_ml = SelectedMarketClassifier(feature_columns(table))

    metrics: dict = {"markets": {}}
    ensemble_weights: dict[str, float] = {}
    p_ensemble_by_market: dict[str, object] = {}
    y_true_by_market: dict[str, object] = {}

    eval_finished = eval_table.sort_values("date")
    split_at = eval_finished.iloc[int(len(eval_finished) * 0.4)]["date"]
    selection_mask = (eval_finished["date"] < split_at).to_numpy()
    if not selection_mask.any():
        return {"competition": competition_code, "status": "insufficient_temporal_split"}
    calibration_mask = ~selection_mask
    model_selection = eval_finished.loc[selection_mask]
    grouped_markets = {m for group in MUTUALLY_EXCLUSIVE_GROUPS for m in group}
    # "multinomial_joint" no cubre mercados fuera de un grupo (ver
    # MultinomialGroupClassifier): la comparacion por grupo solo tiene
    # sentido con los candidatos que SI cubren esos mercados.
    ml_winner_by_group_market = (
        _choose_ml_source_per_group(ml_candidate_models, model_selection)
        if len(eval_finished) > 0
        else {}
    )
    ungrouped_candidate_models = {
        name: model for name, model in ml_candidate_models.items() if name != "multinomial_joint"
    }

    for market_key in MARKET_DEFINITIONS:
        market_metrics = {}
        if len(eval_finished) > 0:
            y_true = label_for_market(eval_finished, market_key).to_numpy()
            y_true_by_market[market_key] = y_true

            p_baseline = market_probabilities(baseline, eval_finished)[market_key]
            p_dc = market_probabilities(dixon_coles, eval_finished)[market_key]
            p_by_candidate = {
                name: model.predict_market_probabilities(eval_finished).get(market_key)
                for name, model in ml_candidate_models.items()
            }

            market_metrics["baseline"] = {
                "brier_score": brier_score(y_true, p_baseline),
                "log_loss": log_loss_score(y_true, p_baseline),
            }
            market_metrics["dixon_coles"] = {
                "brier_score": brier_score(y_true, p_dc),
                "log_loss": log_loss_score(y_true, p_dc),
            }
            for candidate_name, p_candidate in p_by_candidate.items():
                if p_candidate is not None:
                    market_metrics[candidate_name] = {
                        "brier_score": brier_score(y_true, p_candidate),
                        "log_loss": log_loss_score(y_true, p_candidate),
                    }

            if market_key in grouped_markets:
                # Ganador decidido A NIVEL DE GRUPO (ver _choose_ml_source_per_group):
                # todos los mercados del mismo grupo usan el MISMO candidato,
                # para no reintroducir la inconsistencia entre ellos.
                winner_name, winner_source = ml_winner_by_group_market[market_key]
                winner = (winner_name, winner_source, p_by_candidate[winner_name])
            else:
                candidates = [
                    (name, model, p_by_candidate[name][selection_mask] if p_by_candidate[name] is not None else None)
                    for name, model in ungrouped_candidate_models.items()
                ]
                winner = pick_best_candidate(y_true[selection_mask], candidates)

            if winner is not None and winner[2] is not None:
                winner_name, winner_source, _ = winner
                p_ml = p_by_candidate[winner_name]
                winner_pipeline = winner_source.pipelines_[market_key]
                selected_ml.register(market_key, winner_source, winner_name, winner_pipeline)
                market_metrics["ml_winner"] = winner_name

                weights, ensemble_loss = learn_ensemble_weights(
                    [p_dc[selection_mask], p_ml[selection_mask]], y_true[selection_mask]
                )
                ensemble_weights[market_key] = weights[0]
                market_metrics["ensemble"] = {
                    "weight_statistical": weights[0],
                    "log_loss": ensemble_loss,
                }
                p_ensemble_by_market[market_key] = weights[0] * p_dc + weights[1] * p_ml
            else:
                p_ensemble_by_market[market_key] = p_dc
        metrics["markets"][market_key] = market_metrics

    # Calibracion (seccion 9/11 del brief): se ajusta sobre la MISMA
    # probabilidad ensemble RENORMALIZADA (grupos mutuamente excluyentes ya
    # forzados a sumar 1, ver market_labels.py) que se servira en produccion
    # -- calibrar sobre la version cruda serviria un `calibrated_probability`
    # que no corresponde a la probabilidad realmente usada para el edge.
    # Ajustado SOLO en el holdout (nunca visto por dixon_coles/ml_classifier
    # durante el fit), igual que `learn_ensemble_weight` -- no hay leakage.
    from backend.app.models.calibration.coherent import RESULT, fit_temperature

    result_temperature = None
    result_validation = {"reason": "insufficient_sample"}
    if all(market in p_ensemble_by_market and market in y_true_by_market for market in RESULT):
        original = renormalize_mutually_exclusive_groups(p_ensemble_by_market)
        x = np.column_stack([original[m] for m in RESULT])[calibration_mask]
        y = np.column_stack([y_true_by_market[m] for m in RESULT])[calibration_mask]
        result_temperature, result_validation = fit_temperature(
            x, y.argmax(axis=1), eval_finished.loc[calibration_mask, "date"].to_numpy(),
        )
    calibrators: dict[str, IsotonicCalibrator] = {}
    if int(calibration_mask.sum()) >= MIN_CALIBRATION_MATCHES:
        p_ensemble_by_market = renormalize_mutually_exclusive_groups(p_ensemble_by_market)
        calibration_reports: dict[str, dict] = {}
        for market_key, p_ensemble in p_ensemble_by_market.items():
            y_true = y_true_by_market.get(market_key)
            if y_true is None or len(set(y_true.tolist())) < 2:
                continue
            calibrator, report = _fit_validated_isotonic_calibrator(
                np.asarray(p_ensemble, dtype=float)[calibration_mask],
                y_true[calibration_mask],
                eval_finished.loc[calibration_mask, "date"].to_numpy(),
            )
            calibration_reports[market_key] = report
            if calibrator is not None:
                calibrators[market_key] = calibrator
        metrics["calibration"] = {
            "method": "isotonic" if calibrators else None,
            "n_holdout_matches": len(eval_finished),
            "markets_calibrated": sorted(calibrators.keys()),
            "n_model_selection": int(selection_mask.sum()),
            "n_calibration_pool": int(calibration_mask.sum()),
            "temporal_diagnostics": calibration_reports,
            # Mercados con evidencia suficiente para intentar calibrar pero
            # SIN mejora real demostrada (o muestra insuficiente para
            # validar): quedan en model_probability cruda, nunca sin
            # explicacion de por que (pedido real del usuario, "Bloque
            # 1.4": documentar la razon de la eleccion).
            "markets_rejected": {
                k: v for k, v in calibration_reports.items() if k not in calibrators
            },
        }
    else:
        metrics["calibration"] = {
            "method": None,
            "n_holdout_matches": len(eval_finished),
            "reason": f"menos de {MIN_CALIBRATION_MATCHES} partidos en el holdout",
        }

    metrics["joint_1x2"] = result_validation

    # Corners (seccion 18/19 del roadmap): un unico Poisson sobre el TOTAL
    # del partido. Se entrena y evalua igual que los mercados de goles, y
    # se empaqueta en el MISMO artefacto (una unica ejecucion de
    # entrenamiento por competicion) para no multiplicar ModelVersions
    # casi identicas. Tarjetas se quito del todo (pedido real del
    # usuario): ninguna casa agregada por API-Football ofrece ese mercado
    # para estas ligas, asi que entrenar/predecir tarjetas solo producia
    # predicciones "sin mercado" permanentes, sin cuota real con la que
    # compararse -- ver docstring de secondary_markets.py.
    secondary_models: dict[str, TotalCountPoissonModel] = {}
    corner_calibration = {}
    corner_calibration_choice: dict[str, str] = {}
    corner_survival = None
    for stat_family in ("corners",):
        try:
            model = TotalCountPoissonModel(stat_family).fit(train_table)
        except InsufficientSecondaryMarketDataError as exc:
            # Competicion nueva (o sin fuente con corners todavia): se
            # salta este mercado para ESTA ejecucion en vez de tumbar el
            # entrenamiento entero de goles/1X2/BTTS por su culpa. Sin
            # "corners_model" en el artefacto, las predicciones de corners
            # simplemente no se generan (nunca "sin mercado" con una
            # probabilidad inventada).
            logger.warning(
                "model_service.secondary_market_skipped: family=%s competition=%s reason=%s",
                stat_family,
                competition_code,
                exc,
            )
            continue
        secondary_models[stat_family] = model

        # Ver docstring de `has_real_count_data`: un partido sin esta
        # estadistica registrada se excluye de la metrica de validacion,
        # nunca se trata como si tuviera 0 (eso corrompia brier/log_loss
        # con etiquetas inventadas para partidos sin dato real).
        eval_rows = eval_finished[has_real_count_data(eval_finished, stat_family)]
        if len(eval_rows) > 0:
            probs = model.predict_market_probabilities(eval_rows)
            for market_key, p in probs.items():
                y_true = label_for_secondary_market(eval_rows, market_key).to_numpy()
                metrics["markets"][market_key] = {
                    "poisson_total": {
                        "brier_score": brier_score(y_true, p),
                        "log_loss": log_loss_score(y_true, p),
                    }
                }

            for market_key, probabilities in probs.items():
                labels = label_for_secondary_market(eval_rows, market_key).to_numpy(dtype=bool)
                if len(labels) < MIN_CALIBRATION_MATCHES or len(set(labels)) < 2:
                    corner_calibration[market_key] = {
                        "accepted": False, "reason": "insufficient_labels", "n_holdout": len(labels)
                    }
                    continue
                calibrator, report = _fit_validated_isotonic_calibrator(
                    probabilities, labels, eval_rows["date"].to_numpy()
                )
                corner_calibration[market_key] = report
                if calibrator is not None:
                    calibrators[market_key] = calibrator
                corner_calibration_choice[market_key] = "isotonic" if calibrator is not None else "none"
            from backend.app.models.calibration.corners import validate_corner_calibration
            from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS

            over_markets = [market for market in probs if SECONDARY_MARKET_DEFINITIONS[market].kind == "over"]
            if over_markets:
                corner_survival, corner_survival_report = validate_corner_calibration(
                    np.column_stack([probs[market] for market in over_markets]),
                    np.column_stack([label_for_secondary_market(eval_rows, market) for market in over_markets]),
                    eval_rows["date"].to_numpy(),
                )
                metrics["corner_survival_validation"] = corner_survival_report
                # Auditoria v2.8.2: `corner_survival` (una UNICA correccion
                # monotona compartida por las 4 lineas de corners) se
                # entrenaba y se guardaba desde siempre, pero nunca se
                # aplicaba en el pipeline real de predicciones -- solo en
                # la vista previa de candidatos de Champions League. Antes
                # de conectarla, se compara de verdad contra la alternativa
                # YA validada (el calibrador isotonico por mercado, cada
                # uno con su propio holdout fuera de muestra): se usa SOLO
                # si demuestra una mejora de log loss relativa mayor que la
                # del mejor calibrador isotonico individual de esas mismas
                # lineas. "Mayor mejora relativa" (ratio despues/antes), no
                # el valor absoluto, porque ambos metodos se miden en
                # bloques temporales de holdout distintos -- nunca se
                # asume que conectar la calibracion mejora nada sin esta
                # comparacion. Si gana, sustituye al isotonico para esas
                # lineas (nunca se aplican ambas: se retira el mercado de
                # `calibrators` para que no se pueda calibrar dos veces).
                if _corner_survival_beats_isotonic(
                    corner_survival, corner_survival_report, over_markets, corner_calibration
                ):
                    for market_key in over_markets:
                        corner_calibration_choice[market_key] = "corner_survival"
                        calibrators.pop(market_key, None)
    metrics["corners_calibration"] = corner_calibration
    metrics["corners_calibration_choice"] = corner_calibration_choice

    base_ids = train_table["match_id"].astype(int).tolist()
    previous_ids = previous_manifest.get("base_fit_match_ids")
    if previous_ids is None and previous and previous.artifact_path:
        try:
            previous_ids = load_model_artifact(previous).get("training_manifest", {}).get("base_fit_match_ids")
        except OSError:
            pass
    previous_set = set(previous_ids or ())
    learning_manifest = {
        "partition": partition, "dataset_sha256": dataset_hash,
        "closed_round_result_ids": sorted(closed_result_ids or ()),
        "observed_at": observed_at.isoformat() + "Z",
        "latest_observed_kickoff": frozen["date"].max().isoformat() + "Z",
        "base_fit_match_ids": base_ids,
        "new_base_fit_match_ids": [i for i in base_ids if i not in previous_set]
        if previous_ids is not None else None,
        "historical_availability_certified": False,
        "promotion_status": "awaiting_certified_prospective_evidence",
    }
    version = dt.datetime.utcnow().isoformat()
    artifact_dir = settings.model_artifacts_path / competition_code
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{version.replace(':', '-')}.joblib"
    artifact_payload = {
        "learning_manifest": learning_manifest,
        "training_input_records_json": records,
        "baseline": baseline,
        "dixon_coles": dixon_coles,
        "ml_classifier": selected_ml,
        "ensemble_weights": ensemble_weights,
        "calibrators": calibrators,
        "coherent_calibration": {"temperature": result_temperature},
        "training_manifest": {
            "base_fit_match_ids": train_table["match_id"].astype(int).tolist(),
            "holdout_match_ids": eval_finished["match_id"].astype(int).tolist(),
            "ensemble_selection_match_ids": eval_finished.loc[selection_mask, "match_id"].astype(int).tolist(),
            "calibration_pool_match_ids": eval_finished.loc[calibration_mask, "match_id"].astype(int).tolist(),
        },
    }
    if "corners" in secondary_models:
        artifact_payload["corners_model"] = secondary_models["corners"]
        artifact_payload["corner_survival"] = corner_survival
        artifact_payload["corner_calibration_choice"] = corner_calibration_choice
    joblib.dump(artifact_payload, artifact_path)

    model_version = ModelVersion(
        name="dixon_coles_plus_logistic_ensemble",
        version=version,
        market_family="goals",
        training_start=train_table["date"].min().date() if len(train_table) else None,
        training_end=max(train_table["date"].max(), eval_table["date"].max()).date(),
        features=feature_columns(table),
        hyperparameters={
            "learning_manifest": learning_manifest,
            "holdout_season": holdout_season,
            "base_fit_end": train_table["date"].max().isoformat(),
            "selection_end": eval_table["date"].max().isoformat(),
        },
        metrics=metrics,
        dataset_version=f"{'learning:' if rolling else ''}{competition_code}:{len(finished)}_matches",
        calibration_method="isotonic" if calibrators else None,
        artifact_path=str(artifact_path),
        is_active=False,
    )
    db.add(model_version)
    db.commit()
    db.refresh(model_version)

    logger.info(
        "model_service.trained",
        extra={
            "competition": competition_code,
            "model_version_id": model_version.id,
            "holdout_season": holdout_season,
        },
    )
    training_result = {
        "competition": competition_code,
        "status": "trained",
        "model_version_id": model_version.id,
        "n_matches": len(finished),
        "holdout_season": holdout_season,
        "metrics": metrics,
    }
    log_training_run(competition_code, training_result, settings)
    return training_result


def load_latest_model_version(db: Session, market_family: str = "goals") -> ModelVersion | None:
    return (
        db.query(ModelVersion)
        .filter_by(market_family=market_family)
        .order_by(ModelVersion.created_at.desc())
        .first()
    )


def load_model_artifact(model_version: ModelVersion) -> dict:
    return joblib.load(model_version.artifact_path)
