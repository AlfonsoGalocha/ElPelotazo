"""Forward-only decisions funded by a virtual euro balance."""

import datetime as dt
import logging
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
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
from backend.app.prediction.opportunities import assess_opportunity, forecast_priority
from backend.app.services.bot_match_context import OUTSIDER_GOAL_BAND, TEAM_SCORE_SIDE, choose_match_pick, match_context, team_win_prices
from backend.app.services.evaluation_service import market_track_records_with_provenance
from backend.app.services.publication_service import selection_outcome
from backend.app.services.ranking_config_service import get_opportunity_odds_band

LEAGUES = ("laliga", "premier_league", "serie_a")
MADRID = ZoneInfo("Europe/Madrid")
INITIAL_CENTS = 30_000
POLICY_VERSION = "forward-v3-team-goal"
TRIAL_LEAGUES = (*LEAGUES, "bundesliga", "ligue_1", "champions_league", "afc_champions_league", "liga_f")
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


def make_tickets(best, available_cents):
    if not best or available_cents <= 0:
        return []
    tickets, weights, used = [], [], set()
    doubles = []
    for (p, a), (q, b) in combinations(best, 2):
        if not (a.high_confidence and b.high_confidence and a.quote.bookmaker == b.quote.bookmaker):
            continue
        conservative_ev = (1 + a.conservative_ev) * (1 + b.conservative_ev) - 1
        doubles.append((conservative_ev, p, a, q, b))
    if doubles:
        conservative_ev, p, a, q, b = max(doubles, key=lambda row: row[0])
        tickets.append(dict(legs=[candidate_leg(p, a), candidate_leg(q, b)],
                            conservative_ev=conservative_ev))
        weights.append(1)
        used = {p.match_id, q.match_id}
    for p, item in best:
        if p.match_id not in used:
            tickets.append(dict(legs=[candidate_leg(p, item)]))
            weights.append(1 if item.probability_source == "original" else 2)

    # Integer apportionment keeps the recorded exposure exactly equal to the available balance.
    total_weight = sum(weights)
    allocated = 0
    accumulated_weight = 0
    funded = []
    for ticket, weight in zip(tickets, weights):
        accumulated_weight += weight
        cumulative_cents = available_cents * accumulated_weight // total_weight
        stake = cumulative_cents - allocated
        allocated = cumulative_cents
        if stake:
            funded.append(dict(ticket, stake_cents=stake))
    return funded


def capture_bot_decision(db, *, now=None, early=False, dry_run=False, trial=False, continuous=False):
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    day = now.replace(tzinfo=dt.timezone.utc).astimezone(MADRID).date()
    if day < START_DATE:
        return None
    decision_model = BotTrialDecision if trial else BotDecision
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    if continuous and not trial:
        raise ValueError("Continuous mode uses the existing virtual trial bank")
    if trial:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(39178024)"))
        continuous = continuous or db.query(BotTrialCut.match_id).first() is not None
    if continuous:
        previous = [
            row.payload for table in (BotTrialDecision, BotTrialCut) for row in db.query(table)
        ]
        seen = {fixture["match_id"] for payload in previous for fixture in payload.get("fixtures", [])}
        fixtures = db.query(Match).join(Competition).filter(
            Competition.code.in_(TRIAL_LEAGUES), Match.status == "scheduled",
            Match.kickoff_utc > now, Match.kickoff_utc <= now + dt.timedelta(hours=1),
            ~Match.id.in_(seen),
        ).order_by(Match.kickoff_utc, Match.id).all()
        balance = bot_report(db, trial=True)["balance_cents"]
        if not fixtures or balance <= 0:
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
        balance = bot_report(db, trial=trial)["balance_cents"]

    settings = get_settings()
    floor, ceiling = get_opportunity_odds_band(db, settings)
    settings = settings.model_copy(update={"min_opportunity_odds": floor, "max_opportunity_odds": ceiling})
    outsider_settings = settings.model_copy(update={
        "min_opportunity_odds": max(floor, OUTSIDER_GOAL_BAND[0]),
        "max_opportunity_odds": min(ceiling, OUTSIDER_GOAL_BAND[1]),
    })
    predictions = defaultdict(dict)
    for p in db.query(Prediction).filter(
        Prediction.match_id.in_([match.id for match in fixtures]), Prediction.created_at <= now,
    ).order_by(Prediction.created_at, Prediction.id):
        predictions[p.match_id][(p.market, p.line, p.selection)] = p

    if continuous:
        from backend.app.normalization.competitions import API_CANDIDATE_COMPETITIONS
        from backend.app.services.prediction_service import _market_line_and_selection, score_api_candidate

        matches = {match.id: match for match in fixtures}
        for code in API_CANDIDATE_COMPETITIONS:
            targets = [match for match in fixtures if match.competition.code == code and not predictions[match.id]]
            version, forecasts, _ = score_api_candidate(db, targets, now, code, preview=True)
            for index, row in enumerate(forecasts):
                line, selection = _market_line_and_selection(row.market)
                predictions[row.match_id][(row.market, line, selection)] = SimpleNamespace(
                    **vars(row), id=-index-1, match=matches[row.match_id], line=line,
                    selection=selection, created_at=now, model_version_id=version.id,
                )
    provenance = market_track_records_with_provenance(db, as_of=now)
    best = []
    decisions = []
    for match in fixtures:
        rows = list(predictions[match.id].values())
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
        if pick:
            best.append(pick)
        decisions.append(dict(
            match_id=match.id, match=f"{match.home_team.canonical_name} – {match.away_team.canonical_name}",
            kickoff=match.kickoff_utc.isoformat() + "Z", assessed=len(rows), eligible=len(qualified),
            reasons=dict(reasons), context=context, decision=decision,
            chosen_market=pick[0].market if pick else None,
        ))
    best.sort(key=lambda pair: (pair[1].score, *forecast_priority(pair)), reverse=True)
    payload = dict(day=day.isoformat(), captured_at=now.isoformat() + "Z",
                   policy_version="forward-v4-continuous-team-goal" if continuous else POLICY_VERSION, odds_band=[floor, ceiling], available_at_cut_cents=balance,
                   fixtures=decisions, tickets=make_tickets(best, balance))
    if dry_run:
        return payload
    if continuous:
        db.execute(insert(BotTrialCut).values(match_id=fixtures[0].id, captured_at=now, payload=payload)
                   .on_conflict_do_nothing(index_elements=["match_id"]))
        return payload
    db.execute(insert(decision_model).values(day=day, payload=payload).on_conflict_do_nothing(index_elements=["day"]))
    return db.get(decision_model, day).payload


