"""Forward-only decisions funded by a virtual euro balance."""

import datetime as dt
import logging
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
from math import prod
from threading import Lock
from time import monotonic
from types import SimpleNamespace

import httpx
from sqlalchemy import text
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.app.config.settings import get_settings
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match, MatchOddsHistory
from backend.app.db.models.modeling import Prediction
from backend.app.db.models.publication import BotDecision, BotManualTicket, BotTrialCut, BotTrialDecision
from backend.app.ingestion.api_football.client import ApiFootballClient
from backend.app.prediction.anomaly import compute_anomaly_flags
from backend.app.prediction.eligibility import observed_corner_total
from backend.app.prediction.opportunities import assess_opportunity, forecast_priority, valid_probability
from backend.app.services.bot_capital import (
    CAPITAL_POLICY_VERSION, COMBO_LEG_ODDS, COMBO_MARKETS, MAX_STAKE_QUOTE_AGE_MINUTES, MIN_STAKE_CENTS, PROFILES,
    active_profile, assess_double, assess_high_probability_double, choose_portfolio, leg_from_candidate, leg_key,
    profile_summary, simulate_profiles,
)
from backend.app.services.bot_match_context import OUTSIDER_GOAL_BAND, TEAM_SCORE_SIDE, choose_match_pick, match_context, team_win_prices
from backend.app.services.evaluation_service import market_track_records_with_provenance
from backend.app.services.publication_service import selection_outcome
from backend.app.services.ranking_config_service import get_opportunity_odds_band

LEAGUES = ("laliga", "premier_league", "serie_a")
MADRID = ZoneInfo("Europe/Madrid")
INITIAL_CENTS = 30_000
POLICY_VERSION = "forward-v5-portfolio"
CONTINUOUS_POLICY_VERSION = "forward-v5-continuous-portfolio"
# Fixtures of the next 24 h may join a cut only as the second leg of a double; unless used,
# they keep their own cut. A used fixture is then exposed until it settles.
COMBO_LOOKAHEAD = dt.timedelta(hours=24)
POSTPONED_VOID_AFTER = dt.timedelta(hours=48)
# The virtual trial bank follows every competition with fixtures; competitions without
# forecasts simply yield no candidates.
TRIAL_LEAGUES = (*LEAGUES, "bundesliga", "ligue_1", "champions_league", "afc_champions_league", "liga_f")


def trial_competitions():
    return Competition.id.isnot(None)
START_DATE = dt.date(2026, 11, 23)
LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
_live_lock = Lock()
_live_checked_at = float("-inf")
_live_scores = {}
_live_fixture_ids = ()
FINAL_STATUSES = {"FT", "AET", "PEN"}
logger = logging.getLogger(__name__)


def live_scores(fixture_ids):
    """Share one provider refresh per minute across bankroll viewers."""
    global _live_checked_at, _live_scores, _live_fixture_ids
    with _live_lock:
        if fixture_ids == _live_fixture_ids and monotonic() - _live_checked_at < 60:
            return _live_scores
        _live_fixture_ids = fixture_ids
        _live_checked_at = monotonic()
        try:
            with httpx.Client(timeout=5.0) as http_client:
                provider = ApiFootballClient(http_client)
                rows = []
                if provider.is_available():
                    for offset in range(0, len(fixture_ids), 20):
                        ids = "-".join(fixture_ids[offset:offset + 20])
                        rows.extend(provider._get("/fixtures", {"ids": ids}))
            scores = {}
            for row in rows:
                fixture = row["fixture"]
                status = fixture["status"]
                goals = row.get("goals") or {}
                home, away = goals.get("home"), goals.get("away")
                if status["short"] in LIVE_STATUSES | FINAL_STATUSES and all(type(n) is int and n >= 0 for n in (home, away)):
                    scores[f"api-football-{fixture['id']}"] = {
                        "home": home, "away": away, "minute": status.get("elapsed"),
                        "extra": status.get("extra"), "status": status["short"],
                    }
            _live_scores = scores
        except (httpx.HTTPError, RuntimeError, KeyError, TypeError) as exc:
            logger.warning("Bankroll live scores unavailable (%s)", type(exc).__name__)
            _live_scores = {}
        return _live_scores


def with_live_score(leg, match, scores):
    if match.provider != "api_football" or match.status not in {"scheduled", "live"}:
        return leg
    score = scores.get(match.provider_id)
    if score is None or score["status"] not in LIVE_STATUSES:
        return leg
    side = TEAM_SCORE_SIDE.get(leg["market"])
    return dict(leg, live=dict(score, goal_met=side is not None and score[side] > 0))


def competition_fixtures(db, day):
    start = dt.datetime.combine(day, dt.time.min, MADRID)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, MADRID)
    start, end = [value.astimezone(dt.timezone.utc).replace(tzinfo=None) for value in (start, end)]
    return db.query(Match).join(Competition).filter(
        Competition.code.in_(LEAGUES), Match.kickoff_utc >= start, Match.kickoff_utc < end,
        Match.status != "cancelled",
    ).order_by(Match.kickoff_utc, Match.id).all()


