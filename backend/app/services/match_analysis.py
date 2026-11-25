"""Descriptive pre-match statistics: team form, line frequencies, H2H and coverage.

Everything here is descriptive. It reads finished matches strictly before a reference instant
(the analysed kickoff), never touches models or features, and reports sample sizes per metric:
a missing statistic is never counted as zero.
"""

import datetime as dt
from collections import defaultdict
from statistics import mean, median, pstdev

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from backend.app.db.models.matches import Match, MatchStatistics

WINDOWS = (5, 10, 20)
GOAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
TEAM_GOAL_LINES = (0.5, 1.5, 2.5)
CORNER_LINES = (6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5)
TEAM_CORNER_LINES = (2.5, 3.5, 4.5, 5.5, 6.5)
SHOT_LINES = (18.5, 20.5, 22.5, 24.5, 26.5, 28.5)
SOT_LINES = (5.5, 6.5, 7.5, 8.5, 9.5, 10.5)
# Per-match team view: metric -> (value for the team, value for the opponent) columns.
PAIRS = {
    "goals": ("goals", "goals"), "goals_ht": ("goals_ht", "goals_ht"), "corners": ("corners", "corners"),
    "shots": ("shots", "shots"), "shots_on_target": ("shots_on_target", "shots_on_target"),
    "possession": ("possession", "possession"),
}


def _side_value(match, side, metric):
    if metric == "goals":
        return getattr(match, f"{side}_goals")
    if metric == "goals_ht":
        return getattr(match, f"{side}_goals_ht")
    stats = match.statistics
    if stats is None:
        return None
    value = getattr(stats, f"{side}_{metric}")
    # Provider placeholders (negative counts) are missing data, never real values.
    return value if value is None or value >= 0 else None


def team_row(match, team_id):
    """One finished match seen from one team."""
    side, other = ("home", "away") if match.home_team_id == team_id else ("away", "home")
    row = dict(
        match_id=match.id, kickoff=match.kickoff_utc.isoformat() + "Z", competition=match.competition.code,
        competition_name=match.competition.name, season_id=match.season_id, venue=side,
        opponent_id=getattr(match, f"{other}_team_id"),
        opponent=getattr(match, f"{other}_team").canonical_name,
        opponent_logo=getattr(match, f"{other}_team").logo_url,
    )
    for metric in PAIRS:
        row[f"{metric}_for"] = _side_value(match, side, metric)
        row[f"{metric}_against"] = _side_value(match, other, metric)
    gf, ga = row["goals_for"], row["goals_against"]
    row["result"] = None if gf is None or ga is None else "W" if gf > ga else "D" if gf == ga else "L"
    return row


def finished_matches(db, team_ids, before, *, since=None):
    """All finished matches of `team_ids` that kicked off strictly before `before`, newest first."""
    query = db.query(Match).options(
        joinedload(Match.statistics), joinedload(Match.competition),
        joinedload(Match.home_team), joinedload(Match.away_team),
    ).filter(
        Match.status == "finished", Match.kickoff_utc < before,
        Match.home_goals.isnot(None), Match.away_goals.isnot(None),
        or_(Match.home_team_id.in_(team_ids), Match.away_team_id.in_(team_ids)),
    )
    if since is not None:
        query = query.filter(Match.kickoff_utc >= since)
    return query.order_by(Match.kickoff_utc.desc(), Match.id.desc()).all()


def select_rows(rows, *, window=None, venue=None, competition=None, season_id=None):
    picked = [row for row in rows
              if (venue is None or row["venue"] == venue)
              and (competition is None or row["competition"] == competition)
              and (season_id is None or row["season_id"] == season_id)]
    return picked[:window] if window else picked


def describe(values):
    """Mean/median/std/min/max/distribution over the values that exist; n is their real count."""
    present = [v for v in values if v is not None]
    if not present:
        return dict(n=0, mean=None, median=None, std=None, min=None, max=None, distribution={})
    distribution = defaultdict(int)
    for value in present:
        distribution[int(round(value))] += 1
    return dict(
        n=len(present), mean=round(mean(present), 3), median=median(present),
        std=round(pstdev(present), 3) if len(present) > 1 else 0., min=min(present), max=max(present),
        distribution=dict(sorted(distribution.items())),
    )


def line_frequencies(values, lines):
    present = [v for v in values if v is not None]
    return {
        str(line): dict(over=sum(v > line for v in present), n=len(present),
                        rate=round(sum(v > line for v in present) / len(present), 4) if present else None)
        for line in lines
    }


def _rate(flags):
    present = [f for f in flags if f is not None]
    return dict(count=sum(present), n=len(present), rate=round(sum(present) / len(present), 4) if present else None)


def _total(row, metric):
    a, b = row[f"{metric}_for"], row[f"{metric}_against"]
    return None if a is None or b is None else a + b


