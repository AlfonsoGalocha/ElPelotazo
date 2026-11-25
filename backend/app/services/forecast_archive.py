"""Keep the last pre-match forecast of every fixture and grade it once the match ends.

On-the-fly competitions (Champions, AFC Elite, Liga F) never store `Prediction` rows, so without
this archive their forecast vanished at kickoff. The archive keeps the same preview shown on the
main page, refreshed until kickoff and frozen afterwards.
"""

import datetime as dt
import json
from statistics import median
from types import SimpleNamespace

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload

from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.publication import PreMatchForecast, StrategyCapture
from backend.app.services.publication_service import selection_outcome

ARCHIVE_HORIZON = dt.timedelta(hours=3)


def archive_pre_match_forecasts(db, now=None, horizon=ARCHIVE_HORIZON):
    """Upsert the preview of fixtures starting soon; rows are never touched after kickoff."""
    from backend.app.services.today_preview import build_today_previews

    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    fixtures = db.query(Match).options(
        selectinload(Match.competition), selectinload(Match.odds).selectinload(MatchOdds.archived_quote),
    ).filter(Match.status == "scheduled", Match.kickoff_utc > now, Match.kickoff_utc <= now + horizon).all()
    from backend.app.normalization.bookmakers import is_excluded_bookmaker
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    previews = build_today_previews(db, fixtures, now)
    kickoffs = {match.id: match.kickoff_utc for match in fixtures}
    by_id = {match.id: match for match in fixtures}
    for match_id, preview in previews.items():
        # Saved consensus price per market (median across bookmakers), as stored for league forecasts.
        for market, details in (preview.get("markets") or {}).items():
            key = ALL_MARKET_TO_ODDS_LOOKUP.get(market)
            prices = [q.price for q in by_id[match_id].odds if key and (q.market, q.line, q.selection) == key
                      and q.snapshot_type == "pre_match" and q.price > 1 and not is_excluded_bookmaker(q.bookmaker)]
            details["consensus_odds"] = round(median(prices), 4) if prices else None
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    stored = 0
    for match_id, preview in previews.items():
        if not now < kickoffs[match_id]:
            continue
        payload = json.loads(json.dumps(preview, default=str))
        statement = insert(PreMatchForecast).values(match_id=match_id, captured_at=now, payload=payload)
        db.execute(statement.on_conflict_do_update(
            index_elements=["match_id"], set_=dict(captured_at=now, payload=payload),
        ))
        stored += 1
    return stored


def _corner_total(match):
    stats = match.statistics
    if stats and stats.home_corners is not None and stats.away_corners is not None and min(stats.home_corners, stats.away_corners) >= 0:
        return stats.home_corners + stats.away_corners
    return None


def _graded(market, probability, odds, match, corners):
    result = selection_outcome(market, match.home_goals, match.away_goals, corners) if match.status == "finished" else None
    return dict(market=market, probability=probability, odds=odds, is_correct=result)


def pre_match_forecast(db, match):
    """Graded pre-match forecast of a fixture, from the archive or the laboratory capture."""
    corners = _corner_total(match)
    archived = db.get(PreMatchForecast, match.id)
    if archived is not None and archived.captured_at < match.kickoff_utc:
        payload = archived.payload
        markets = [
            _graded(market, details.get("probability"), None, match, corners)
            for market, details in (payload.get("markets") or {}).items() if details.get("probability") is not None
        ]
        pick = lambda row: row and dict(row, **_graded(row["market"], row["probability"], row.get("odds"), match, corners))
        return dict(
            source="previsión_prepartido", captured_at=archived.captured_at.isoformat() + "Z",
            forecast=pick(payload.get("forecast")), opportunity=pick(payload.get("opportunity")),
            markets=sorted(markets, key=lambda row: -row["probability"]),
        )
    capture = db.get(StrategyCapture, match.id)
    if capture is not None and capture.captured_at < match.kickoff_utc:
        best = {}
        for row in capture.payload.get("common") or []:
            probability = row.get("probabilities", {}).get("original")
            if probability is not None and probability > best.get(row["market"], {}).get("probability", -1):
                best[row["market"]] = _graded(row["market"], probability, row.get("price"), match, corners)
        markets = sorted(best.values(), key=lambda row: -row["probability"])
        # Same idea as the main-page "interesting forecast": likely and priced in the usual band.
        interesting = [row for row in markets if row["probability"] >= .65 and row["odds"] and 1.30 <= row["odds"] <= 1.80]
        return dict(
            source="captura_laboratorio", captured_at=capture.captured_at.isoformat() + "Z",
            forecast=(interesting or markets or [None])[0], opportunity=None, markets=markets,
        )
    return None


def archived_model_picks(db, matches, *, minimum=.65, band=(1.30, 1.80)):
    """Round-accuracy rule on archived forecasts: highest original probability ≥ 65 % with a saved
    price in 1.30–1.80. Used for competitions whose forecasts are not stored as `Prediction` rows."""
    picks = {}
    ids = [match.id for match in matches]
    archived = {row.match_id: row for row in db.query(PreMatchForecast).filter(PreMatchForecast.match_id.in_(ids))}
    captures = {row.match_id: row for row in db.query(StrategyCapture).filter(StrategyCapture.match_id.in_(ids))}
    for match in matches:
        candidates = []
        row = archived.get(match.id)
        if row is not None and row.captured_at < match.kickoff_utc:
            candidates = [(details.get("original_probability"), details.get("consensus_odds"), market)
                          for market, details in (row.payload.get("markets") or {}).items()]
        elif (capture := captures.get(match.id)) is not None and capture.captured_at < match.kickoff_utc:
            candidates = [(item.get("probabilities", {}).get("original"), item.get("price"), item["market"])
                          for item in capture.payload.get("common") or []]
        valid = [(p, odds, market) for p, odds, market in candidates
                 if p is not None and p >= minimum and odds is not None and band[0] <= odds <= band[1]]
        if valid:
            p, odds, market = max(valid, key=lambda item: (item[0], item[2]))
            picks[match.id] = SimpleNamespace(match_id=match.id, market=market, model_probability=p, market_odds=odds,
                                              market_probability=None, market_probability_source="archivo_prepartido")
    return picks