def candidate_leg(prediction, item):
    quote = item.quote
    return dict(
        match_id=prediction.match_id, match=f"{prediction.match.home_team.canonical_name} – {prediction.match.away_team.canonical_name}",
        competition=prediction.match.competition.code, kickoff=prediction.match.kickoff_utc.isoformat() + "Z",
        market=prediction.market, line=prediction.line, selection=prediction.selection,
        prediction_id=prediction.id, model_version_id=prediction.model_version_id,
        predicted_at=prediction.created_at.isoformat() + "Z",
        probability=item.probability, probability_source=item.probability_source, price=quote.price, ev=item.ev,
        conservative_ev=item.conservative_ev, bookmaker=quote.bookmaker,
        quote_history_id=quote.history_quote_id, odds_origin_at=quote.provider_updated_at.isoformat() + "Z",
        odds_fetched_at=quote.fetched_at.isoformat() + "Z",
    )


def combo_legs(db, match, rows, settings, now, provenance, lookup):
    """Bot-only universe of double components; it never feeds the opportunity ranking."""
    combo_settings = settings.model_copy(update={
        "min_opportunity_odds": COMBO_LEG_ODDS[0], "max_opportunity_odds": COMBO_LEG_ODDS[1],
    })
    legs, rejected = [], []
    for p in rows:
        if p.market not in COMBO_MARKETS and not p.market.startswith("corners_"):
            continue
        reliability = provenance.get((match.competition.code, p.market))
        item = assess_opportunity(
            p, match.odds, lookup.get(p.market), combo_settings, now, reliability,
            compute_anomaly_flags(p, rows, settings),
        )
        reasons = list(item.reasons)
        # Corners join only with exact local evidence; low prices alone are never enough.
        if p.market.startswith("corners_") and "fiabilidad_historica_insuficiente" in item.confidence_reasons:
            reasons.append("corners_sin_evidencia_local")
        if item.quote is None or not valid_probability(item.probability):
            if item.reference_quote is not None and valid_probability(item.probability):
                rejected.append(dict(market=p.market, price=item.reference_quote.price, probability=round(item.probability, 4),
                                     ev=round(item.ev, 4) if item.ev is not None else None, reasons=reasons))
            continue
        leg = leg_from_candidate(candidate_leg(p, item), p, item, reliability, now)
        reasons += leg["confidence_gates"]
        if reasons or leg["effective_probability"] is None:
            rejected.append(dict(market=p.market, price=leg["price"], probability=round(leg["probability"], 4),
                                 ev=round(leg["ev"], 4) if leg["ev"] is not None else None, reasons=reasons))
        else:
            legs.append(leg)
    in_band = [r for r in rejected if COMBO_LEG_ODDS[0] <= r["price"] <= COMBO_LEG_ODDS[1]]
    in_band.sort(key=lambda r: (r["ev"] is not None, r["ev"] or 0), reverse=True)
    # Best confirmed-edge components plus the most likely ones for high-probability doubles.
    by_edge = sorted(legs, key=lambda leg: leg["effective_probability"] * leg["price"], reverse=True)[:2]
    likely = sorted(legs, key=lambda leg: leg["probability"], reverse=True)[:2]
    return list({leg_key(leg): leg for leg in (*by_edge, *likely)}.values()), in_band[:4]


def build_doubles(legs_by_match, decision_ids):
    assessed, likely = [], []
    for a_id, b_id in combinations(sorted(legs_by_match), 2):
        if a_id not in decision_ids and b_id not in decision_ids:
            continue
        for a in legs_by_match[a_id]:
            for b in legs_by_match[b_id]:
                assessed.append(assess_double(a, b))
                if (high := assess_high_probability_double(a, b)) is not None:
                    likely.append(high)
    accepted = sorted([d for d in assessed if d["accepted"]], key=lambda d: d["robust_ev"], reverse=True)[:3]
    accepted += sorted([d for d in likely if d["accepted"]], key=lambda d: d["robust_ev"], reverse=True)[:2]
    rejected = [d for d in (*assessed, *likely) if not d["accepted"]]
    return accepted, rejected


def _public_double(d):
    return dict({k: v for k, v in d.items() if k != "legs"},
                matches=[leg["match"] for leg in d["legs"]], markets=[leg["market"] for leg in d["legs"]],
                prices=[leg["price"] for leg in d["legs"]], probabilities=[leg["probability"] for leg in d["legs"]])


def capital_state(report):
    summary = report["summary"]
    return dict(available=report["balance_cents"], reserved=report["reserved_cents"],
                equity=report["balance_cents"] + report["reserved_cents"], peak=summary["peak_equity_cents"],
                pending_matches=set(report["pending_match_ids"]))


def trial_seen_matches(db):
    """Fixtures already decided by a trial cut or carrying a recorded ticket."""
    previous = [row.payload for table in (BotTrialDecision, BotTrialCut, BotManualTicket) for row in db.query(table)]
    seen = {fixture["match_id"] for payload in previous for fixture in payload.get("fixtures", [])}
    return seen | {leg["match_id"] for payload in previous for ticket in payload["tickets"] for leg in ticket["legs"]}