def profile(rows):
    """Aggregates for one team sample (already filtered and windowed)."""
    gf = [r["goals_for"] for r in rows]
    ga = [r["goals_against"] for r in rows]
    second_half = [None if r["goals_for"] is None or r["goals_ht_for"] is None else r["goals_for"] - r["goals_ht_for"]
                   for r in rows]
    out = dict(
        matches=len(rows),
        record=dict(W=sum(r["result"] == "W" for r in rows), D=sum(r["result"] == "D" for r in rows),
                    L=sum(r["result"] == "L" for r in rows)),
        clean_sheets=_rate([None if v is None else v == 0 for v in ga]),
        scored=_rate([None if v is None else v > 0 for v in gf]),
        conceded=_rate([None if v is None else v > 0 for v in ga]),
        btts=_rate([None if a is None or b is None else a > 0 and b > 0 for a, b in zip(gf, ga)]),
        goals_for=describe(gf), goals_against=describe(ga),
        goals_total=describe([_total(r, "goals") for r in rows]),
        goals_ht_for=describe([r["goals_ht_for"] for r in rows]),
        goals_ht_total=describe([_total(r, "goals_ht") for r in rows]),
        goals_2h_for=describe(second_half),
        goals_lines=line_frequencies([_total(r, "goals") for r in rows], GOAL_LINES),
        team_goals_lines=line_frequencies(gf, TEAM_GOAL_LINES),
        corners_for=describe([r["corners_for"] for r in rows]),
        corners_against=describe([r["corners_against"] for r in rows]),
        corners_total=describe([_total(r, "corners") for r in rows]),
        corners_lines=line_frequencies([_total(r, "corners") for r in rows], CORNER_LINES),
        team_corners_lines=line_frequencies([r["corners_for"] for r in rows], TEAM_CORNER_LINES),
        shots_for=describe([r["shots_for"] for r in rows]), shots_against=describe([r["shots_against"] for r in rows]),
        shots_total=describe([_total(r, "shots") for r in rows]),
        shots_lines=line_frequencies([_total(r, "shots") for r in rows], SHOT_LINES),
        shots_on_target_for=describe([r["shots_on_target_for"] for r in rows]),
        shots_on_target_against=describe([r["shots_on_target_against"] for r in rows]),
        shots_on_target_total=describe([_total(r, "shots_on_target") for r in rows]),
        shots_on_target_lines=line_frequencies([_total(r, "shots_on_target") for r in rows], SOT_LINES),
        shots_off_target_for=describe([None if r["shots_for"] is None or r["shots_on_target_for"] is None
                                       else r["shots_for"] - r["shots_on_target_for"] for r in rows]),
        possession=describe([r["possession_for"] for r in rows]),
    )
    shots = sum(r["shots_for"] for r in rows if r["shots_for"] is not None and r["goals_for"] is not None)
    goals = sum(r["goals_for"] for r in rows if r["shots_for"] is not None and r["goals_for"] is not None)
    sot = sum(r["shots_on_target_for"] for r in rows if r["shots_on_target_for"] is not None and r["goals_for"] is not None)
    goals_sot = sum(r["goals_for"] for r in rows if r["shots_on_target_for"] is not None and r["goals_for"] is not None)
    out["conversion"] = dict(goals_per_shot=round(goals / shots, 4) if shots else None,
                             goals_per_shot_on_target=round(goals_sot / sot, 4) if sot else None,
                             n=sum(r["shots_for"] is not None and r["goals_for"] is not None for r in rows))
    return out


def team_analysis(rows, *, window, venue=None, competition=None, season_id=None):
    sample = select_rows(rows, window=window, venue=venue, competition=competition, season_id=season_id)
    return dict(filters=dict(window=window, venue=venue, competition=competition, season_id=season_id),
                requested=window, profile=profile(sample), matches=sample)


def head_to_head(rows, opponent_id, limit=10, now=None):
    """Previous meetings, newest first, from the home team's rows; each with its age in days."""
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    meetings = [row for row in rows if row["opponent_id"] == opponent_id][:limit]
    return dict(
        matches=[dict(row, age_days=(now - dt.datetime.fromisoformat(row["kickoff"].rstrip("Z"))).days) for row in meetings],
        profile=profile(meetings),
    )


def coverage(rows):
    """Share of the sample with each statistic actually recorded."""
    n = len(rows)
    return {metric: dict(n=sum(row[f"{metric}_for"] is not None for row in rows), of=n)
            for metric in ("goals", "goals_ht", "corners", "shots", "shots_on_target", "possession")}


def statistics_coverage(db):
    """Per competition and season: finished matches and how many carry each statistic."""
    from sqlalchemy import func

    from backend.app.db.models.core import Competition, Season

    def both(field):
        return func.count(MatchStatistics.id).filter(
            getattr(MatchStatistics, f"home_{field}").isnot(None), getattr(MatchStatistics, f"away_{field}").isnot(None))

    def partial(field):
        home, away = (getattr(MatchStatistics, f"{side}_{field}") for side in ("home", "away"))
        return func.count(MatchStatistics.id).filter(or_(home.is_(None), away.is_(None)), or_(home.isnot(None), away.isnot(None)))

    rows = db.query(
        Competition.code, Competition.name, Season.label, func.count(Match.id),
        func.count(Match.id).filter(Match.home_goals_ht.isnot(None)),
        both("corners"), both("shots"), both("shots_on_target"), both("possession"),
        partial("shots"), partial("shots_on_target"),
    ).join(Competition, Competition.id == Match.competition_id).join(Season, Season.id == Match.season_id) \
        .outerjoin(MatchStatistics, MatchStatistics.match_id == Match.id) \
        .filter(Match.status == "finished").group_by(Competition.code, Competition.name, Season.label) \
        .order_by(Competition.code, Season.label).all()
    return [dict(competition=code, name=name, season=season, finished=total, halftime=ht, corners=corners,
                 shots=shots, shots_on_target=sot, possession=poss, shots_partial=sp, shots_on_target_partial=sotp)
            for code, name, season, total, ht, corners, shots, sot, poss, sp, sotp in rows]