def settle_ticket(ticket, fixtures, scores=None):
    odds = Decimal(1)
    pending = False
    for leg in ticket["legs"]:
        match, corners = fixtures[leg["match_id"]]
        if match.status == "cancelled":
            continue
        result = None
        market = leg["market"]
        if match.status == "finished":
            result = selection_outcome(market, match.home_goals, match.away_goals, corners)
        elif scores and match.provider == "api_football":
            score = scores.get(match.provider_id)
            if score and score["status"] == "FT":
                result = selection_outcome(market, score["home"], score["away"], None)
            elif score and score["status"] in LIVE_STATUSES:
                side = TEAM_SCORE_SIDE.get(market)
                if side and score[side] > 0:
                    result = True
        if result is False:
            return "lost", 0
        if result is None:
            pending = True
        else:
            odds *= Decimal(str(leg["price"]))
    if pending:
        return "pending", 0
    returned = int((odds * ticket["stake_cents"]).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return ("void" if odds == 1 else "won"), returned


def bot_report(db, *, trial=False, include_live=False):
    decision_model = BotTrialDecision if trial else BotDecision
    days = db.query(decision_model).order_by(decision_model.day).all()
    if trial:
        days = sorted([*days, *db.query(BotTrialCut).all(), *db.query(BotManualTicket).all()], key=lambda row: row.payload.get("captured_at", ""))
    ids = {leg["match_id"] for day in days for ticket in day.payload["tickets"] for leg in ticket["legs"]}
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
    rows = []
    for day in days:
        tickets = []
        day_profit = 0
        for ticket in day.payload["tickets"]:
            state, returned = settle_ticket(ticket, fixtures, scores)
            if state == "pending":
                reserved += ticket["stake_cents"]
            else:
                day_profit += returned - ticket["stake_cents"]
            balance += returned - ticket["stake_cents"]
            legs = [
                with_live_score(leg, fixtures[leg["match_id"]][0], scores)
                for leg in ticket["legs"]
            ] if scores else ticket["legs"]
            tickets.append(dict(ticket, legs=legs, state=state, returned_cents=returned))
        rows.append(dict(day.payload, tickets=tickets, profit_cents=day_profit))
    return dict(initial_cents=INITIAL_CENTS, balance_cents=balance, reserved_cents=reserved, days=rows[::-1])


def record_manual_team_goal(db, quote_id):
    """Record an explicitly requested virtual ticket at an archived price."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(39178024)"))
    quote = db.get(MatchOddsHistory, quote_id)
    if quote is None or (quote.market, quote.line, quote.selection, quote.snapshot_type) != (
        "home_team_total_goals", .5, "over", "pre_match"
    ):
        raise ValueError("Se necesita una cuota archivada de local marca")
    match = db.get(Match, quote.match_id)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if match.status != "live" or not quote.recorded_at < match.kickoff_utc <= now:
        raise ValueError("El partido debe seguir en juego y la cuota debe ser prepartido")
    existing = db.get(BotManualTicket, match.id)
    if existing is not None:
        return existing.payload
    stake = bot_report(db, trial=True)["balance_cents"]
    if stake <= 0:
        raise ValueError("La banca virtual no tiene saldo disponible")
    payload = {
        "day": now.replace(tzinfo=dt.timezone.utc).astimezone(MADRID).date().isoformat(),
        "captured_at": now.isoformat() + "Z", "policy_version": "manual-in-play-historical",
        "odds_band": [quote.price, quote.price], "fixtures": [],
        "tickets": [{"stake_cents": stake, "legs": [{
            "match_id": match.id, "match": f"{match.home_team.canonical_name} – {match.away_team.canonical_name}",
            "market": "home_team_over_0_5", "price": quote.price, "bookmaker": quote.bookmaker,
            "quote_history_id": quote.id, "odds_origin_at": quote.provider_updated_at.isoformat() + "Z"
            if quote.provider_updated_at else None,
        }]}],
    }
    db.add(BotManualTicket(match_id=match.id, captured_at=now, payload=payload))
    db.flush()
    return payload