def load_predictions(db, matches, now, *, score_candidates):
    """Stored forecasts, plus on-the-fly previews for API competitions without stored rows."""
    predictions = defaultdict(dict)
    for p in db.query(Prediction).filter(
        Prediction.match_id.in_([match.id for match in matches]), Prediction.created_at <= now,
    ).order_by(Prediction.created_at, Prediction.id):
        predictions[p.match_id][(p.market, p.line, p.selection)] = p
    if score_candidates:
        from backend.app.normalization.competitions import API_CANDIDATE_COMPETITIONS
        from backend.app.services.prediction_service import _market_line_and_selection, score_api_candidate

        by_id = {match.id: match for match in matches}
        for code in API_CANDIDATE_COMPETITIONS:
            targets = [match for match in matches if match.competition.code == code and not predictions[match.id]]
            version, forecasts, _ = score_api_candidate(db, targets, now, code, preview=True)
            for index, row in enumerate(forecasts):
                line, selection = _market_line_and_selection(row.market)
                predictions[row.match_id][(row.market, line, selection)] = SimpleNamespace(
                    **vars(row), id=-index-1, match=by_id[row.match_id], line=line,
                    selection=selection, created_at=now, model_version_id=version.id,
                )
    return predictions


def evaluate_cut(db, fixtures, lookahead, capital, now, *, continuous, predictions=None, provenance=None):
    """Joint decision for one cut; pure with respect to the bank (nothing is written here)."""
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    day = now.replace(tzinfo=dt.timezone.utc).astimezone(MADRID).date()
    balance = capital["available"]

    settings = get_settings()
    profile, profile_note = active_profile(settings.bot_risk_profile, settings.bot_aggressive_authorized)
    floor, ceiling = get_opportunity_odds_band(db, settings)
    settings = settings.model_copy(update={"min_opportunity_odds": floor, "max_opportunity_odds": ceiling})
    outsider_settings = settings.model_copy(update={
        "min_opportunity_odds": max(floor, OUTSIDER_GOAL_BAND[0]),
        "max_opportunity_odds": min(ceiling, OUTSIDER_GOAL_BAND[1]),
    })
    evaluated = [*fixtures, *lookahead]
    if predictions is None:
        predictions = load_predictions(db, evaluated, now, score_candidates=continuous)
    if provenance is None:
        provenance = market_track_records_with_provenance(db, as_of=now)
    singles = []
    legs_by_match = {}
    decisions = []
    for match in evaluated:
        rows = list(predictions[match.id].values())
        # A fixture already carrying a pending ticket cannot add unrelated exposure.
        if match.id in capital["pending_matches"]:
            continue
        legs_by_match[match.id], combo_rejected = combo_legs(db, match, rows, settings, now, provenance, ALL_MARKET_TO_ODDS_LOOKUP)
        if match not in fixtures:
            continue
        qualified = []
        reasons = defaultdict(int)
        for p in rows:
            item = assess_opportunity(
                p, match.odds, ALL_MARKET_TO_ODDS_LOOKUP.get(p.market),
                outsider_settings if p.market in TEAM_SCORE_SIDE else settings, now,
                provenance.get((match.competition.code, p.market)), compute_anomaly_flags(p, rows, settings), allow_original=True,
            )
            if item.reasons:
                for reason in item.reasons:
                    reasons[reason] += 1
            else:
                qualified.append((p, item))
        context = match_context(db, match, now, rows)
        max_age = settings.max_odds_age_minutes or settings.opportunity_max_odds_age_minutes
        context["team_win_prices"] = team_win_prices(match, now, max_age)
        pick, decision = choose_match_pick(qualified, context)
        # Every eligible selection competes, including the verified opportunity of the main page
        # (best ranking score) and the bot's own market-order pick.
        verified = max(qualified, key=lambda pair: (pair[1].score, *forecast_priority(pair)), default=None)
        candidates = []
        for p, item in qualified:
            leg = leg_from_candidate(candidate_leg(p, item), p, item, provenance.get((match.competition.code, p.market)), now)
            leg["source"] = [name for name, chosen in (("oportunidad_verificada", verified), ("orden_del_bot", pick))
                             if chosen is not None and chosen[0] is p] or ["elegible"]
            candidates.append(leg)
        candidates.sort(key=lambda leg: (leg["effective_probability"] or 0) * leg["price"], reverse=True)
        singles.extend(candidates[:3])
        single = candidates[0] if candidates else None
        decisions.append(dict(
            match_id=match.id, match=f"{match.home_team.canonical_name} – {match.away_team.canonical_name}",
            kickoff=match.kickoff_utc.isoformat() + "Z", assessed=len(rows), eligible=len(qualified),
            reasons=dict(reasons), context=context, decision=decision,
            chosen_market=pick[0].market if pick else None,
            single=single and {k: single[k] for k in ("market", "price", "probability", "effective_probability", "source",
                                                       "confidence", "confidence_gates")},
            combo_candidates=[{k: leg[k] for k in ("market", "price", "probability", "effective_probability", "confidence")}
                              for leg in legs_by_match[match.id]],
            combo_rejected=combo_rejected,
        ))
    doubles, rejected = build_doubles(legs_by_match, {match.id for match in fixtures})
    portfolio = choose_portfolio(
        singles, doubles, profile, equity=capital["equity"], available=capital["available"],
        reserved=capital["reserved"], peak=capital["peak"],
        min_stake_cents=max(int(round(get_settings().bot_min_stake_eur * 100)), MIN_STAKE_CENTS),
    )
    payload = dict(day=day.isoformat(), captured_at=now.isoformat() + "Z",
                   policy_version=CONTINUOUS_POLICY_VERSION if continuous else POLICY_VERSION,
                   odds_band=[floor, ceiling], available_at_cut_cents=balance,
                   fixtures=decisions, lookahead=[match.id for match in lookahead],
                   tickets=portfolio.pop("tickets"),
                   capital=dict(
                       policy_version=CAPITAL_POLICY_VERSION, profile=profile_summary(profile, capital["equity"]),
                       profile_note=profile_note, equity_cents=capital["equity"], reserved_cents=capital["reserved"],
                       peak_equity_cents=capital["peak"], singles=singles, doubles=doubles,
                       rejected_doubles=[_public_double(d) for d in rejected[:10]],
                       accepted_doubles=[_public_double(d) for d in doubles], **portfolio,
                   ))
    return payload


