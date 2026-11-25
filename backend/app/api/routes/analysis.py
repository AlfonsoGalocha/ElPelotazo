"""Football intelligence endpoints: match analysis, odds centre, team profiles and the explorer.

Read-only and descriptive. Every pre-match sample ends strictly before the analysed kickoff,
responses are cached briefly, and nothing here calls API-Football.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from statistics import median
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from backend.app.db.database import SessionLocal, get_db
from backend.app.db.models.core import Competition, Team
from backend.app.db.models.matches import Match, MatchOdds, MatchOddsHistory
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.db.models.publication import PreMatchForecast
from backend.app.db.models.standings import StandingsSnapshot
from backend.app.normalization.bookmakers import is_excluded_bookmaker
from backend.app.services import match_analysis as ma
from backend.app.services.response_cache import cached

router = APIRouter(prefix="/analysis", tags=["analysis"])
HISTORY_SPAN = dt.timedelta(days=4 * 365)
Venue = Literal["split", "all"]
Scope = Literal["all", "competition"]
Season = Literal["all", "current"]


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _session_cached(key, build, fresh=300):
    """Cache a response computed in its own session (shared safely across requests)."""
    def compute():
        session = SessionLocal()
        try:
            return build(session)
        finally:
            session.rollback()
            session.close()
    value, _ = cached(key, compute, fresh_seconds=fresh, max_stale_seconds=fresh * 4)
    return value


def _team(team):
    return dict(id=team.id, name=team.canonical_name, logo_url=team.logo_url)


def _standing(db, match, team_id):
    row = db.query(StandingsSnapshot).filter_by(
        competition_id=match.competition_id, season_id=match.season_id, team_id=team_id,
    ).filter(StandingsSnapshot.as_of <= match.kickoff_utc).order_by(
        StandingsSnapshot.as_of.desc(), StandingsSnapshot.id.desc()).first()
    if row is None:
        return None
    return dict(position=row.position, points=row.points, played=row.played, won=row.won, drawn=row.drawn,
                lost=row.lost, goals_for=row.goals_for, goals_against=row.goals_against,
                as_of=row.as_of.isoformat() + "Z")


def model_probabilities(db, match):
    """Latest stored forecast per market made before kickoff, or the archived pre-match preview."""
    rows = db.query(Prediction, ModelVersion).outerjoin(ModelVersion, ModelVersion.id == Prediction.model_version_id) \
        .filter(Prediction.match_id == match.id, Prediction.created_at < match.kickoff_utc) \
        .order_by(Prediction.created_at.desc(), Prediction.id.desc()).all()
    out = {}
    for prediction, version in rows:
        if prediction.market in out:
            continue
        out[prediction.market] = dict(
            original=prediction.model_probability, calibrated=prediction.calibrated_probability,
            uncertainty=[prediction.uncertainty_low, prediction.uncertainty_high],
            model_version_id=prediction.model_version_id, model_name=version.name if version else None,
            created_at=prediction.created_at.isoformat() + "Z", source="prediccion_guardada",
        )
    if out:
        return out
    from backend.app.services.response_cache import peek

    preview = peek(("match-preview", match.id)) if match.status == "scheduled" else None
    for row in (preview or {}).get("markets", []) if isinstance(preview, dict) else []:
        # On-the-fly forecast already computed for the predictions tab; reused, never recomputed here.
        out[row["market"]] = dict(original=row.get("original_probability"), calibrated=row.get("calibrated_probability"),
                                  uncertainty=None, model_version_id=preview.get("model_version_id"), model_name=None,
                                  created_at=preview.get("evaluated_at"), source="prevision_al_vuelo")
    if out:
        return out
    archived = db.get(PreMatchForecast, match.id)
    if archived is not None and archived.captured_at < match.kickoff_utc:
        for market, details in (archived.payload.get("markets") or {}).items():
            if details.get("probability") is not None:
                out[market] = dict(original=details.get("original_probability"), calibrated=details.get("probability"),
                                   uncertainty=None, model_version_id=None, model_name=None,
                                   created_at=archived.captured_at.isoformat() + "Z", source="prevision_archivada")
    return out


def market_consensus(match, now):
    """Median current pre-match price per (market, line, selection) and its implied probability."""
    groups = defaultdict(list)
    for quote in match.odds:
        if quote.snapshot_type == "pre_match" and quote.price > 1 and not is_excluded_bookmaker(quote.bookmaker):
            groups[(quote.market, quote.line, quote.selection)].append(quote)
    out = {}
    for key, quotes in groups.items():
        price = median(q.price for q in quotes)
        out[key] = dict(price=round(price, 4), implied=round(1 / price, 4), bookmakers=len(quotes),
                        best=max(q.price for q in quotes))
    return out


def _market_key_lookup():
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP
    return ALL_MARKET_TO_ODDS_LOOKUP


def build_match_analysis(db, match_id, window, venue, scope, season):
    match = db.query(Match).options(
        joinedload(Match.statistics), joinedload(Match.competition), joinedload(Match.home_team),
        joinedload(Match.away_team), joinedload(Match.odds),
    ).filter(Match.id == match_id).one_or_none()
    if match is None:
        return None
    before = match.kickoff_utc
    history = ma.finished_matches(db, [match.home_team_id, match.away_team_id], before, since=before - HISTORY_SPAN)
    rows = {team: [ma.team_row(m, team) for m in history if team in (m.home_team_id, m.away_team_id)]
            for team in (match.home_team_id, match.away_team_id)}
    competition = match.competition.code if scope == "competition" else None
    season_id = match.season_id if season == "current" else None
    sides = {}
    for side, team_id in (("home", match.home_team_id), ("away", match.away_team_id)):
        team_rows = rows[team_id]
        sides[side] = dict(
            team=_team(match.home_team if side == "home" else match.away_team),
            standing=_standing(db, match, team_id),
            form=[r["result"] for r in team_rows[:5]],
            analysis=ma.team_analysis(team_rows, window=window, venue=side if venue == "split" else None,
                                      competition=competition, season_id=season_id),
            coverage=ma.coverage(ma.select_rows(team_rows, window=window, venue=side if venue == "split" else None,
                                                competition=competition, season_id=season_id)),
        )
    consensus = market_consensus(match, _now())
    lookup = _market_key_lookup()
    models = model_probabilities(db, match)
    from backend.app.services.evaluation_service import market_track_records_with_provenance

    evidence = market_track_records_with_provenance(db, as_of=before)
    markets = {}
    for market, key in lookup.items():
        quote = consensus.get(key)
        model = models.get(market)
        if quote or model:
            record = evidence.get((match.competition.code, market))
            markets[market] = dict(model=model, market=quote, evidence=dict(
                source_level=record.source_level, n_observations=record.n_observations, value=round(record.value, 4),
            ) if record else None)
    final = None
    if match.status == "finished":
        stats = match.statistics
        final = dict(home_goals=match.home_goals, away_goals=match.away_goals, home_goals_ht=match.home_goals_ht,
                     away_goals_ht=match.away_goals_ht,
                     **({f"{side}_{m}": getattr(stats, f"{side}_{m}") for side in ("home", "away")
                         for m in ("corners", "shots", "shots_on_target", "possession")} if stats else {}))
    return dict(
        match=dict(id=match.id, kickoff=match.kickoff_utc.isoformat() + "Z", status=match.status,
                   competition=dict(code=match.competition.code, name=match.competition.name,
                                    country=match.competition.country),
                   matchday=match.matchday, round_name=match.round_name,
                   home_goals=match.home_goals, away_goals=match.away_goals),
        filters=dict(window=window, venue=venue, scope=scope, season=season),
        temporal_note="Muestras prepartido: solo partidos terminados antes del inicio de este encuentro.",
        home=sides["home"], away=sides["away"],
        h2h=ma.head_to_head(rows[match.home_team_id], match.away_team_id, now=before),
        markets=markets, final=final,
    )


@router.get("/matches/{match_id}")
def match_analysis(
    match_id: int, window: int = Query(10, ge=1, le=60), venue: Venue = "split", scope: Scope = "all",
    season: Season = "all",
):
    payload = _session_cached(("analysis", match_id, window, venue, scope, season),
                              lambda db: build_match_analysis(db, match_id, window, venue, scope, season))
    if payload is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    return payload


def _freshness(origin, fetched, now):
    if fetched is None:
        return "sin_fecha"
    age = (now - fetched).total_seconds() / 60
    return "vigente" if age <= 90 else "reciente" if age <= 360 else "antigua"


NO_VIG_GROUPS = {
    "match_result": ("home", "draw", "away"), "over_under_goals": ("over", "under"), "btts": ("yes", "no"),
    "corners_total": ("over", "under"), "home_team_total_goals": ("over", "under"),
    "away_team_total_goals": ("over", "under"),
}


def build_match_odds(db, match_id):
    match = db.get(Match, match_id)
    if match is None:
        return None
    now = _now()
    current = [q for q in db.query(MatchOdds).filter(MatchOdds.match_id == match_id, MatchOdds.snapshot_type == "pre_match")
               if not is_excluded_bookmaker(q.bookmaker) and q.price > 1]
    history = db.query(MatchOddsHistory.market, MatchOddsHistory.line, MatchOddsHistory.selection,
                       MatchOddsHistory.bookmaker, MatchOddsHistory.price, MatchOddsHistory.recorded_at,
                       MatchOddsHistory.provider_updated_at).filter(
        MatchOddsHistory.match_id == match_id, MatchOddsHistory.snapshot_type == "pre_match",
        MatchOddsHistory.recorded_at < match.kickoff_utc,
    ).order_by(MatchOddsHistory.recorded_at, MatchOddsHistory.id).all()
    series = defaultdict(list)  # key -> [(at, bookmaker, price)] keeping only price changes per bookmaker
    first = {}
    last_price = {}
    for market, line, selection, bookmaker, price, recorded_at, origin in history:
        if is_excluded_bookmaker(bookmaker) or price <= 1:
            continue
        key = (market, line, selection)
        first.setdefault(key, dict(price=price, bookmaker=bookmaker, recorded_at=recorded_at.isoformat() + "Z",
                                   origin_at=origin.isoformat() + "Z" if origin else None))
        if last_price.get((key, bookmaker)) != price:
            last_price[(key, bookmaker)] = price
            series[key].append(dict(at=recorded_at.isoformat() + "Z", bookmaker=bookmaker, price=price))
    lookup = {value: market for market, value in _market_key_lookup().items()}
    models = model_probabilities(db, match)
    by_key = defaultdict(list)
    for quote in current:
        by_key[(quote.market, quote.line, quote.selection)].append(quote)
    consensus = {key: median(q.price for q in quotes) for key, quotes in by_key.items()}
    rows = []
    for key in sorted(set(by_key) | set(first), key=lambda k: (k[0], k[1] if k[1] is not None else -1, k[2])):
        market, line, selection = key
        quotes = by_key.get(key, [])
        price = consensus.get(key)
        fair = None
        group = NO_VIG_GROUPS.get(market)
        if price and group and selection in group:
            prices = [consensus.get((market, line, sel)) for sel in group]
            if all(prices):
                fair = round((1 / price) / sum(1 / p for p in prices), 4)
        model_key = lookup.get(key)
        model = models.get(model_key) if model_key else None
        model_p = model and (model["calibrated"] if model["calibrated"] is not None else model["original"])
        rows.append(dict(
            market=market, line=line, selection=selection, model_market=model_key,
            consensus=round(price, 4) if price else None, implied=round(1 / price, 4) if price else None,
            no_vig=fair, model_probability=model_p,
            model_gap=round(model_p - (fair or 1 / price), 4) if model_p is not None and price else None,
            first=first.get(key),
            bookmakers=[dict(bookmaker=q.bookmaker, price=q.price,
                             origin_at=q.provider_updated_at.isoformat() + "Z" if q.provider_updated_at else None,
                             fetched_at=q.fetched_at.isoformat() + "Z" if q.fetched_at else None,
                             freshness=_freshness(q.provider_updated_at, q.fetched_at, now))
                        for q in sorted(quotes, key=lambda q: -q.price)],
            series=series.get(key, [])[-200:],
        ))
    return dict(match_id=match_id, kickoff=match.kickoff_utc.isoformat() + "Z", status=match.status,
                note=("«Origen» es la última actualización del precio según el proveedor; «descarga» es cuándo lo "
                      "consultamos. Tras el inicio no hay cuotas prepartido vigentes."),
                rows=rows)


@router.get("/matches/{match_id}/odds")
def match_odds(match_id: int):
    payload = _session_cached(("analysis-odds", match_id), lambda db: build_match_odds(db, match_id), fresh=180)
    if payload is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    return payload


def build_team_profile(db, team_id, window, competition):
    team = db.get(Team, team_id)
    if team is None:
        return None
    now = _now()
    history = ma.finished_matches(db, [team_id], now, since=now - HISTORY_SPAN)
    rows = [ma.team_row(m, team_id) for m in history]
    competitions = sorted({(r["competition"], r["competition_name"]) for r in rows[:60]})
    upcoming = db.query(Match).options(joinedload(Match.competition), joinedload(Match.home_team),
                                       joinedload(Match.away_team)).filter(
        Match.status == "scheduled", Match.kickoff_utc > now,
        or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
    ).order_by(Match.kickoff_utc).limit(6).all()
    standings = []
    for code, name in competitions:
        latest = db.query(StandingsSnapshot).join(Competition, Competition.id == StandingsSnapshot.competition_id) \
            .filter(Competition.code == code, StandingsSnapshot.team_id == team_id) \
            .order_by(StandingsSnapshot.as_of.desc(), StandingsSnapshot.id.desc()).first()
        if latest:
            standings.append(dict(competition=code, name=name, position=latest.position, points=latest.points,
                                  played=latest.played, as_of=latest.as_of.isoformat() + "Z"))
    return dict(
        team=_team(team), competitions=[dict(code=c, name=n) for c, n in competitions], standings=standings,
        filters=dict(window=window, competition=competition),
        overall=ma.team_analysis(rows, window=window, competition=competition),
        home=ma.team_analysis(rows, window=window, venue="home", competition=competition),
        away=ma.team_analysis(rows, window=window, venue="away", competition=competition),
        recent=ma.select_rows(rows, window=40, competition=competition),
        upcoming=[dict(id=m.id, kickoff=m.kickoff_utc.isoformat() + "Z", competition=m.competition.name,
                       home=_team(m.home_team), away=_team(m.away_team)) for m in upcoming],
    )


@router.get("/teams/{team_id}")
def team_profile(team_id: int, window: int = Query(10, ge=1, le=60), competition: str | None = None):
    payload = _session_cached(("team", team_id, window, competition),
                              lambda db: build_team_profile(db, team_id, window, competition))
    if payload is None:
        raise HTTPException(status_code=404, detail="Equipo no encontrado")
    return payload


def build_team_directory(db):
    since = _now() - dt.timedelta(days=400)
    counts = defaultdict(lambda: defaultdict(int))
    for home, away, code in db.query(Match.home_team_id, Match.away_team_id, Competition.code).join(
            Competition, Competition.id == Match.competition_id).filter(Match.kickoff_utc >= since):
        counts[home][code] += 1
        counts[away][code] += 1
    names = dict(db.query(Competition.code, Competition.name))
    teams = db.query(Team).filter(Team.id.in_(list(counts))).all()
    return sorted([
        dict(id=t.id, name=t.canonical_name, logo_url=t.logo_url,
             competitions=[dict(code=c, name=names.get(c, c)) for c, _ in sorted(counts[t.id].items(), key=lambda kv: -kv[1])])
        for t in teams
    ], key=lambda t: t["name"].lower())


@router.get("/teams")
def team_directory():
    return _session_cached("team-directory", build_team_directory, fresh=1800)


# --- Statistical explorer ------------------------------------------------------------------------

FAMILY = {"goals": "goals", "corners": "corners", "shots": "shots", "shots_on_target": "shots_on_target"}
METRICS = {
    "goals_avg": ("Media de goles por partido", "goals", None),
    "over_1_5": ("Frecuencia Over 1,5 goles", "goals", 1.5),
    "over_2_5": ("Frecuencia Over 2,5 goles", "goals", 2.5),
    "btts": ("Frecuencia ambos marcan", None, None),
    "corners_avg": ("Media de córners por partido", "corners", None),
    "shots_avg": ("Media de tiros por partido", "shots", None),
    "shots_on_target_avg": ("Media de tiros a puerta por partido", "shots_on_target", None),
    "line": ("Frecuencia de una línea", None, None),
}


def _metric_value(rows, metric, family, line):
    if metric == "btts":
        flags = [None if r["goals_for"] is None or r["goals_against"] is None
                 else r["goals_for"] > 0 and r["goals_against"] > 0 for r in rows]
        present = [f for f in flags if f is not None]
        return (sum(present) / len(present) if present else None), len(present), sum(present)
    totals = [None if r[f"{family}_for"] is None or r[f"{family}_against"] is None
              else r[f"{family}_for"] + r[f"{family}_against"] for r in rows]
    present = [v for v in totals if v is not None]
    if not present:
        return None, 0, 0
    if line is None:
        return sum(present) / len(present), len(present), None
    over = sum(v > line for v in present)
    return over / len(present), len(present), over


def build_explorer(db, *, date_from, date_to, competition, team_id, metric, family, line, window, venue,
                   min_sample, order, market, scope="all"):
    start = dt.datetime.combine(date_from, dt.time.min)
    end = dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time.min)
    query = db.query(Match).options(joinedload(Match.competition), joinedload(Match.home_team),
                                    joinedload(Match.away_team)).filter(
        Match.status == "scheduled", Match.kickoff_utc >= max(start, _now()), Match.kickoff_utc < end)
    if competition:
        query = query.join(Competition, Competition.id == Match.competition_id).filter(Competition.code == competition)
    if team_id:
        query = query.filter(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))
    fixtures = query.order_by(Match.kickoff_utc).limit(400).all()
    if not fixtures:
        return dict(rows=[], kind="predictivo" if metric == "model" else "estadístico")
    team_ids = list({t for m in fixtures for t in (m.home_team_id, m.away_team_id)})
    # One query for every team: all fixtures here start after now, so every finished match precedes them.
    history = ma.finished_matches(db, team_ids, _now(), since=_now() - HISTORY_SPAN)
    rows_by_team = defaultdict(list)
    for m in history:
        for team in (m.home_team_id, m.away_team_id):
            if team in team_ids:
                rows_by_team[team].append(ma.team_row(m, team))
    probabilities = {}
    if metric == "model":
        latest = db.query(Prediction.match_id, Prediction.calibrated_probability, Prediction.model_probability) \
            .filter(Prediction.match_id.in_([m.id for m in fixtures]), Prediction.market == market) \
            .order_by(Prediction.created_at).all()
        for match_id, calibrated, original in latest:
            probabilities[match_id] = calibrated if calibrated is not None else original
    out = []
    for m in fixtures:
        home = ma.select_rows(rows_by_team[m.home_team_id], window=window, venue="home" if venue == "split" else None,
                              competition=m.competition.code if scope == "competition" else None)
        away = ma.select_rows(rows_by_team[m.away_team_id], window=window, venue="away" if venue == "split" else None,
                              competition=m.competition.code if scope == "competition" else None)
        if metric == "model":
            value = probabilities.get(m.id)
            hv = av = None
            hn, an = len(home), len(away)
            counts = None
        else:
            hv, hn, ho = _metric_value(home, metric, family, line)
            av, an, ao = _metric_value(away, metric, family, line)
            if hv is None or av is None:
                value = None
            elif metric.endswith("_avg"):
                value = (hv + av) / 2
            else:
                value = (ho + ao) / (hn + an)
            counts = None if ho is None or ao is None else dict(home=ho, away=ao)
        if value is None or min(hn, an) < min_sample:
            continue
        out.append(dict(
            match_id=m.id, kickoff=m.kickoff_utc.isoformat() + "Z",
            competition=dict(code=m.competition.code, name=m.competition.name),
            home=_team(m.home_team), away=_team(m.away_team), value=round(value, 4),
            home_value=round(hv, 4) if hv is not None else None, away_value=round(av, 4) if av is not None else None,
            sample=dict(home=hn, away=an, requested=window), counts=counts,
        ))
    out.sort(key=lambda row: row["value"], reverse=order == "desc")
    return dict(rows=out, kind="predictivo" if metric == "model" else "estadístico")


@router.get("/explorer")
def explorer(
    metric: Literal["goals_avg", "over_1_5", "over_2_5", "btts", "corners_avg", "shots_avg",
                    "shots_on_target_avg", "line", "model"] = "goals_avg",
    family: Literal["goals", "corners", "shots", "shots_on_target"] = "goals",
    line: float | None = Query(None, ge=0, le=60), market: str | None = None,
    date_from: dt.date | None = None, date_to: dt.date | None = None, competition: str | None = None,
    team_id: int | None = None, window: int = Query(10, ge=1, le=60), venue: Venue = "split",
    min_sample: int = Query(5, ge=0, le=60), order: Literal["asc", "desc"] = "desc", scope: Scope = "all",
):
    if metric == "line" and line is None:
        raise HTTPException(status_code=422, detail="Indica la línea a analizar")
    if metric == "model" and not market:
        raise HTTPException(status_code=422, detail="Indica el mercado del modelo")
    today = _now().date()
    date_from, date_to = date_from or today, date_to or today + dt.timedelta(days=3)
    resolved_family, resolved_line = (family, line) if metric == "line" else METRICS.get(metric, (None, None, None))[1:]
    key = ("explorer", metric, resolved_family, resolved_line, market, date_from, date_to, competition, team_id,
           window, venue, min_sample, order, scope)
    result = _session_cached(key, lambda db: build_explorer(
        db, date_from=date_from, date_to=date_to, competition=competition, team_id=team_id, metric=metric,
        family=resolved_family, line=resolved_line, window=window, venue=venue, min_sample=min_sample,
        order=order, market=market, scope=scope))
    return dict(result, metric=metric, label=METRICS.get(metric, ("Probabilidad del modelo",))[0] if metric != "model"
                else f"Probabilidad del modelo · {market}", family=resolved_family, line=resolved_line,
                window=window, venue=venue, min_sample=min_sample, order=order, scope=scope,
                date_from=date_from.isoformat(), date_to=date_to.isoformat())


@router.get("/coverage")
def coverage():
    return _session_cached("coverage", ma.statistics_coverage, fresh=3600)


def build_competition(db, code):
    competition = db.query(Competition).filter_by(code=code).one_or_none()
    if competition is None:
        return None
    now = _now()
    latest = db.query(StandingsSnapshot.season_id, func.max(StandingsSnapshot.as_of)).filter(
        StandingsSnapshot.competition_id == competition.id).group_by(StandingsSnapshot.season_id) \
        .order_by(func.max(StandingsSnapshot.as_of).desc()).first()
    table = []
    if latest:
        season_id, as_of = latest
        rows = db.query(StandingsSnapshot, Team).join(Team, Team.id == StandingsSnapshot.team_id).filter(
            StandingsSnapshot.competition_id == competition.id, StandingsSnapshot.season_id == season_id,
            StandingsSnapshot.as_of == as_of).order_by(StandingsSnapshot.position).all()
        table = [dict(team=_team(team), position=s.position, played=s.played, won=s.won, drawn=s.drawn, lost=s.lost,
                      goals_for=s.goals_for, goals_against=s.goals_against, goal_difference=s.goal_difference,
                      points=s.points, as_of=s.as_of.isoformat() + "Z") for s, team in rows]
    options = (joinedload(Match.home_team), joinedload(Match.away_team))
    upcoming = db.query(Match).options(*options).filter(
        Match.competition_id == competition.id, Match.status == "scheduled", Match.kickoff_utc > now,
    ).order_by(Match.kickoff_utc).limit(12).all()
    recent = db.query(Match).options(*options).filter(
        Match.competition_id == competition.id, Match.status == "finished",
    ).order_by(Match.kickoff_utc.desc()).limit(12).all()
    fixture = lambda m: dict(id=m.id, kickoff=m.kickoff_utc.isoformat() + "Z", home=_team(m.home_team), away=_team(m.away_team),
                             home_goals=m.home_goals, away_goals=m.away_goals, round=m.round_name or m.matchday)
    return dict(
        competition=dict(code=competition.code, name=competition.name, country=competition.country),
        standings=table, upcoming=[fixture(m) for m in upcoming], recent=[fixture(m) for m in recent],
        coverage=[row for row in ma.statistics_coverage(db) if row["competition"] == code],
    )


@router.get("/competitions/{code}")
def competition_overview(code: str):
    payload = _session_cached(("competition", code), lambda db: build_competition(db, code), fresh=600)
    if payload is None:
        raise HTTPException(status_code=404, detail="Competición no encontrada")
    return payload


STRATEGY_RULE = ("Probabilidad ≥ 65 %, cuota vigente entre 1,30 y 1,80 y EV positivo; ordenadas por la puntuación del ranking. "
                 "Si cumplen menos de 3, se completa con las más probables a cuota entre 1,40 y 2,50.")
FILL_ODDS = (1.40, 2.50)


def strategy_picks_from(markets, match=None, limit=3):
    """Top selections by the verified-opportunity rule; near misses fill the list, with their reasons."""
    from backend.app.services.publication_service import selection_outcome

    rows = []
    for market, d in (markets or {}).items():
        probability = d.get("probability")
        if probability is None:
            continue
        odds = d.get("odds")
        if odds is None and d.get("ev") is not None and probability:
            odds = round((d["ev"] + 1) / probability, 3)
        outcome = None
        if match is not None and match.status == "finished":
            corners = None
            if match.statistics and match.statistics.home_corners is not None and match.statistics.away_corners is not None:
                corners = match.statistics.home_corners + match.statistics.away_corners
            outcome = selection_outcome(market, match.home_goals, match.away_goals, corners)
        rows.append(dict(
            market=market, probability=probability, original_probability=d.get("original_probability"),
            odds=odds, bookmaker=d.get("bookmaker"), ev=d.get("ev"), score=d.get("score"),
            eligible=not d.get("reasons"), reasons=list(d.get("reasons") or []),
            confidence_reasons=list(d.get("confidence_reasons") or []), is_correct=outcome,
        ))
    eligible = sorted([r for r in rows if r["eligible"]],
                      key=lambda r: (not r["confidence_reasons"], r["score"] or 0, r["probability"]), reverse=True)
    # Fillers: the most likely selections priced between 1.40 and 2.50, even if they fail the strategy.
    near = sorted([r for r in rows if not r["eligible"] and r["odds"] is not None
                   and FILL_ODDS[0] <= r["odds"] <= FILL_ODDS[1]],
                  key=lambda r: -r["probability"])
    return dict(rule=STRATEGY_RULE, eligible_count=len(eligible), picks=(eligible + near)[:limit])


def build_strategy_picks(db, match_id):
    from sqlalchemy.orm import selectinload

    from backend.app.services.today_preview import build_today_previews

    match = db.query(Match).options(
        selectinload(Match.competition), selectinload(Match.statistics),
        selectinload(Match.odds).selectinload(MatchOdds.archived_quote),
    ).filter(Match.id == match_id).one_or_none()
    if match is None:
        return None
    now = _now()
    if match.status == "scheduled" and now < match.kickoff_utc:
        preview = build_today_previews(db, [match], now).get(match.id) or {}
        return dict(source="en_vivo", evaluated_at=now.isoformat() + "Z", **strategy_picks_from(preview.get("markets"), match))
    archived = db.get(PreMatchForecast, match.id)
    if archived is not None and archived.captured_at < match.kickoff_utc:
        return dict(source="archivo_prepartido", evaluated_at=archived.captured_at.isoformat() + "Z",
                    **strategy_picks_from(archived.payload.get("markets"), match))
    return dict(source="sin_datos", evaluated_at=None, rule=STRATEGY_RULE, eligible_count=0, picks=[])


@router.get("/matches/{match_id}/strategy-picks")
def strategy_picks(match_id: int):
    payload = _session_cached(("strategy-picks", match_id), lambda db: build_strategy_picks(db, match_id), fresh=300)
    if payload is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    return payload
