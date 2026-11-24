from datetime import datetime, timedelta
from types import SimpleNamespace as Record

import httpx

from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider
from backend.app.services import today_preview


def forecast(market, p, calibrated=None):
    return Record(match_id=1, market=market, model_probability=p,
                  calibrated_probability=calibrated, uncertainty_low=None,
                  uncertainty_high=None, data_quality=.8)


def price(market, now, value, age=20):
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP
    category, line, side = ALL_MARKET_TO_ODDS_LOOKUP[market]
    fields = dict(match_id=1, market=category, line=line, selection=side, snapshot_type="pre_match",
                  bookmaker="Pinnacle", price=value, provider_updated_at=now - timedelta(minutes=age),
                  fetched_at=now, recorded_at=now)
    return Record(**fields, archived_quote=Record(**fields))


def test_informative_quote_boundaries_and_negative_ev():
    now = datetime(2026, 11, 23)
    row = forecast("home_win", .60)
    for value in (1.30, 1.90):
        pick = today_preview.informative_pick([row], "laliga", {}, 17, now,
            odds=[price(row.market, now, value)], diagnostics={})
        assert pick["odds"] == value
        assert pick["probability_source"] == "original"
        assert pick["quote_status"] == "current"
        assert pick["ev"] == .60 * value - 1
    for value in (1.29, 1.91):
        assert today_preview.informative_pick([row], "laliga", {}, 17, now,
            odds=[price(row.market, now, value)], diagnostics={}) is None
    row.model_probability = .599
    assert today_preview.informative_pick([row], "laliga", {}, 17, now,
        odds=[price(row.market, now, 1.50)], diagnostics={}) is None


def test_informative_ranking_prefers_supported_calibration_and_marks_stale():
    now = datetime(2026, 11, 23)
    rows = [forecast("corners_over_8_5", .97), forecast("over_2_5", .60, .62)]
    evidence = {("champions_league", "over_2_5"): Record(
        source_level="competition_market", n_observations=50, value=.60)}
    odds = [price(row.market, now, 1.5, 400) for row in rows]
    pick = today_preview.informative_pick(rows, "champions_league", evidence, 17, now,
                                          odds=odds, diagnostics={})
    assert pick["market"] == "over_2_5"
    assert pick["probability_source"] == "calibrated"
    assert pick["quote_status"] == "stale"
    assert pick["ev"] is None


def test_categories_and_filter_diagnostics_are_independent(monkeypatch, db_session):
    now = datetime(2026, 11, 23)
    match = Record(id=1, status="scheduled", odds=[], kickoff_utc=now + timedelta(days=1),
                   competition=Record(code="champions_league"))
    monkeypatch.setattr(today_preview, "market_track_records_with_provenance", lambda *a, **k: {})
    monkeypatch.setattr(today_preview, "score_champions_candidate", lambda *a, **k: (None, [], {}))
    no_model = today_preview.build_today_previews(db_session, [match], now)[1]
    assert no_model["forecast_unavailable_reason"] == "sin_modelo_valido"
    assert no_model["opportunity"] is None

    monkeypatch.setattr(today_preview, "score_champions_candidate", lambda *a, **k: (
        Record(id=17), [forecast("home_win", .60)], {},
    ))
    preview = today_preview.build_today_previews(db_session, [match], now)[1]
    assert preview["forecast"] is None
    assert preview["forecast_unavailable_reason"] == "sin_candidato_informativo"
    assert preview["opportunity"] is None
    assert preview["counts"]["evaluated"] == 1
    assert preview["counts"]["eligible"] == 0
    assert set(preview["markets"]["home_win"]["reasons"]) == {"sin_mercado", "probabilidad_inferior_al_65"}


def test_odds_refresh_deduplicates_and_preserves_partial_success(monkeypatch):
    def fixture(id, hour, status="NS"):
        return {"fixture": {"id": id, "date": f"2026-11-24T{hour}:00:00+00:00", "status": {"short": status}},
                "teams": {"home": {"name": "A"}, "away": {"name": "B"}}}

    fixtures = [fixture(3, 21), fixture(2, 20), fixture(1, 18), fixture(1, 18), fixture(4, 17, "FT")]
    calls = []

    def fetch(path, params):
        if path == "/fixtures":
            return fixtures
        id = params["fixture"]
        calls.append(id)
        if id == 2:
            raise httpx.ReadTimeout("exhausted retries")
        return [{"fixture": {"id": id}, "update": "2026-11-23T12:00:00+00:00",
                 "bookmakers": [{"name": "Pinnacle", "bets": [{"name": "Match Winner",
                                "values": [{"value": "Home", "odd": "1.5"}]}]}]}]

    provider = ApiFootballOddsProvider()
    monkeypatch.setattr(provider, "_get", fetch)
    monkeypatch.setattr("backend.app.ingestion.api_football.odds_provider.time.sleep", lambda _: None)
    day = datetime(2026, 11, 24).date()
    result = provider.fetch_odds_for_window("champions_league", 2026, day, day)
    assert calls == [1, 2, 3]
    assert len(result) == 2
    assert result[0].odds[0].provider_updated_at == datetime(2026, 11, 23, 12)
    assert provider.odds_fetch_failures == ["2: ReadTimeout"]


def test_unknown_origin_requires_archived_provenance_and_has_no_ev():
    now = datetime(2026, 11, 23)
    row = forecast("home_win", .75)
    quote = price(row.market, now, 1.50)
    quote.provider_updated_at = quote.archived_quote.provider_updated_at = None
    pick = today_preview.informative_pick([row], "laliga", {}, 17, now,
                                          odds=[quote], diagnostics={})
    assert pick["quote_status"] == "unverified"
    assert pick["ev"] is None
    quote.archived_quote = None
    assert today_preview.informative_pick([row], "laliga", {}, 17, now,
                                         odds=[quote], diagnostics={}) is None
