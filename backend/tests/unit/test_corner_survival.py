from types import SimpleNamespace

import numpy as np
from scipy.stats import poisson

from backend.app.models.calibration.corners import correct_probability, validate_corner_calibration
from backend.app.prediction.predictor import apply_corner_survival_correction
from backend.app.prediction.secondary_markets import TotalCountPoissonModel


def test_poisson_thresholds_and_shared_correction():
    model = TotalCountPoissonModel("corners")
    model.predict_lambda = lambda _: np.array([3., 10., 50.])
    raw = model.predict_market_probabilities(None)
    keys = list(raw)
    assert np.allclose(raw[keys[0]][1], poisson.sf(8, 10))
    params = {"slope": .65, "intercept": -.32}
    over_85, over_95, over_105 = [correct_probability(raw[key], params) for key in keys[:3]]
    assert np.all(over_85 >= over_95)
    assert np.all(over_95 >= over_105)
    assert np.allclose(correct_probability(raw[keys[3]], params, under=True), 1 - over_95)
    assert np.all((over_85 > 0) & (over_85 < 1))


def test_validation_rejects_small_samples_and_future_regression():
    p = np.tile(np.r_[np.full(20, .87), np.full(20, .93)], 3).reshape(-1, 1)
    y = np.tile(np.r_[np.arange(20) < 8, np.arange(20) < 14], 3).reshape(-1, 1)
    assert validate_corner_calibration(p[:20], y[:20], np.arange(20))[0] is None
    params, report = validate_corner_calibration(p, y, np.arange(120))
    assert params is not None
    assert report["fixture_counts"] == [40, 40, 40]
    y[-40:] = (np.arange(40).reshape(-1, 1) >= 5)
    params, report = validate_corner_calibration(p, y, np.arange(120))
    assert params is None
    assert report["reason"] == "holdout_rejected"


# `apply_corner_survival_correction` (predictor.py, auditoria v2.8.2):
# conecta `corner_survival` al pipeline REAL (antes solo se usaba en la
# vista previa de candidatos de Champions League). Solo actua sobre los
# mercados cuya eleccion guardada en el artefacto sea "corner_survival"
# (decidida en model_service.py comparando de verdad contra el isotonico
# -- ver test_model_service_calibration.py); nunca inventa una
# probabilidad cuando no hay parametros validados.


def _row(market, model_probability, calibrated_probability=None):
    return SimpleNamespace(
        match_id=1, market=market, model_probability=model_probability,
        calibrated_probability=calibrated_probability, calibration_source=None,
    )


def test_apply_corner_survival_correction_replaces_only_chosen_markets():
    params = {"slope": 0.8, "intercept": -0.1, "version": "corner-survival-logistic-v1"}
    artifact = {
        "corner_survival": params,
        "corner_calibration_choice": {
            "corners_over_8_5": "corner_survival",
            "corners_over_9_5": "isotonic",
            "corners_under_9_5": "isotonic",
        },
    }
    over_85 = _row("corners_over_8_5", 0.62, calibrated_probability=0.55)  # isotonic previo, se descarta
    over_95 = _row("corners_over_9_5", 0.49, calibrated_probability=0.52)  # se queda igual (eligio isotonic)
    under_95 = _row("corners_under_9_5", 0.51, calibrated_probability=0.48)
    outputs = [over_85, over_95, under_95]

    apply_corner_survival_correction(outputs, artifact)

    assert over_85.calibrated_probability == correct_probability(0.62, params, under=False)
    assert over_85.calibration_source == "corner-survival-logistic-v1"
    # Nunca se combina con el isotonico previo: el valor nuevo viene de
    # `model_probability` crudo, no de la `calibrated_probability` vieja.
    assert over_85.calibrated_probability != 0.55

    # Mercados cuya eleccion NO sea "corner_survival" quedan intactos.
    assert over_95.calibrated_probability == 0.52
    assert under_95.calibrated_probability == 0.48


def test_apply_corner_survival_correction_is_a_noop_without_validated_params():
    # Artefacto sin `corner_survival` (nunca se entreno o nunca se valido)
    # -- nunca se inventa una probabilidad calibrada.
    outputs = [_row("corners_over_9_5", 0.60, calibrated_probability=0.58)]
    apply_corner_survival_correction(outputs, {"corner_calibration_choice": {"corners_over_9_5": "corner_survival"}})
    assert outputs[0].calibrated_probability == 0.58


def test_apply_corner_survival_correction_is_a_noop_on_artifacts_without_the_choice_map():
    # Compatibilidad con artefactos antiguos (entrenados antes de esta
    # fase): sin `corner_calibration_choice`, nunca se aplica nada nuevo.
    params = {"slope": 0.8, "intercept": -0.1, "version": "corner-survival-logistic-v1"}
    outputs = [_row("corners_over_9_5", 0.60, calibrated_probability=0.58)]
    apply_corner_survival_correction(outputs, {"corner_survival": params})
    assert outputs[0].calibrated_probability == 0.58


def test_hoy_excludes_stale_and_unverified_prices_and_ranks_one_per_match():
    from datetime import datetime, timedelta
    from types import SimpleNamespace as Record

    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP, rank_champions_preview

    now = datetime(2026, 11, 23, 12)
    markets = ("corners_over_8_5", "corners_over_9_5", "corners_over_10_5")
    match = Record(id=1, kickoff_utc=now + timedelta(days=1), status="scheduled", odds=[])

    def inputs(market, p, price, age, market_p):
        key, line, side = ALL_MARKET_TO_ODDS_LOOKUP[market]
        fields = dict(match_id=1, market=key, line=line, selection=side, bookmaker="Pinnacle",
                      snapshot_type="pre_match", price=price,
                      # `age` = minutes since the price was last downloaded (currency follows confirmation).
                      provider_updated_at=now - timedelta(minutes=age + 5), fetched_at=now - timedelta(minutes=age),
                      recorded_at=now - timedelta(minutes=age))
        match.odds.append(Record(**fields, history_quote_id=1, archived_quote=Record(**fields)))
        complementary = {**fields, "selection": "under", "price": price * market_p / (1 - market_p)}
        match.odds.append(Record(**complementary, history_quote_id=2, archived_quote=Record(**complementary)))
        return Record(match_id=1, market=market, model_probability=p, calibrated_probability=None,
                      uncertainty_low=None, uncertainty_high=None, market_probability=market_p,
                      market_probability_source="consensus_no_vig", data_quality=.8, calibration_source=None)

    first = inputs(markets[0], .86, 1.60, 10, .58)
    second = inputs(markets[1], .75, 1.55, 10, .69)
    third = inputs(markets[2], .70, 1.80, 361, .65)
    picks, diagnostics = rank_champions_preview([match], [first, second, third], 1, now, {})
    assert picks[1]["market"] == markets[1]
    assert picks[1]["probability_source"] == "original"
    assert "ventaja_extrema_frente_al_mercado" in diagnostics[1, markets[0]]["reasons"]
    assert "cuota_desactualizada" in diagnostics[1, markets[2]]["reasons"]
    match.odds[2].archived_quote = None
    assert rank_champions_preview([match], [first, second, third], 1, now, {})[0] == {}
