from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.app.config.settings import Settings
from backend.app.prediction.opportunities import assess_opportunity, select_ranked
from scripts.compare_publication_rankings import eligible_at_capture

NOW = datetime(2026, 11, 20, 10)
MARKET = ("match_result", None, "home")


def prediction(**changes):
    values = dict(
        market_probability=None, market_probability_source=None,
        id=1, match_id=10, match=SimpleNamespace(status="scheduled", kickoff_utc=NOW + timedelta(hours=2)),
        created_at=NOW - timedelta(minutes=30), model_probability=.76,
        calibrated_probability=.76, uncertainty_low=.73, uncertainty_high=.79, data_quality=.8,
    )
    return SimpleNamespace(**(values | changes))


def quote(**changes):
    values = dict(
        match_id=10, market=MARKET[0], line=None, selection="home", snapshot_type="pre_match",
        price=1.45, history_quote_id=1, bookmaker="A", provider_updated_at=NOW - timedelta(minutes=20),
        fetched_at=NOW - timedelta(minutes=10), recorded_at=NOW - timedelta(minutes=9),
    )
    fields = values | changes
    archive = SimpleNamespace(**fields) if fields["history_quote_id"] else None
    return SimpleNamespace(**(fields | {"archived_quote": archive}))


def assess(odds=None, p=None, reliability=None, flags=()):
    return assess_opportunity(
        p or prediction(), [quote()] if odds is None else odds, MARKET, Settings(min_opportunity_odds=1.35, max_opportunity_odds=1.90), NOW, reliability, flags,
    )


@pytest.mark.parametrize("price", [1.34, 1.91])
def test_price_outside_band(price):
    assert "cuota_fuera_de_banda" in assess([quote(price=price)]).reasons


@pytest.mark.parametrize("probability", [76, float("nan"), float("inf"), -0.1])
def test_probability_scale_and_nonfinite_values(probability):
    item = assess(p=prediction(calibrated_probability=probability))
    assert "probabilidad_invalida" in item.reasons
    assert item.ev is None


def test_ev_is_computed_with_the_exact_price_and_requires_positive_value():
    item = assess()
    assert not item.reasons
    assert item.break_even == pytest.approx(1/1.45)
    assert item.ev == pytest.approx(.76*1.45-1)
    below = assess(p=prediction(calibrated_probability=.68))
    assert below.ev == pytest.approx(-.014)
    assert "ev_estimado_no_positivo" in below.reasons


def test_calibration_is_not_inferred_from_raw_probability():
    item = assess(p=prediction(calibrated_probability=None))
    assert "calibracion_no_disponible" in item.reasons
    assert item.ev is None


def test_wide_interval_and_missing_history_reduce_confidence_without_discarding():
    item = assess(p=prediction(uncertainty_low=.4, uncertainty_high=.9))
    assert not item.reasons
    assert not item.high_confidence
    assert item.conservative_ev < 0
    assert "incertidumbre_elevada_o_no_disponible" in item.confidence_reasons
    assert "fiabilidad_historica_insuficiente" in item.confidence_reasons
    assert assess(reliability=SimpleNamespace(value=.8, n_observations=30, source_level="competition_market")).high_confidence


def test_quote_freshness_uses_origin_and_rejects_inconsistent_timestamps():
    item = assess([quote(provider_updated_at=NOW - timedelta(hours=7))])
    assert "cuota_desactualizada" in item.reasons
    invalid = assess([quote(provider_updated_at=NOW)])
    assert "origen_cuota_no_verificado" in invalid.reasons


@pytest.mark.parametrize("change", [
    {"match_id": 11}, {"market": "over_under_goals"}, {"line": 2.5},
    {"selection": "away"}, {"snapshot_type": "closing"},
])
def test_other_fixture_market_line_side_and_snapshot_cannot_supply_price(change):
    assert "sin_mercado" in assess([quote(**change)]).reasons


def test_picks_best_verified_bookmaker_in_band():
    item = assess([
        quote(), quote(price=1.65, bookmaker="B"),
        quote(price=1.88, bookmaker="C", history_quote_id=None),
        quote(price=1.94, bookmaker="D"),
    ])
    assert item.quote.bookmaker == "B"
    assert item.ev == pytest.approx(.76 * 1.65 - 1)


def test_started_fixture_and_future_prediction_are_rejected():
    assert assess(p=prediction(match=SimpleNamespace(status="scheduled", kickoff_utc=NOW))).reasons
    assert assess(p=prediction(created_at=NOW + timedelta(minutes=1))).reasons


