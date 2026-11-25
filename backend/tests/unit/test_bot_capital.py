import copy
import datetime as dt
from types import SimpleNamespace

import pytest

from backend.app.services import bot_capital
from backend.app.services.bot_bankroll import settle_ticket
from backend.app.services.bot_capital import (
    MIN_STAKE_CENTS, PROFILES, active_profile, assess_double, choose_portfolio, drawdown_multiplier,
    effective_probability, joint_probability, statistical_confidence,
)

MODERATE = PROFILES["moderado"]


def _leg(match_id, price, probability, confidence=.8, competition="afc_champions_league", market="over_1_5",
         kickoff="2026-11-24T12:15:00Z", gates=(), calibration_error=.02):
    low = probability - .04
    return dict(
        match_id=match_id, match=f"Partido {match_id}", competition=competition, kickoff=kickoff,
        market=market, line=None, selection=None, price=price, probability=probability,
        effective_probability=effective_probability(probability, low, price, confidence),
        confidence=confidence, confidence_gates=list(gates), calibration_error=calibration_error,
    )


def _choose(singles, doubles=(), equity=30_000, available=None, reserved=0, peak=None, profile=MODERATE):
    available = equity - reserved if available is None else available
    return choose_portfolio(list(singles), list(doubles), profile, equity=equity, available=available,
                            reserved=reserved, peak=peak or equity)


# --- Conservation of the existing bank and history -------------------------------------------------

def _historical_bank(db):
    """The four known tickets, stored exactly with the payload shapes already in PostgreSQL."""
    from backend.app.db.models.core import Competition, Season, Team
    from backend.app.db.models.matches import Match
    from backend.app.db.models.publication import BotManualTicket, BotTrialCut, BotTrialDecision

    league = Competition(code="hist_bank", name="Hist", country="X")
    db.add(league)
    db.flush()
    season = Season(competition_id=league.id, label="hist")
    db.add(season)
    teams = [Team(canonical_name=f"Hist team {i}") for i in range(8)]
    db.add_all(teams)
    db.flush()
    scores = [(2, 0), (1, 1), (2, 1), (1, 0)]  # 1X ok, 1X ok, X2 fails, home scores
    matches = []
    for i, (home, away) in enumerate(scores):
        match = Match(provider="test", provider_id=f"hist-{i}", competition_id=league.id, season_id=season.id,
                      kickoff_utc=dt.datetime(2026, 11, 23, 12) + dt.timedelta(hours=i * 6), status="finished",
                      home_team_id=teams[2 * i].id, away_team_id=teams[2 * i + 1].id, home_goals=home, away_goals=away)
        db.add(match)
        matches.append(match)
    db.flush()
    leg = lambda m, market, price: {"match_id": m.id, "match": f"M{m.id}", "market": market, "price": price}
    auto = {"day": "2026-11-23", "captured_at": "2026-11-23T09:38:47.017839Z", "policy_version": "forward-v3",
            "available_at_cut_cents": 30000, "odds_band": [1.3, 1.8], "fixtures": [], "tickets": [
                {"stake_cents": 7500, "legs": [leg(matches[0], "double_chance_1x", 1.40)]},
                {"stake_cents": 7500, "legs": [leg(matches[1], "double_chance_1x", 1.42)]},
                {"stake_cents": 15000, "legs": [leg(matches[2], "double_chance_x2", 1.32)]},
            ]}
    cut = {"day": "2026-11-24", "captured_at": "2026-11-24T07:42:21Z", "policy_version": "forward-v4-continuous",
           "available_at_cut_cents": 21150, "odds_band": [1.3, 1.8], "fixtures": [{"match_id": matches[3].id}], "tickets": []}
    manual = {"day": "2026-11-24", "captured_at": "2026-11-24T07:54:12Z", "policy_version": "manual-in-play-historical",
              "odds_band": [1.4, 1.4], "fixtures": [], "tickets": [
                  {"stake_cents": 21150, "legs": [leg(matches[3], "home_team_over_0_5", 1.40)]}]}
    db.add_all([
        BotTrialDecision(day=dt.date(2026, 11, 23), payload=auto),
        BotTrialCut(match_id=matches[3].id, captured_at=dt.datetime(2026, 11, 24, 7, 42), payload=cut),
        BotManualTicket(match_id=matches[3].id, captured_at=dt.datetime(2026, 11, 24, 7, 54), payload=manual),
    ])
    db.flush()
    return matches, (auto, cut, manual)