def capture_bot_decision(db, *, now=None, early=False, dry_run=False, trial=False, continuous=False):
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    day = now.replace(tzinfo=dt.timezone.utc).astimezone(MADRID).date()
    if day < START_DATE:
        return None
    decision_model = BotTrialDecision if trial else BotDecision
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    if continuous and not trial:
        raise ValueError("Continuous mode uses the existing virtual trial bank")
    if db.get_bind().dialect.name == "postgresql" and not dry_run:
        db.execute(text("SELECT pg_advisory_xact_lock(39178024)"))
    if trial:
        continuous = continuous or db.query(BotTrialCut.match_id).first() is not None
    lookahead = []
    if continuous:
        seen = trial_seen_matches(db)
        upcoming = db.query(Match).join(Competition).filter(
            trial_competitions(), Match.status == "scheduled",
            Match.kickoff_utc > now, Match.kickoff_utc <= now + COMBO_LOOKAHEAD,
            ~Match.id.in_(seen),
        ).order_by(Match.kickoff_utc, Match.id).all()
        fixtures = [match for match in upcoming if match.kickoff_utc <= now + dt.timedelta(hours=1)]
        lookahead = [match for match in upcoming if match.kickoff_utc > now + dt.timedelta(hours=1)]
        report = bot_report(db, trial=True)
        if not fixtures or report["balance_cents"] <= 0:
            return None
    else:
        existing = db.get(decision_model, day)
        if existing is not None and not dry_run:
            return existing.payload
        fixtures = competition_fixtures(db, day)
        if not fixtures or any(match.status != "scheduled" or match.kickoff_utc <= now for match in fixtures):
            return None
        if not early and fixtures[0].kickoff_utc - now > dt.timedelta(hours=1):
            return None
        report = bot_report(db, trial=trial)
    payload = evaluate_cut(db, fixtures, lookahead, capital_state(report), now, continuous=continuous)
    if dry_run:
        return payload
    if continuous:
        db.execute(insert(BotTrialCut).values(match_id=fixtures[0].id, captured_at=now, payload=payload)
                   .on_conflict_do_nothing(index_elements=["match_id"]))
        return payload
    db.execute(insert(decision_model).values(day=day, payload=payload).on_conflict_do_nothing(index_elements=["day"]))
    return db.get(decision_model, day).payload


CUT_LEAD = dt.timedelta(hours=1)


def _compact_cut(payload):
    capital = payload["capital"]
    return dict(
        fixtures=[dict(match_id=f["match_id"], match=f["match"], kickoff=f["kickoff"], eligible=f["eligible"],
                       assessed=f["assessed"], single=f.get("single"), combo_rejected=f.get("combo_rejected", [])[:3])
                  for f in payload["fixtures"]],
        lookahead=payload.get("lookahead", []), chosen=capital["chosen"], reason=capital["reason"],
        tickets=payload["tickets"], alternatives=capital["alternatives"][:6],
        evaluated_alternatives=capital["evaluated_alternatives"], accepted_doubles=capital["accepted_doubles"],
    )


