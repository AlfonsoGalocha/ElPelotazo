"""Forward-only decisions funded by a virtual euro balance."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.app.config.settings import get_settings
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.db.models.publication import BotDecision, BotTrialDecision
from backend.app.prediction.anomaly import compute_anomaly_flags
from backend.app.prediction.eligibility import observed_corner_total
from backend.app.prediction.opportunities import assess_opportunity, forecast_priority
from backend.app.services.bot_match_context import choose_match_pick, match_context
from backend.app.services.evaluation_service import market_track_records_with_provenance
from backend.app.services.publication_service import selection_outcome
from backend.app.services.ranking_config_service import get_opportunity_odds_band

LEAGUES = ("laliga", "premier_league", "serie_a")
MADRID = ZoneInfo("Europe/Madrid")
INITIAL_CENTS = 30_000
POLICY_VERSION = "forward-v3"
START_DATE = dt.date(2026, 11, 23)


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


def capture_bot_decision(db, *, now=None, early=False, dry_run=False, trial=False):
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    day = now.replace(tzinfo=dt.timezone.utc).astimezone(MADRID).date()
    if day < START_DATE:
        return None
    decision_model = BotTrialDecision if trial else BotDecision
    existing = db.get(decision_model, day)
    if existing is not None and not dry_run:
        return existing.payload
    fixtures = competition_fixtures(db, day)
    if not fixtures or any(match.status != "scheduled" or match.kickoff_utc <= now for match in fixtures):
        return None
    if not early and fixtures[0].kickoff_utc - now > dt.timedelta(hours=1):
        return None

    settings = get_settings()
    floor, ceiling = get_opportunity_odds_band(db, settings)
    settings = settings.model_copy(update={"min_opportunity_odds": floor, "max_opportunity_odds": ceiling})
    predictions = defaultdict(dict)
    for p in db.query(Prediction).filter(
        Prediction.match_id.in_([match.id for match in fixtures]), Prediction.created_at <= now,
    ).order_by(Prediction.created_at, Prediction.id):
        predictions[p.match_id][(p.market, p.line, p.selection)] = p

    provenance = market_track_records_with_provenance(db, as_of=now)
    best = []
    decisions = []
    for match in fixtures:
        rows = list(predictions[match.id].values())
        qualified = []
        reasons = defaultdict(int)
        for p in rows:
            item = assess_opportunity(
                p, match.odds, ALL_MARKET_TO_ODDS_LOOKUP.get(p.market), settings, now,
                provenance.get((match.competition.code, p.market)), compute_anomaly_flags(p, rows, settings), allow_original=True,
            )
            if item.reasons:
                for reason in item.reasons:
                    reasons[reason] += 1
            else:
                qualified.append((p, item))
        context = match_context(db, match, now, rows)
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
    balance = INITIAL_CENTS if trial else bot_report(db)["balance_cents"]
    payload = dict(day=day.isoformat(), captured_at=now.isoformat() + "Z",
                   policy_version=POLICY_VERSION, odds_band=[floor, ceiling], available_at_cut_cents=balance,
                   fixtures=decisions, tickets=make_tickets(best, balance))
    if dry_run:
        return payload
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(decision_model).values(day=day, payload=payload).on_conflict_do_nothing(index_elements=["day"]))
    return db.get(decision_model, day).payload


def settle_ticket(ticket, fixtures):
    odds = Decimal(1)
    pending = False
    for leg in ticket["legs"]:
        match, corners = fixtures[leg["match_id"]]
        if match.status == "cancelled":
            continue
        result = selection_outcome(leg["market"], match.home_goals, match.away_goals, corners) if match.status == "finished" else None
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


def bot_report(db, *, trial=False):
    decision_model = BotTrialDecision if trial else BotDecision
    days = db.query(decision_model).order_by(decision_model.day).all()
    ids = {leg["match_id"] for day in days for ticket in day.payload["tickets"] for leg in ticket["legs"]}
    fixtures = {}
    for match, corners in db.query(Match, observed_corner_total()).filter(Match.id.in_(ids)):
        stats = match.statistics
        if corners is None and stats is not None:
            home, away = stats.home_corners, stats.away_corners
            if home is not None and away is not None and min(home, away) >= 0:
                corners = home + away
        fixtures[match.id] = (match, corners)
    balance = INITIAL_CENTS
    reserved = 0
    rows = []
    for day in days:
        if trial:
            balance, reserved = INITIAL_CENTS, 0
        tickets = []
        day_profit = 0
        for ticket in day.payload["tickets"]:
            state, returned = settle_ticket(ticket, fixtures)
            if state == "pending":
                reserved += ticket["stake_cents"]
            else:
                day_profit += returned - ticket["stake_cents"]
            balance += returned - ticket["stake_cents"]
            tickets.append(dict(ticket, state=state, returned_cents=returned))
        rows.append(dict(day.payload, tickets=tickets, profit_cents=day_profit))
    return dict(initial_cents=INITIAL_CENTS, balance_cents=balance, reserved_cents=reserved, days=rows[::-1])