def test_existing_balance_and_history_are_preserved(db_session):
    from backend.app.db.models.publication import BotManualTicket, BotTrialCut, BotTrialDecision
    from backend.app.services.bot_bankroll import bot_report

    _, stored = _historical_bank(db_session)
    snapshot = copy.deepcopy(stored)
    report = bot_report(db_session, trial=True)
    assert report["initial_cents"] == 30_000
    assert report["balance_cents"] == 29_610
    assert report["reserved_cents"] == 0
    tickets = [t for day in report["days"] for t in day["tickets"]]
    assert sorted((t["stake_cents"], t["state"], t["returned_cents"]) for t in tickets) == [
        (7500, "won", 10500), (7500, "won", 10650), (15000, "lost", 0), (21150, "won", 29610)]
    # Reading never rewrites the stored, immutable payloads.
    payloads = [row.payload for table in (BotTrialDecision, BotTrialCut, BotManualTicket) for row in db_session.query(table)]
    assert sorted(payloads, key=lambda p: p["captured_at"]) == sorted(snapshot, key=lambda p: p["captured_at"])


def test_legacy_payloads_need_no_migration_and_split_manual_from_automatic(db_session):
    from backend.app.services.bot_bankroll import bot_report

    _historical_bank(db_session)
    summary = bot_report(db_session, trial=True)["summary"]
    assert summary["automatic"]["profit_cents"] == 10_500 + 10_650 - 30_000
    assert summary["manual"]["profit_cents"] == 29_610 - 21_150
    assert summary["by_kind"]["auto_single"]["tickets"] == 3
    assert summary["by_kind"]["manual_live"]["tickets"] == 1
    assert summary["by_kind"]["auto_double"]["tickets"] == 0
    # Accounting identity: available + committed = opening bank + settled profit.
    assert summary["available_cents"] + summary["committed_cents"] == 30_000 + summary["net_profit_cents"]
    assert summary["max_drawdown"] == pytest.approx(1 - 21_150 / 30_000, abs=1e-4)
    assert summary["automatic"]["hit_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_bot_tables_are_unchanged_by_the_new_policy():
    from backend.app.db.database import Base

    for name in ("bot_decisions", "bot_trial_decisions", "bot_trial_cuts", "bot_manual_tickets"):
        assert name in Base.metadata.tables
    assert {c.name for c in Base.metadata.tables["bot_trial_cuts"].columns} == {"match_id", "captured_at", "payload"}


# --- Probability versus statistical confidence ------------------------------------------------------

def test_confidence_is_not_the_probability():
    def prediction(p, low, high, market_p):
        return SimpleNamespace(uncertainty_low=low, uncertainty_high=high, model_probability=p,
                               market_probability=market_p, market_probability_source="consensus")

    item = lambda p, age=20: SimpleNamespace(probability=p, probability_source="calibrated", age_minutes=age,
                                             max_age_minutes=360, quote=None)
    strong = SimpleNamespace(n_observations=250, source_level="competition_market", value=.8)
    weak = SimpleNamespace(n_observations=12, source_level="market_global", value=.4)
    high_p = statistical_confidence(prediction(.90, .62, .98, .66), item(.90), weak)
    solid = statistical_confidence(prediction(.75, .71, .79, .73), item(.75), strong)
    assert solid["score"] > high_p["score"]
    stale = statistical_confidence(prediction(.75, .71, .79, .73), item(.75, age=120), strong)
    assert stale["score"] == 0 and "cuota_desactualizada_para_stake" in stale["gates"]
    # An old origin re-confirmed by a recent fetch can size a position, with lower freshness.
    now = dt.datetime(2026, 11, 24, 10, 20)
    reconfirmed = SimpleNamespace(probability=.75, probability_source="calibrated", age_minutes=120, max_age_minutes=360,
                                  quote=SimpleNamespace(fetched_at=now - dt.timedelta(minutes=10)))
    fresh = statistical_confidence(prediction(.75, .71, .79, .73), reconfirmed, strong, now)
    assert not fresh["gates"] and 0 < fresh["components"]["frescura"] < 1
    unconfirmed = SimpleNamespace(**dict(vars(reconfirmed), quote=SimpleNamespace(fetched_at=now - dt.timedelta(minutes=95))))
    assert "cuota_desactualizada_para_stake" in statistical_confidence(prediction(.75, .71, .79, .73), unconfirmed, strong, now)["gates"]


def test_effective_probability_shrinks_to_break_even():
    assert effective_probability(.80, .76, 1.40, 0) == pytest.approx(1 / 1.40)
    assert effective_probability(.80, .76, 1.40, 1) == pytest.approx(.78)
    assert effective_probability(None, .7, 1.4, 1) is None


# --- Dynamic stake and limits -----------------------------------------------------------------------

def test_dynamic_stake_grows_with_evidence_not_with_probability_alone():
    weak = _choose([_leg(1, 1.40, .90, confidence=.25)])
    strong = _choose([_leg(1, 1.40, .80, confidence=.90)])
    weak_stake = sum(t["stake_cents"] for t in weak["tickets"])
    strong_stake = sum(t["stake_cents"] for t in strong["tickets"])
    assert strong_stake > weak_stake
    assert strong_stake <= MODERATE.max_single_cents


def test_never_all_in_even_with_extreme_edge():
    for profile in PROFILES.values():
        decision = _choose([_leg(1, 1.90, .97, confidence=1)], profile=profile)
        stake = sum(t["stake_cents"] for t in decision["tickets"])
        assert 0 < stake <= min(profile.max_single_cents, profile.max_single_fraction * 30_000)
        assert stake <= (1 - profile.min_reserve_fraction) * 30_000


def test_aggressive_profile_can_reach_large_stakes_only_with_authorisation():
    assert active_profile("agresivo_experimental", False)[0].name == "moderado"
    profile, note = active_profile("agresivo_experimental", True)
    assert profile.name == "agresivo_experimental" and note is None
    decision = _choose([_leg(1, 1.90, .97, confidence=1)], profile=profile)
    assert 10_000 <= decision["tickets"][0]["stake_cents"] <= 15_000
    # 100 € is a ceiling reached with exceptional evidence, never a minimum.
    modest = _choose([_leg(1, 1.40, .80, confidence=.9)], profile=profile)
    assert 0 < modest["tickets"][0]["stake_cents"] < 10_000
    capped = _choose([_leg(1, 1.90, .97, confidence=1)])
    assert capped["tickets"][0]["stake_cents"] == 6_000
    assert "tope_por_posicion" in [a for a in capped["alternatives"] if a["chosen"]][0]["binding_limits"]


def test_insufficient_bank_abstains():
    decision = _choose([_leg(1, 1.40, .80)], equity=600)
    assert decision["tickets"] == []
    assert decision["chosen"] == "No apostar"


def test_abstention_without_edge_after_confidence():
    decision = _choose([_leg(1, 1.40, .73, confidence=.9), _leg(2, 1.50, .80, confidence=.0)])
    assert decision["tickets"] == [] and "Abstención" in decision["reason"]


def test_stale_quote_cannot_size_a_position():
    decision = _choose([_leg(1, 1.40, .85, gates=["cuota_desactualizada_para_stake"])])
    assert decision["tickets"] == []


def test_simultaneous_exposure_counts_pending_tickets():
    free = _choose([_leg(1, 1.50, .85, confidence=1)])
    busy = _choose([_leg(1, 1.50, .85, confidence=1)], reserved=11_000)
    assert sum(t["stake_cents"] for t in busy["tickets"]) <= MODERATE.max_exposure_fraction * 30_000 - 11_000
    assert sum(t["stake_cents"] for t in busy["tickets"]) < sum(t["stake_cents"] for t in free["tickets"])
    full = _choose([_leg(1, 1.50, .85, confidence=1)], reserved=12_000)
    assert full["tickets"] == []


def test_drawdown_reduces_and_never_increases_the_fraction():
    assert drawdown_multiplier(30_000, 30_000) == 1
    assert drawdown_multiplier(20_000, 30_000) < 1
    assert drawdown_multiplier(10_000, 30_000) == .25
    after_losses = _choose([_leg(1, 1.50, .85, confidence=1)], equity=24_000, peak=30_000)
    fresh = _choose([_leg(1, 1.50, .85, confidence=1)], equity=24_000, peak=24_000)
    assert after_losses["tickets"][0]["stake_cents"] < fresh["tickets"][0]["stake_cents"]


# --- Doubles ----------------------------------------------------------------------------------------

def test_joint_probability_and_dependence_bounds():
    assert joint_probability(.8, .9, 0) == pytest.approx(.72)
    assert joint_probability(.8, .9, .15) > .72 > joint_probability(.8, .9, -.15)
    assert joint_probability(.95, .95, -1) == pytest.approx(.90)  # Fréchet lower bound
    assert joint_probability(.3, .9, 1) <= .3


def test_double_requires_two_fixtures_and_robust_positive_ev():
    a, b = _leg(1, 1.22, .90, confidence=.9), _leg(2, 1.25, .88, confidence=.9, kickoff="2026-11-25T12:00:00Z")
    double = assess_double(a, b)
    assert double["accepted"] and double["synthetic_price"]
    assert double["price"] == pytest.approx(1.525)
    labels = [row["scenario"] for row in double["sensitivity"]]
    assert labels == ["independencia", "dependencia_positiva", "dependencia_negativa", "error_calibracion_adicional"]
    assert double["robust_ev"] == min(row["ev"] for row in double["sensitivity"][:3])
    assert assess_double(a, dict(a, market="btts"))["reasons"][0] == "mismo_partido"
    thin = assess_double(_leg(1, 1.15, .88, confidence=.5), _leg(2, 1.18, .86, confidence=.5))
    assert not thin["accepted"]
    cheap = assess_double(_leg(1, 1.05, .97), _leg(2, 1.08, .95))
    assert "cuota_combinada_fuera_de_rango" in cheap["reasons"]


def test_low_price_double_is_not_chosen_just_for_its_price():
    a, b = _leg(1, 1.20, .845, confidence=.5), _leg(2, 1.22, .83, confidence=.5)
    double = assess_double(a, b)
    decision = _choose([], [double] if double["accepted"] else [])
    assert all(t["kind"] != "auto_double" for t in decision["tickets"]) or double["robust_ev"] > 0


def test_shared_selection_is_one_exposure_with_joint_scenarios():
    a, b = _leg(1, 1.30, .92, confidence=1), _leg(2, 1.28, .92, confidence=1, kickoff="2026-11-25T12:00:00Z")
    double = assess_double(a, b)
    assert double["accepted"]
    decision = _choose([a, b], [double], profile=PROFILES["agresivo_experimental"])
    for alt in decision["alternatives"]:
        if not alt.get("stakes_cents"):
            continue
        both_lose = [s for s in alt["scenarios"] if not any(s["outcome"].values())]
        assert both_lose and -both_lose[0]["profit_cents"] == alt["max_loss_cents"] == sum(alt["stakes_cents"])
        assert alt["max_loss_share"] <= PROFILES["agresivo_experimental"].max_scenario_loss_fraction + 1e-9
        assert len(alt["scenarios"]) == 2 ** len({k for s in alt["scenarios"] for k in s["outcome"]})
    # A single on another selection of the same fixture can never join that double.
    other = dict(a, market="btts")
    alternatives = _choose([other, b], [double])["alternatives"]
    assert not any("Individual Partido 1" in alt["description"] and "Combinada" in alt["description"] for alt in alternatives)


def test_alternatives_include_abstention_and_every_two_fixture_option():
    a, b = _leg(1, 1.45, .80, confidence=.9), _leg(2, 1.40, .82, confidence=.9, kickoff="2026-11-25T12:00:00Z")
    double = assess_double(a, b)
    decision = _choose([a, b], [double])
    descriptions = {alt["description"] for alt in decision["alternatives"]}
    assert "No apostar" in descriptions
    assert {"Individual Partido 1", "Individual Partido 2", "Individual Partido 1 + Individual Partido 2"} <= descriptions
    assert sum(alt["chosen"] for alt in decision["alternatives"]) == 1
    assert decision["evaluated_alternatives"] >= 4


def test_order_of_candidates_does_not_change_the_decision():
    a, b = _leg(1, 1.45, .80, confidence=.9), _leg(2, 1.40, .82, confidence=.9, kickoff="2026-11-25T12:00:00Z")
    first = _choose([a, b])
    second = _choose([b, a])
    key = lambda d: sorted((t["legs"][0]["match_id"], t["stake_cents"]) for t in d["tickets"])
    assert key(first) == key(second)


# --- Settlement -------------------------------------------------------------------------------------

def test_double_with_void_leg_pays_the_other_leg_once():
    ticket = {"stake_cents": 1000, "legs": [{"match_id": 1, "market": "home_win", "price": 1.5},
                                             {"match_id": 2, "market": "home_win", "price": 1.6}]}
    won = SimpleNamespace(status="finished", home_goals=1, away_goals=0)
    cancelled = SimpleNamespace(status="cancelled")
    assert settle_ticket(ticket, {1: (won, None), 2: (cancelled, None)}) == ("won", 1500)
    assert settle_ticket(ticket, {1: (cancelled, None), 2: (cancelled, None)}) == ("void", 1000)


def test_postponed_leg_waits_then_voids():
    now = dt.datetime(2026, 11, 26, 12)
    ticket = {"stake_cents": 1000, "legs": [{"match_id": 1, "market": "home_win", "price": 1.5}]}
    recent = SimpleNamespace(status="postponed", kickoff_utc=now - dt.timedelta(hours=10))
    old = SimpleNamespace(status="postponed", kickoff_utc=now - dt.timedelta(hours=49))
    assert settle_ticket(ticket, {1: (recent, None)}, now=now) == ("pending", 0)
    assert settle_ticket(ticket, {1: (old, None)}, now=now) == ("void", 1000)


def test_double_loses_as_soon_as_one_leg_loses():
    ticket = {"stake_cents": 1000, "legs": [{"match_id": 1, "market": "home_win", "price": 1.5},
                                             {"match_id": 2, "market": "home_win", "price": 1.6}]}
    lost = SimpleNamespace(status="finished", home_goals=0, away_goals=1)
    pending = SimpleNamespace(status="scheduled")
    assert settle_ticket(ticket, {1: (lost, None), 2: (pending, None)}) == ("lost", 0)


# --- End-to-end decision on the trial bank ----------------------------------------------------------

def _afc_pair(db, now):
    from backend.app.db.models.core import Competition, Season, Team
    from backend.app.db.models.matches import Match

    league = db.query(Competition).filter_by(code="afc_champions_league").one_or_none()
    if not league:
        league = Competition(code="afc_champions_league", name="AFC Elite", country="Asia")
        db.add(league)
        db.flush()
    season = Season(competition_id=league.id, label="capital-test")
    teams = [Team(canonical_name=f"Capital team {i}") for i in range(4)]
    db.add_all([season, *teams])
    db.flush()
    matches = [Match(provider="test", provider_id=f"capital-{i}", competition_id=league.id, season_id=season.id,
                     kickoff_utc=now + dt.timedelta(minutes=minutes), status="scheduled",
                     home_team_id=teams[2 * i].id, away_team_id=teams[2 * i + 1].id)
               for i, minutes in enumerate((30, 150))]
    db.add_all(matches)
    db.flush()
    return matches


def test_restart_does_not_duplicate_and_lookahead_keeps_its_own_cut(db_session, monkeypatch):
    from backend.app.db.models.publication import BotTrialCut
    from backend.app.services import bot_bankroll, prediction_service

    now = dt.datetime(2031, 3, 4, 11, 45)  # far from fixtures committed by other suites
    first, later = _afc_pair(db_session, now)
    monkeypatch.setattr(prediction_service, "score_api_candidate", lambda *args, **kwargs: (None, [], {}))
    monkeypatch.setattr(bot_bankroll, "market_track_records_with_provenance", lambda *args, **kwargs: {})
    monkeypatch.setattr(bot_bankroll, "match_context", lambda *args: {})
    monkeypatch.setattr(bot_bankroll, "choose_match_pick", lambda *args: (None, "pasar"))

    payload = bot_bankroll.capture_bot_decision(db_session, now=now, trial=True, continuous=True)
    assert [row["match_id"] for row in payload["fixtures"]] == [first.id]
    assert payload["lookahead"] == [later.id]
    assert payload["tickets"] == [] and payload["capital"]["chosen"] == "No apostar"
    assert bot_bankroll.capture_bot_decision(db_session, now=now, trial=True, continuous=True) is None
    later_cut = bot_bankroll.capture_bot_decision(db_session, now=now + dt.timedelta(hours=1, minutes=45),
                                                  trial=True, continuous=True)
    assert [row["match_id"] for row in later_cut["fixtures"]] == [later.id]
    assert db_session.query(BotTrialCut).count() == 2
    # No decision is ever built after kickoff.
    assert bot_bankroll.capture_bot_decision(db_session, now=now + dt.timedelta(hours=3), trial=True, continuous=True) is None


def test_dry_run_never_touches_the_bank(db_session, monkeypatch):
    from backend.app.db.models.publication import BotTrialCut
    from backend.app.services import bot_bankroll, prediction_service

    now = dt.datetime(2031, 3, 4, 11, 45)  # far from fixtures committed by other suites
    _afc_pair(db_session, now)
    monkeypatch.setattr(prediction_service, "score_api_candidate", lambda *args, **kwargs: (None, [], {}))
    monkeypatch.setattr(bot_bankroll, "market_track_records_with_provenance", lambda *args, **kwargs: {})
    monkeypatch.setattr(bot_bankroll, "match_context", lambda *args: {})
    monkeypatch.setattr(bot_bankroll, "choose_match_pick", lambda *args: (None, "pasar"))
    before = db_session.query(BotTrialCut).count()
    payload = bot_bankroll.capture_bot_decision(db_session, now=now, trial=True, continuous=True, dry_run=True)
    assert payload is not None and payload["capital"]["policy_version"] == bot_capital.CAPITAL_POLICY_VERSION
    assert db_session.query(BotTrialCut).count() == before


def test_manual_ticket_respects_reserve(db_session, monkeypatch):
    from backend.app.db.models.matches import MatchOddsHistory
    from backend.app.services import bot_bankroll

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    match = _afc_pair(db_session, now)[0]
    quote = MatchOddsHistory(match_id=match.id, bookmaker="Book", market="over_under_goals", line=1.5, selection="over",
                             price=1.35, snapshot_type="pre_match", provider_updated_at=now - dt.timedelta(minutes=5),
                             fetched_at=now - dt.timedelta(minutes=4), recorded_at=now - dt.timedelta(minutes=4))
    db_session.add(quote)
    db_session.flush()
    with pytest.raises(ValueError, match="límites"):
        bot_bankroll.record_manual_ticket(db_session, quote.id, 29_000)
    payload = bot_bankroll.record_manual_ticket(db_session, quote.id, 2_000)
    assert payload["policy_version"] == "manual-prematch"
    assert payload["tickets"][0]["kind"] == "manual_prematch"
    report = bot_bankroll.bot_report(db_session, trial=True)
    assert report["summary"]["manual"]["tickets"] == 1 and report["summary"]["automatic"]["tickets"] == 0
    assert report["reserved_cents"] == 2_000


# --- Simulations and compatibility ------------------------------------------------------------------

def test_profile_simulations_order_risk():
    result = bot_capital.simulate_profiles(400, 60)
    for world in result["worlds"].values():
        assert world["prudente"]["p95_max_drawdown"] <= world["agresivo_experimental"]["p95_max_drawdown"]
        assert world["prudente"]["share_stakes_100_plus"] == 0
        assert world["agresivo_experimental"]["p99_stake_cents"] <= 15_000
    assert bot_capital.simulate_profiles(400, 60) is result


def test_eight_laboratory_strategies_and_ranking_are_untouched():
    from backend.app.config.settings import get_settings
    from backend.app.services.strategy_shadow import WEIGHTS

    assert len(WEIGHTS) == 8
    settings = get_settings()
    assert (settings.min_opportunity_odds, settings.max_opportunity_odds) == (1.30, 1.80)
    from backend.app.config.settings import Settings

    # Code defaults stay prudent; the experimental profile only comes from explicit configuration.
    assert Settings.model_fields["bot_risk_profile"].default == "moderado"
    assert Settings.model_fields["bot_aggressive_authorized"].default is False


def test_minimum_stake_constant_is_respected():
    decision = _choose([_leg(1, 1.40, .80, confidence=.9)])
    assert all(t["stake_cents"] >= MIN_STAKE_CENTS and t["stake_cents"] % 100 == 0 for t in decision["tickets"])


# --- Provisional plan of the day ----------------------------------------------------------------------

def test_plan_replays_cuts_in_order_carries_capital_and_writes_nothing(db_session, monkeypatch):
    from backend.app.db.models.publication import BotTrialCut, BotTrialDecision
    from backend.app.services import bot_bankroll

    now = dt.datetime(2031, 3, 4, 9, 0)  # 10:00 in Madrid, far from fixtures of other suites
    first, later = _afc_pair(db_session, now)  # kickoffs 09:30 and 11:30 UTC, within the double lookahead
    monkeypatch.setattr(bot_bankroll, "load_predictions", lambda *args, **kwargs: {})
    monkeypatch.setattr(bot_bankroll, "market_track_records_with_provenance", lambda *args, **kwargs: {})
    seen_capital = []

    def fake_cut(db, fixtures, lookahead, capital, at, **kwargs):
        seen_capital.append((capital["available"], [m.id for m in fixtures], [m.id for m in lookahead]))
        tickets = [{"stake_cents": 4_000, "legs": [{"match_id": fixtures[0].id}]}] if fixtures[0].id == first.id else []
        return {"fixtures": [], "tickets": tickets, "capital": {
            "chosen": "x", "reason": "y", "alternatives": [], "evaluated_alternatives": 1, "accepted_doubles": []}}

    monkeypatch.setattr(bot_bankroll, "evaluate_cut", fake_cut)
    before = (db_session.query(BotTrialCut).count(), db_session.query(BotTrialDecision).count())
    plan = bot_bankroll.bot_plan(db_session, now=now)
    assert [step["cut_at"] for step in plan["steps"]] == ["2031-03-04T09:00:00Z", "2031-03-04T10:30:00Z"]
    assert seen_capital == [(30_000, [first.id], [later.id]), (26_000, [later.id], [])]
    assert plan["steps"][1]["capital_before"] == {"available": 26_000, "reserved": 4_000}
    assert plan["next"] is plan["steps"][0]
    assert (db_session.query(BotTrialCut).count(), db_session.query(BotTrialDecision).count()) == before


def test_response_cache_serves_stale_value_while_refreshing(monkeypatch):
    import threading

    from backend.app.services import response_cache

    response_cache.clear()
    calls = []
    release = threading.Event()

    def compute():
        calls.append(1)
        if len(calls) > 1:
            release.wait(2)
        return len(calls)

    assert response_cache.cached("k", compute, fresh_seconds=0)[0] == 1
    # Stale: answered immediately with the previous value; the refresh runs in the background.
    assert response_cache.cached("k", compute, fresh_seconds=0)[0] == 1
    release.set()
    for _ in range(100):
        if response_cache._entries["k"][1] == 2:
            break
        threading.Event().wait(.01)
    assert response_cache.cached("k", compute, fresh_seconds=60)[0] == 2
    response_cache.clear()


def test_calibration_error_is_not_counted_twice_when_sizing():
    # Real Madrid–PSV, 24/11: away team to score at 1.50, p 76.9 %, confidence 0.51 → effective 69.7 %.
    leg = dict(_leg(1, 1.50, .769, confidence=.506), effective_probability=.697181, calibration_error=.05)
    decision = _choose([leg], equity=29_610, peak=30_000)
    assert decision["chosen"] == "Individual Partido 1"
    assert 500 <= decision["tickets"][0]["stake_cents"] <= 1_500
    chosen = [a for a in decision["alternatives"] if a["chosen"]][0]
    assert chosen["calibration_stress_log_growth"] < chosen["robust_log_growth"]
    # A sub-1 % effective edge still abstains.
    thin = dict(_leg(2, 1.75, .679, confidence=.495), effective_probability=.576828, calibration_error=.05)
    assert _choose([thin], equity=29_610)["tickets"] == []


# --- User stake floor and high-probability doubles ---------------------------------------------------

AGGRESSIVE = PROFILES["agresivo_experimental"]


def test_stake_floor_bets_thin_positive_edges_at_the_floor_but_never_without_edge():
    thin = dict(_leg(1, 1.75, .679, confidence=.495), effective_probability=.5768)
    decision = choose_portfolio([thin], [], AGGRESSIVE, equity=29_610, available=29_610, reserved=0,
                                peak=30_000, min_stake_cents=5_000)
    assert [t["stake_cents"] for t in decision["tickets"]] == [5_000]
    none = dict(_leg(2, 1.66, .689, confidence=.47), effective_probability=.60)  # break-even 60.2 %
    decision = choose_portfolio([none], [], AGGRESSIVE, equity=29_610, available=29_610, reserved=0,
                                peak=30_000, min_stake_cents=5_000)
    assert decision["tickets"] == []


def test_stake_floor_respects_exposure_limits():
    legs = [dict(_leg(i, 1.60, .80, confidence=.9, kickoff=f"2026-11-2{i}T20:00:00Z")) for i in range(1, 6)]
    decision = choose_portfolio(legs, [], AGGRESSIVE, equity=30_000, available=30_000, reserved=0,
                                peak=30_000, min_stake_cents=5_000)
    total = sum(t["stake_cents"] for t in decision["tickets"])
    assert all(t["stake_cents"] >= 5_000 for t in decision["tickets"])
    assert total <= AGGRESSIVE.max_exposure_fraction * 30_000 and total <= (1 - AGGRESSIVE.min_reserve_fraction) * 30_000
    assert choose_portfolio(legs, [], AGGRESSIVE, equity=30_000, available=6_000, reserved=24_000,
                            peak=30_000, min_stake_cents=5_000)["tickets"] == []


def test_high_probability_double_uses_model_probabilities_and_needs_positive_ev():
    from backend.app.services.bot_capital import assess_high_probability_double

    city = _leg(1, 1.15, .875, confidence=.64, competition="champions_league")
    psg = _leg(2, 1.18, .954, confidence=.60, competition="champions_league", kickoff="2026-11-25T20:00:00Z")
    double = assess_high_probability_double(city, psg)
    assert double["accepted"] and double["strategy"] == "alta_probabilidad"
    assert double["legs"][0]["effective_probability"] == .875
    assert double["effective_probability"] == min(r["probability"] for r in double["sensitivity"][:3])
    # Two likely selections priced below their probability never qualify.
    poor = assess_high_probability_double(_leg(1, 1.10, .85), _leg(2, 1.12, .86, kickoff="2026-11-25T20:00:00Z"))
    assert not poor["accepted"]
    assert assess_high_probability_double(_leg(1, 1.15, .70), psg) is None
    decision = choose_portfolio([], [double], AGGRESSIVE, equity=29_610, available=29_610, reserved=0,
                                peak=30_000, min_stake_cents=5_000)
    assert decision["tickets"][0]["strategy"] == "alta_probabilidad"
    assert 5_000 <= decision["tickets"][0]["stake_cents"] <= AGGRESSIVE.max_double_fraction * 29_610