def bot_plan(db, *, now=None):
    """Provisional route of the trial bot for the rest of the Madrid day; it never records anything.

    Cuts are replayed in kickoff order exactly as the scheduler would trigger them, each one
    using today's latest forecasts and quotes. Stakes planned in earlier cuts are treated as
    pending, so later cuts only see the cash that would really remain available.
    """
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    today = now.replace(tzinfo=dt.timezone.utc).astimezone(MADRID).date()
    day_end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min, MADRID).astimezone(dt.timezone.utc).replace(tzinfo=None)
    report = bot_report(db, trial=True)
    capital = capital_state(report)
    seen = trial_seen_matches(db)
    remaining = db.query(Match).join(Competition).filter(
        trial_competitions(), Match.status == "scheduled",
        Match.kickoff_utc > now, Match.kickoff_utc < day_end + COMBO_LOOKAHEAD, ~Match.id.in_(seen),
    ).order_by(Match.kickoff_utc, Match.id).all()
    predictions = load_predictions(db, remaining, now, score_candidates=True)
    provenance = market_track_records_with_provenance(db, as_of=now)
    steps = []
    start = capital["available"]
    while remaining and remaining[0].kickoff_utc < day_end:
        cut_at = max(now, remaining[0].kickoff_utc - CUT_LEAD)
        fixtures = [m for m in remaining if m.kickoff_utc <= cut_at + CUT_LEAD]
        lookahead = [m for m in remaining if m not in fixtures and m.kickoff_utc <= cut_at + COMBO_LOOKAHEAD]
        before = dict(available=capital["available"], reserved=capital["reserved"])
        if capital["available"] > 0:
            payload = evaluate_cut(db, fixtures, lookahead, capital, now, continuous=True,
                                   predictions=predictions, provenance=provenance)
            step = _compact_cut(payload)
        else:
            step = dict(fixtures=[dict(match_id=m.id, match=f"{m.home_team.canonical_name} – {m.away_team.canonical_name}",
                                       kickoff=m.kickoff_utc.isoformat() + "Z") for m in fixtures],
                        chosen="No apostar", reason="Sin saldo disponible en ese momento.", tickets=[], alternatives=[])
        used = {m.id for m in fixtures} | {leg["match_id"] for t in step["tickets"] for leg in t["legs"]}
        staked = sum(t["stake_cents"] for t in step["tickets"])
        capital = dict(capital, available=capital["available"] - staked, reserved=capital["reserved"] + staked,
                       pending_matches=capital["pending_matches"] | used)
        steps.append(dict(step, cut_at=cut_at.isoformat() + "Z", capital_before=before,
                          capital_after=dict(available=capital["available"], reserved=capital["reserved"])))
        remaining = [m for m in remaining if m.id not in used]
    executed = [
        dict(captured_at=day["captured_at"], policy_version=day.get("policy_version"),
             chosen=day.get("capital", {}).get("chosen") or ("Apuesta manual" if day.get("policy_version") in MANUAL_POLICIES else None),
             reason=day.get("capital", {}).get("reason"),
             fixtures=[dict(match_id=f["match_id"], match=f["match"]) for f in day.get("fixtures", [])],
             tickets=day["tickets"])
        for day in report["days"]
        if dt.datetime.fromisoformat(day["captured_at"].rstrip("Z")).replace(tzinfo=dt.timezone.utc).astimezone(MADRID).date() == today
    ][::-1]
    return dict(
        generated_at=now.isoformat() + "Z", day=today.isoformat(), available_cents=start,
        reserved_cents=report["reserved_cents"], executed=executed, steps=steps,
        next=next((step for step in steps), None),
        note=("Plan provisional: cada corte se congela una hora antes del primer partido con las cuotas de ese momento. "
              "Las apuestas previstas en cortes anteriores se cuentan como pendientes; si se liquidan antes, "
              "el saldo y el plan se recalculan."),
    )


def leg_result(leg, match, corners, scores=None, now=None):
    """True/False when decided, "void" when the fixture will not be played, None while pending."""
    if match.status == "cancelled":
        return "void"
    if match.status == "postponed":
        now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        return "void" if now - match.kickoff_utc > POSTPONED_VOID_AFTER else None
    market = leg["market"]
    if match.status == "finished":
        return selection_outcome(market, match.home_goals, match.away_goals, corners)
    if scores and match.provider == "api_football":
        score = scores.get(match.provider_id)
        if score and score["status"] == "FT":
            return selection_outcome(market, score["home"], score["away"], None)
        if score and score["status"] in LIVE_STATUSES:
            side = TEAM_SCORE_SIDE.get(market)
            if side and score[side] > 0:
                return True
    return None


