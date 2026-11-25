"""Pre-match evidence and market order for the bot's selection policy."""

from math import isfinite
from statistics import median

from sqlalchemy import or_

from backend.app.db.models.matches import Match
from backend.app.db.models.standings import StandingsSnapshot
from backend.app.normalization.bookmakers import is_excluded_bookmaker
from backend.app.prediction.opportunities import confirmed_quote_age, forecast_priority, valid_probability


OUTSIDER_GOAL_BAND = (1.40, 1.50)
TEAM_SCORE_SIDE = {"home_team_over_0_5": "home", "away_team_over_0_5": "away"}

def _team_form(results, team_id):
    recent = [row for row in results if team_id in (row.home_team_id, row.away_team_id)][:5]
    scores = [
        (row.home_goals, row.away_goals) if row.home_team_id == team_id
        else (row.away_goals, row.home_goals)
        for row in recent
    ]
    return dict(
        matches=len(scores), wins=sum(a > b for a, b in scores),
        draws=sum(a == b for a, b in scores), losses=sum(a < b for a, b in scores),
        scored=sum(a > 0 for a, _ in scores), conceded=sum(b > 0 for _, b in scores),
        goals_for=sum(a for a, _ in scores), goals_against=sum(b for _, b in scores),
        fixture_ids=[row.id for row in recent],
    )


def match_context(db, match, now, predictions):
    teams = (match.home_team_id, match.away_team_id)
    results = db.query(Match).filter(
        Match.status == "finished", Match.kickoff_utc < now, Match.id != match.id,
        Match.home_goals >= 0, Match.away_goals >= 0,
        or_(Match.home_team_id.in_(teams), Match.away_team_id.in_(teams)),
    ).order_by(Match.kickoff_utc.desc(), Match.id.desc()).all()
    direct = [row for row in results if {row.home_team_id, row.away_team_id} == set(teams)][:5]
    standings = {}
    for team in teams:
        row = db.query(StandingsSnapshot).filter_by(
            competition_id=match.competition_id, season_id=match.season_id, team_id=team,
        ).filter(StandingsSnapshot.as_of <= now).order_by(
            StandingsSnapshot.as_of.desc(), StandingsSnapshot.id.desc()
        ).first()
        if row:
            standings[team] = dict(position=row.position, points=row.points, as_of=row.as_of.isoformat() + "Z")

    forms = [_team_form(results, team) for team in teams]
    sides = {p.market: p for p in predictions if p.market in ("home_win", "away_win")}
    favorite = None
    source = None
    if len(sides) == 2:
        home, away = (sides[key] for key in ("home_win", "away_win"))
        for field in ("market_probability", "model_probability"):
            h, a = getattr(home, field), getattr(away, field)
            if valid_probability(h) and valid_probability(a):
                favorite = "home" if h > a else "away" if a > h else None
                source = field
                break
    if favorite is None and all(team in standings for team in teams) and all(f["matches"] >= 3 for f in forms):
        h, a = (standings[team] for team in teams)
        recent_points = [(3 * f["wins"] + f["draws"]) / f["matches"] for f in forms]
        if h["position"] < a["position"] and recent_points[0] > recent_points[1]:
            favorite, source = "home", "standings_and_form"
        elif a["position"] < h["position"] and recent_points[1] > recent_points[0]:
            favorite, source = "away", "standings_and_form"

    return dict(
        home_form=forms[0], away_form=forms[1],
        home_standing=standings.get(teams[0]), away_standing=standings.get(teams[1]),
        h2h=dict(matches=len(direct), both_scored=sum(row.home_goals > 0 and row.away_goals > 0 for row in direct),
                 over_2_5=sum(row.home_goals + row.away_goals >= 3 for row in direct),
                 fixture_ids=[row.id for row in direct]),
        favorite=favorite, favorite_source=source,
    )



def team_win_prices(match, now, max_age_minutes):
    """Use the median archived and current 1X2 win price for each side."""
    prices = {"home": {}, "away": {}}
    for quote in match.odds:
        if (quote.match_id != match.id or quote.market != "match_result" or quote.line is not None
                or quote.snapshot_type != "pre_match" or quote.selection not in prices
                or is_excluded_bookmaker(quote.bookmaker) or not isfinite(quote.price) or quote.price <= 1):
            continue
        age = confirmed_quote_age(quote, now)
        if age is not None and age <= max_age_minutes:
            prices[quote.selection][quote.bookmaker] = quote.price
    return {side: median(quotes.values()) for side, quotes in prices.items() if quotes}

def choose_match_pick(qualified, context):
    goals = [pair for pair in qualified if pair[0].market in ("over_1_5", "over_2_5")]
    if goals:
        return max(goals, key=forecast_priority), "goles"
    h2h = context["h2h"]
    teams_scoring = all(
        form["matches"] >= 3 and form["scored"] * 2 > form["matches"]
        for form in (context["home_form"], context["away_form"])
    )
    if h2h["matches"] >= 3 and h2h["both_scored"] * 2 > h2h["matches"] and teams_scoring:
        both = next((pair for pair in qualified if pair[0].market == "btts"), None)
        if both:
            return both, "ambos_marcan"
    cover = {"home": "double_chance_1x", "away": "double_chance_x2"}.get(context["favorite"])
    if cover:
        pair = next((pair for pair in qualified if pair[0].market == cover), None)
        if pair:
            return pair, "doble_oportunidad"
    win_prices = context.get("team_win_prices", {})
    outsiders_to_score = [
        pair for pair in qualified
        if pair[0].market in TEAM_SCORE_SIDE
        and win_prices.get(TEAM_SCORE_SIDE[pair[0].market], 0) >= 4
        and OUTSIDER_GOAL_BAND[0] <= pair[1].quote.price <= OUTSIDER_GOAL_BAND[1]
    ]
    if outsiders_to_score:
        return max(outsiders_to_score, key=forecast_priority), "equipo_no_favorito_marca"
    return None, "pasar"