def test_ranking_collapses_correlated_fixture_and_can_remain_empty():
    favourite = prediction()
    correlated = prediction(id=2, calibrated_probability=.72)
    favourite_item, correlated_item = assess(p=favourite), assess(p=correlated)
    assert select_ranked([(correlated, correlated_item), (favourite, favourite_item)], 2) == [
        (favourite, favourite_item),
    ]
    assert select_ranked([], 10) == []


def test_temporal_replay_rejects_prices_fetched_after_the_publication():
    row = dict(reasons=[], calibrated=.76, opportunity_score=.6, quote_history_id=1, odds=1.45,
               odds_origin_at=(NOW - timedelta(minutes=1)).isoformat(), odds_fetched_at=NOW.isoformat(),
               odds_recorded_at=NOW.isoformat())
    assert eligible_at_capture(row, NOW)
    assert not eligible_at_capture(row, NOW - timedelta(seconds=1))
    assert not eligible_at_capture(row | {"quote_history_id": None}, NOW)


@pytest.mark.parametrize("probability", [0, 1])
def test_saturated_calibrator_requires_additional_evidence(probability):
    assert "calibracion_en_extremo" in assess(p=prediction(calibrated_probability=probability)).reasons


def test_edited_active_quote_cannot_borrow_its_archive_id():
    current = quote()
    current.price = 1.90
    assert "origen_cuota_no_verificado" in assess([current]).reasons


def test_probability_has_priority_when_value_is_comparable():
    favourite = prediction()
    longshot = prediction(id=2, match_id=11, model_probability=.57, calibrated_probability=.57,
                          uncertainty_low=.54, uncertainty_high=.60)
    a = assess([quote(price=1.45)], p=favourite)
    b = assess([quote(match_id=11, price=1.90)], p=longshot)
    assert select_ranked([(longshot, b), (favourite, a)], 2)[0][0] is favourite


def test_large_calibration_jump_requires_market_and_league_evidence():
    p = prediction(model_probability=.78, calibrated_probability=.97)
    assert "ajuste_calibracion_sin_respaldo" in assess(p=p).reasons
    backed = SimpleNamespace(value=.8, n_observations=30, source_level="competition_market")
    assert not assess(p=p, reliability=backed).reasons


def test_explicit_suspension_expires_the_active_market_even_with_no_prices():
    from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider

    suspended = set()
    odds, _ = ApiFootballOddsProvider._parse_odds_response(
        [{"bookmakers": [{"name": "Book", "bets": [
            {"name": "Match Winner", "suspended": True, "values": [{"value": "Home", "odd": "1.55"}]},
        ]}]}],
        suspended_markets=suspended,
    )
    assert odds == []
    assert suspended == {"match_result"}


@pytest.mark.parametrize("market,line,side", [
    ("over_under_goals", 2.5, "over"),
    ("btts", None, "yes"),
    ("corners_total", 9.5, "under"),
    ("double_chance_1x", None, "yes"),
])
def test_high_confidence_accepts_other_markets_when_they_meet_the_same_evidence(market, line, side):
    evidence = SimpleNamespace(value=.8, n_observations=30, source_level="competition_market")
    item = assess_opportunity(
        prediction(), [quote(market=market, line=line, selection=side)],
        (market, line, side), Settings(), NOW, evidence, (),
    )
    assert item.high_confidence


def test_same_fixture_prefers_higher_probability_after_value_and_price_checks():
    favourite = prediction(id=1, model_probability=.76, calibrated_probability=.76)
    lower_probability = prediction(id=2, model_probability=.63, calibrated_probability=.63,
                                   uncertainty_low=.55, uncertainty_high=.69)
    safe = assess([quote(price=1.35)], p=favourite)
    larger_ev = assess([quote(price=1.90)], p=lower_probability)
    assert not safe.reasons and not larger_ev.reasons
    assert larger_ev.ev > safe.ev and larger_ev.score > safe.score
    assert select_ranked([(lower_probability, larger_ev), (favourite, safe)], 5) == [(favourite, safe)]

def test_large_market_gap_blocks_a_calibrated_pick_even_with_a_price_in_range():
    p = prediction(model_probability=.83, calibrated_probability=.83, market_probability=.56,
                   market_probability_source="consensus_no_vig")
    assert "ventaja_extrema_frente_al_mercado" in assess([quote(price=1.80)], p=p).reasons


def test_moderate_market_gap_retains_the_normal_qualification_rules():
    p = prediction(model_probability=.83, calibrated_probability=.83, market_probability=.65,
                   market_probability_source="consensus_raw")
    assert not assess([quote(price=1.55)], p=p).reasons