def settle_ticket(ticket, fixtures, scores=None, now=None):
    """A double is lost as soon as one leg loses; void legs pay at 1.00, and the stake comes back once."""
    odds = Decimal(1)
    pending = False
    for leg in ticket["legs"]:
        match, corners = fixtures[leg["match_id"]]
        result = leg_result(leg, match, corners, scores, now)
        if result is False:
            return "lost", 0
        if result is None:
            pending = True
        elif result is True:
            odds *= Decimal(str(leg["price"]))
    if pending:
        return "pending", 0
    returned = int((odds * ticket["stake_cents"]).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return ("void" if odds == 1 else "won"), returned


MANUAL_POLICIES = {"manual-in-play-historical": "manual_live", "manual-prematch": "manual_prematch"}
KIND_LABELS = {
    "auto_single": "Automática individual", "auto_double": "Automática combinada",
    "manual_prematch": "Manual prepartido", "manual_live": "Manual en directo",
}


def ticket_kind(ticket, policy_version):
    if ticket.get("kind"):
        return ticket["kind"]
    if policy_version in MANUAL_POLICIES:
        return MANUAL_POLICIES[policy_version]
    return "auto_double" if len(ticket["legs"]) > 1 else "auto_single"


def _bucket(value, edges, fmt):
    if value is None:
        return "sin_dato"
    for low, high in zip(edges, edges[1:]):
        if low <= value < high:
            return fmt(low, high)
    return fmt(edges[-1], None)


def _performance(tickets):
    settled = [t for t in tickets if t["state"] in {"won", "lost", "void"}]
    staked = sum(t["stake_cents"] for t in settled)
    returned = sum(t["returned_cents"] for t in settled)
    decided = [t for t in settled if t["state"] != "void"]
    return dict(
        tickets=len(tickets), settled=len(settled), pending=len(tickets) - len(settled),
        won=sum(t["state"] == "won" for t in settled), lost=sum(t["state"] == "lost" for t in settled),
        void=sum(t["state"] == "void" for t in settled), staked_cents=staked, profit_cents=returned - staked,
        roi=round((returned - staked) / staked, 4) if staked else None,
        hit_rate=round(sum(t["state"] == "won" for t in decided) / len(decided), 4) if decided else None,
    )


def _grouped(tickets, key):
    groups = defaultdict(list)
    for ticket in tickets:
        groups[key(ticket)].append(ticket)
    return {name: _performance(rows) for name, rows in sorted(groups.items())}


def learning_report(tickets):
    """Capital-management learning, separate from model training: observe, never self-tune."""
    auto = [t for t in tickets if t["kind"].startswith("auto_")]
    price = lambda t: t.get("price") or prod(leg["price"] for leg in t["legs"])
    probability = lambda t: t.get("probability") if t.get("probability") is not None else (
        t["legs"][0].get("probability") if len(t["legs"]) == 1 else None)
    return dict(
        note=("Solo observación prospectiva. Ningún parámetro cambia automáticamente: un ajuste exige al menos "
              "100 decisiones automáticas liquidadas por grupo y validación en un periodo posterior independiente."),
        min_settled_for_adjustment=100,
        by_kind=_grouped(auto, lambda t: t["kind"]),
        by_strategy=_grouped(auto, lambda t: t.get("strategy") or "sin_dato"),
        by_staking=_grouped(auto, lambda t: "dinámico" if t.get("capital_policy") else "reparto_total_anterior"),
        by_odds=_grouped(auto, lambda t: _bucket(price(t), [1.0, 1.3, 1.5, 1.8, 2.2, 3.0], lambda a, b: f"{a:.2f}–{b:.2f}" if b else f"≥{a:.2f}")),
        by_probability=_grouped(auto, lambda t: _bucket(probability(t), [0, .5, .65, .75, .85], lambda a, b: f"{a:.0%}–{b:.0%}" if b else f"≥{a:.0%}")),
        by_market=_grouped(auto, lambda t: " + ".join(leg["market"] for leg in t["legs"]) if len(t["legs"]) < 2 else "combinada"),
        by_competition=_grouped(auto, lambda t: " + ".join(sorted({leg.get("competition") or "sin_dato" for leg in t["legs"]}))),
        by_confidence=_grouped(auto, lambda t: _bucket(t.get("confidence"), [0, .4, .55, .7], lambda a, b: f"{a:.2f}–{b:.2f}" if b else f"≥{a:.2f}")),
    )


_shadow_cache = {}


def shadow_references(days, fixtures):
    """Parallel reference banks replaying the frozen candidates of each cut; the main bank is untouched."""
    cuts = sorted([day for day in days if day.get("capital", {}).get("singles") is not None],
                  key=lambda day: day["captured_at"])
    banks = {}
    for name in (*PROFILES, "plano_10"):
        bank = peak = INITIAL_CENTS
        placed = pending = 0
        for cut in cuts:
            if name == "plano_10":
                tickets = [dict(legs=[leg], stake_cents=1000) for leg in cut["capital"]["singles"]
                           if leg["effective_probability"] and leg["effective_probability"] * leg["price"] > 1]
                tickets = tickets[:max(bank // 1000, 0)]
            else:
                key = (cut["captured_at"], name, bank)
                if len(_shadow_cache) > 2000:
                    _shadow_cache.clear()
                if key not in _shadow_cache:
                    _shadow_cache[key] = choose_portfolio(
                        cut["capital"]["singles"], cut["capital"]["doubles"], PROFILES[name],
                        equity=bank, available=bank, reserved=0, peak=peak,
                    )["tickets"]
                tickets = _shadow_cache[key]
            for ticket in tickets:
                if any(leg["match_id"] not in fixtures for leg in ticket["legs"]):
                    continue
                state, returned = settle_ticket(ticket, fixtures)
                placed += 1
                if state == "pending":
                    pending += 1
                    continue
                bank += returned - ticket["stake_cents"]
                peak = max(peak, bank)
        banks[name] = dict(balance_cents=bank, tickets=placed, pending=pending, profit_cents=bank - INITIAL_CENTS)
    return dict(cuts=len(cuts), banks=banks,
                note="Bancas de referencia sobre los mismos candidatos congelados antes del inicio; los pendientes no cuentan.")


def bot_report(db, *, trial=False, include_live=False):
    decision_model = BotTrialDecision if trial else BotDecision
    days = db.query(decision_model).order_by(decision_model.day).all()
    if trial:
        days = sorted([*days, *db.query(BotTrialCut).all(), *db.query(BotManualTicket).all()], key=lambda row: row.payload.get("captured_at", ""))
    ids = {leg["match_id"] for day in days for ticket in day.payload["tickets"] for leg in ticket["legs"]}
    ids |= {leg["match_id"] for day in days for leg in day.payload.get("capital", {}).get("singles", [])}
    ids |= {leg["match_id"] for day in days for d in day.payload.get("capital", {}).get("doubles", []) for leg in d["legs"]}
    fixtures = {}
    for match, corners in db.query(Match, observed_corner_total()).filter(Match.id.in_(ids)):
        stats = match.statistics
        if corners is None and stats is not None:
            home, away = stats.home_corners, stats.away_corners
            if home is not None and away is not None and min(home, away) >= 0:
                corners = home + away
        fixtures[match.id] = (match, corners)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    live_fixture_ids = tuple(sorted({
        match.provider_id.removeprefix("api-football-")
        for match, _ in fixtures.values()
        if match.provider == "api_football" and match.status in {"scheduled", "live"}
        and now - dt.timedelta(hours=4) <= match.kickoff_utc <= now
    })) if include_live else ()
    scores = live_scores(live_fixture_ids) if live_fixture_ids else {}
    balance = INITIAL_CENTS
    reserved = 0
    settled_equity = peak = INITIAL_CENTS
    max_drawdown = 0.
    rows = []
    all_tickets = []
    pending_matches = set()
    for day in days:
        tickets = []
        day_profit = 0
        policy = day.payload.get("policy_version")
        for ticket in day.payload["tickets"]:
            state, returned = settle_ticket(ticket, fixtures, scores, now)
            if state == "pending":
                reserved += ticket["stake_cents"]
                pending_matches |= {leg["match_id"] for leg in ticket["legs"]}
            else:
                day_profit += returned - ticket["stake_cents"]
                settled_equity += returned - ticket["stake_cents"]
            balance += returned - ticket["stake_cents"]
            legs = [
                with_live_score(leg, fixtures[leg["match_id"]][0], scores)
                for leg in ticket["legs"]
            ] if scores else ticket["legs"]
            leg_states = [leg_result(leg, *fixtures[leg["match_id"]], scores, now) for leg in ticket["legs"]]
            row = dict(ticket, legs=legs, state=state, returned_cents=returned, kind=ticket_kind(ticket, policy),
                       leg_states=["void" if r == "void" else "won" if r is True else "lost" if r is False else "pending"
                                   for r in leg_states],
                       captured_at=day.payload.get("captured_at"),
                       capital_policy=day.payload.get("capital", {}).get("policy_version"))
            tickets.append(row)
            all_tickets.append(row)
        # Same per-cut curve as the bankroll chart, so ticket order inside a cut is irrelevant.
        peak = max(peak, settled_equity)
        max_drawdown = max(max_drawdown, 1 - settled_equity / peak)
        rows.append(dict(day.payload, tickets=tickets, profit_cents=day_profit))
    manual = [t for t in all_tickets if t["kind"].startswith("manual_")]
    automatic = [t for t in all_tickets if t["kind"].startswith("auto_")]
    settings = get_settings()
    profile, note = active_profile(settings.bot_risk_profile, settings.bot_aggressive_authorized)
    equity = balance + reserved
    summary = dict(
        available_cents=balance, committed_cents=reserved, equity_cents=equity,
        net_profit_cents=settled_equity - INITIAL_CENTS, peak_equity_cents=peak, max_drawdown=round(max_drawdown, 4),
        current_drawdown=round(1 - settled_equity / peak, 4) if peak else 0.,
        all=_performance(all_tickets), automatic=_performance(automatic), manual=_performance(manual),
        by_kind={kind: _performance([t for t in all_tickets if t["kind"] == kind]) for kind in KIND_LABELS},
    )
    return dict(
        initial_cents=INITIAL_CENTS, balance_cents=balance, reserved_cents=reserved, days=rows[::-1],
        pending_match_ids=sorted(pending_matches), summary=summary, kind_labels=KIND_LABELS,
        learning=learning_report(all_tickets),
        shadow=shadow_references([row for row in rows], fixtures),
        risk=dict(active=profile.name, note=note, authorized_experimental=settings.bot_aggressive_authorized,
                  profiles={name: profile_summary(p, equity) for name, p in PROFILES.items()}),
    )


def risk_simulations():
    return simulate_profiles()


def record_manual_ticket(db, quote_id, stake_cents):
    """Record an explicitly requested virtual ticket; it never counts as an automatic decision."""
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(39178024)"))
    quote = db.get(MatchOddsHistory, quote_id)
    markets = {value: key for key, value in ALL_MARKET_TO_ODDS_LOOKUP.items()}
    market = markets.get((quote.market, quote.line, quote.selection)) if quote else None
    if quote is None or market is None or quote.snapshot_type != "pre_match":
        raise ValueError("Se necesita una cuota prepartido archivada de un mercado soportado")
    match = db.get(Match, quote.match_id)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if match.status == "scheduled" and now < match.kickoff_utc:
        origin = quote.fetched_at or quote.recorded_at
        if (now - origin).total_seconds() / 60 > MAX_STAKE_QUOTE_AGE_MINUTES:
            raise ValueError("La cuota está desactualizada; actualiza cuotas antes de registrar la apuesta")
        policy = "manual-prematch"
    elif match.status == "live" and quote.recorded_at < match.kickoff_utc <= now:
        policy = "manual-in-play-historical"
    else:
        raise ValueError("El partido debe estar por empezar o en juego con una cuota prepartido")
    existing = db.get(BotManualTicket, match.id)
    if existing is not None:
        return existing.payload
    report = bot_report(db, trial=True)
    capital = capital_state(report)
    settings = get_settings()
    profile, _ = active_profile(settings.bot_risk_profile, settings.bot_aggressive_authorized)
    # Manual tickets obey the same reserve and exposure limits: no all-in, no committed money.
    limit = min(
        capital["available"] - int(profile.min_reserve_fraction * capital["equity"]),
        int(profile.max_exposure_fraction * capital["equity"]) - capital["reserved"],
        profile.max_single_cents, int(profile.max_single_fraction * capital["equity"]),
    )
    if not 0 < stake_cents <= limit:
        raise ValueError(f"Stake fuera de los límites de riesgo: máximo {max(limit, 0) / 100:.2f} €")
    payload = {
        "day": now.replace(tzinfo=dt.timezone.utc).astimezone(MADRID).date().isoformat(),
        "captured_at": now.isoformat() + "Z", "policy_version": policy,
        "odds_band": [quote.price, quote.price], "fixtures": [],
        "tickets": [{"stake_cents": stake_cents, "kind": MANUAL_POLICIES[policy], "legs": [{
            "match_id": match.id, "match": f"{match.home_team.canonical_name} – {match.away_team.canonical_name}",
            "competition": match.competition.code, "kickoff": match.kickoff_utc.isoformat() + "Z",
            "market": market, "line": quote.line, "selection": quote.selection,
            "price": quote.price, "bookmaker": quote.bookmaker,
            "quote_history_id": quote.id, "odds_origin_at": quote.provider_updated_at.isoformat() + "Z"
            if quote.provider_updated_at else None,
        }], "bankroll_share": round(stake_cents / capital["equity"], 4), "max_loss_cents": stake_cents,
            "reason": "Apuesta manual solicitada explícitamente; no se atribuye al bot."}],
    }
    db.add(BotManualTicket(match_id=match.id, captured_at=now, payload=payload))
    db.flush()
    return payload


def refresh_imminent_odds(db, now=None, horizon=dt.timedelta(minutes=70), stale_after=dt.timedelta(minutes=30)):
    """Refresh API-Football odds for fixtures about to be decided, so no stale price is frozen."""
    from sqlalchemy import func

    from backend.app.db.models.matches import MatchOdds
    from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider
    from backend.app.services.data_service import _attach_odds_snapshots
    from backend.app.utils.dates import season_label

    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    settings = get_settings()
    if not (settings.api_football_enabled and settings.api_football_key):
        return {}
    due = db.query(Match).join(Competition).filter(
        trial_competitions(), Match.status == "scheduled",
        Match.kickoff_utc > now, Match.kickoff_utc <= now + horizon,
    ).all()
    freshest = dict(db.query(MatchOdds.match_id, func.max(MatchOdds.fetched_at)).filter(
        MatchOdds.match_id.in_([m.id for m in due]), MatchOdds.snapshot_type == "pre_match",
    ).group_by(MatchOdds.match_id)) if due else {}
    stale = defaultdict(list)
    for match in due:
        if freshest.get(match.id) is None or now - freshest[match.id] > stale_after:
            stale[match.competition].append(match)
    refreshed = {}
    provider = ApiFootballOddsProvider()
    for competition, matches in stale.items():
        dates = [m.kickoff_utc.date() for m in matches]
        try:
            snapshots = provider.fetch_odds_for_window(
                competition.code, int(season_label(min(dates)).split("/")[0]), min(dates), max(dates))
            result = _attach_odds_snapshots(db, snapshots, provider.name, competition.id, "bot.refresh_odds")
            refreshed[competition.code] = result.matched
        except Exception as exc:  # noqa: BLE001 - a failed refresh leaves the freshness gate in charge
            logger.warning("Bot odds refresh failed for %s (%s)", competition.code, type(exc).__name__)
            db.rollback()
    return refreshed
