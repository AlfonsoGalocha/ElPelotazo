"""Official fixture publication and its independent evaluation."""

import datetime as dt
from collections import defaultdict

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.app.db.models.matches import Match
from backend.app.db.models.publication import FixturePublication, FixtureRankingRevision
from backend.app.config.settings import get_settings
from backend.app.prediction.anomaly import compute_anomaly_flags
from backend.app.prediction.opportunities import assess_opportunity
from backend.app.prediction.ranking import (
    calibrated_probability_or_fallback,
    conservative_probability,
)

GROUPS = {
    "1X2": ("home_win", "draw", "away_win"),
    "Goles 2.5": ("over_2_5", "under_2_5"),
    "Córners 9.5": ("corners_over_9_5", "corners_under_9_5"),
}


def freeze(p, as_of):
    return {
        "market": p.market, "line": p.line, "selection": p.selection,
        "probability": p.model_probability, "calibrated": p.calibrated_probability,
        "conservative": conservative_probability(p), "odds": p.market_odds,
        "estimated_ev": calibrated_probability_or_fallback(p) * p.market_odds - 1 if p.market_odds else None,
        "conservative_ev": conservative_probability(p) * p.market_odds - 1 if p.market_odds else None,
        "confidence": p.confidence, "model_version_id": p.model_version_id,
        "feature_set_version": p.feature_set_version, "evidence": p.features_used,
        "odds_origin_at": p.odds_origin_at.isoformat() if p.odds_origin_at else None,
        "captured_at": as_of.isoformat(),
    }


def capture_publications(db, predictions, reliability, *, timestamp=None):
    """First successful generation in the final hour, once per fixture."""
    from backend.app.services.evaluation_service import market_track_records_with_provenance
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP
    from backend.app.services.ranking_config_service import get_opportunity_odds_band

    timestamp = timestamp or dt.datetime.utcnow()
    if not predictions:
        return
    by_match = defaultdict(list)
    for prediction in predictions:
        by_match[prediction.match_id].append(prediction)
    db.flush()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    provenance = None
    settings = get_settings()
    min_odds, max_odds = get_opportunity_odds_band(db, settings)
    settings = settings.model_copy(update={"min_opportunity_odds": min_odds, "max_opportunity_odds": max_odds})
    for match_id, rows in by_match.items():
        match = db.get(Match, match_id)
        remaining = match.kickoff_utc - timestamp
        if match.status != "scheduled" or not dt.timedelta() < remaining <= dt.timedelta(hours=1):
            continue
        rows = [p for p in rows if p.created_at <= timestamp]
        by_market = {p.market: p for p in rows}
        groups = {}
        for family, keys in GROUPS.items():
            if all(key in by_market for key in keys):
                alternatives = [freeze(by_market[key], timestamp) for key in keys]
                # The base distributions preserve mutually exclusive group semantics.
                principal = max(alternatives, key=lambda row: row["probability"])["market"]
                groups[family] = {"principal": principal, "alternatives": alternatives}
        if not groups:
            continue
        if provenance is None:
            provenance = market_track_records_with_provenance(db, as_of=timestamp)
        assessed = [(p, assess_opportunity(
            p, match.odds, ALL_MARKET_TO_ODDS_LOOKUP.get(p.market), settings, timestamp,
            provenance.get((match.competition.code, p.market)), compute_anomaly_flags(p, rows, settings),
        )) for p in rows]
        assessed.sort(key=lambda item: (item[1].score is not None, item[1].score or 0, -item[0].id), reverse=True)
        ranked_rows = []
        for position, (prediction, item) in enumerate(assessed, start=1):
            quote = item.quote
            ranked_rows.append({
                **freeze(prediction, timestamp), "position": position,
                "odds": quote.price if quote else None,
                "bookmaker": quote.bookmaker if quote else None,
                "quote_history_id": quote.history_quote_id if quote else None,
                "odds_origin_at": quote.provider_updated_at.isoformat() if quote else None,
                "odds_fetched_at": quote.fetched_at.isoformat() if quote else None,
                "odds_recorded_at": quote.recorded_at.isoformat() if quote else None,
                "estimated_ev": item.ev,
                "conservative_ev": item.conservative_ev,
                "opportunity_score": item.score,
                "reasons": list(item.reasons),
                "confidence_reasons": list(item.confidence_reasons),
                "high_confidence": item.high_confidence,
            })
        recommended = next((row for row in ranked_rows if not row["reasons"]), None)
        db.execute(insert(FixtureRankingRevision).values(
            match_id=match_id, captured_at=timestamp, scheduled_kickoff=match.kickoff_utc,
            ranking_version="execution-v2", ranking=ranked_rows,
        ).on_conflict_do_nothing(index_elements=["match_id", "captured_at"]))
        db.execute(insert(FixturePublication).values(
            match_id=match_id, captured_at=timestamp, scheduled_kickoff=match.kickoff_utc,
            ranking_version="execution-v2", groups=groups,
            ranking=ranked_rows,
            recommendation=recommended,
        ).on_conflict_do_nothing(index_elements=["match_id"]))


def publication_report(db, competition=None, market=None, model_version=None, min_probability=None,
                       matchday=None, outcome=None, max_probability=None):
    from math import log
    from backend.app.db.models.core import Competition
    from backend.app.prediction.eligibility import observed_corner_total

    query = db.query(FixturePublication, Match, Competition, observed_corner_total()).join(
        Match, Match.id == FixturePublication.match_id
    ).join(Competition, Competition.id == Match.competition_id)
    if competition:
        query = query.filter(Competition.code == competition)
    if matchday is not None:
        query = query.filter(Match.matchday == matchday)
    query = query.filter(FixturePublication.captured_at < Match.kickoff_utc).order_by(
        Match.kickoff_utc, FixturePublication.match_id
    )
    groups, history, picks = defaultdict(list), [], []
    for pub, match, league, observed in query.all():
        stats = match.statistics
        if observed is None and stats is not None and stats.home_corners is not None and stats.away_corners is not None:
            if min(stats.home_corners, stats.away_corners) >= 0:
                observed = stats.home_corners + stats.away_corners

        def grade(row):
            if match.status == "cancelled":
                return "void"
            if match.status != "finished" or match.kickoff_utc >= dt.datetime.utcnow():
                return "unsettled"
            result = selection_outcome(row["market"], match.home_goals, match.away_goals, observed)
            return "unverifiable" if result is None else "won" if result else "lost"

        common = {"match_id": match.id, "competition": league.code, "matchday": match.matchday,
                  "kickoff": match.kickoff_utc.isoformat(), "match": f"{match.home_team.canonical_name} – {match.away_team.canonical_name}",
                  "score": [match.home_goals, match.away_goals], "ranking_version": pub.ranking_version}
        for family, group in pub.groups.items():
            alternatives = group["alternatives"]
            principal = next(row for row in alternatives if row["market"] == group["principal"])
            state = grade(principal)
            if (market and market not in (family, principal["market"])) or (
                model_version is not None and principal["model_version_id"] != model_version
            ) or (min_probability is not None and principal["probability"] < min_probability) or (
                max_probability is not None and principal["probability"] > max_probability
            ) or (
                outcome and state != outcome
            ):
                continue
            history.append({**common, **principal, "family": family, "status": state, "alternatives": alternatives,
                            "probability_error": (principal["probability"] - (state == "won")) ** 2
                            if state in {"won", "lost"} else None})
            if state not in {"won", "lost"}:
                continue
            vector = alternatives if family == "1X2" else alternatives[:1]
            observations = [(row["probability"], grade(row) == "won") for row in vector]
            brier = sum((probability - actual) ** 2 for probability, actual in observations)
            loss = -sum(
                log(max(1e-15, probability if actual else 1-probability))
                for probability, actual in observations if family != "1X2" or actual
            )
            groups[family].append((common, principal["model_version_id"], state, observations, brier, loss))
        if pub.recommendation is not None:
            row = pub.recommendation
            state = grade(row)
            profit = row["odds"] - 1 if state == "won" else -1 if state == "lost" else 0
            picks.append({**common, **row, "status": state, "profit_units": profit})
    summaries = []
    for family, rows in groups.items():
        buckets = defaultdict(list)
        for _, _, _, observations, _, _ in rows:
            for probability, actual in observations:
                buckets[min(9, int(probability * 10))].append((probability, actual))
        calibration = [
            {"low": i / 10, "high": (i + 1) / 10, "n": len(values),
             "predicted": sum(p for p, _ in values) / len(values),
             "observed": sum(y for _, y in values) / len(values)}
            for i, values in sorted(buckets.items())
        ]
        segments = defaultdict(list)
        for context, version, state, _, brier, loss in rows:
            segments[(context["competition"], version)].append((state, brier, loss))
        summaries.append({
            "family": family, "n": len(rows), "wins": sum(row[2] == "won" for row in rows),
            "losses": sum(row[2] == "lost" for row in rows),
            "brier": sum(row[4] for row in rows) / len(rows),
            "log_loss": sum(row[5] for row in rows) / len(rows), "calibration": calibration,
            "segments": [
                {"competition": key[0], "model_version": key[1], "n": len(values),
                 "brier": sum(row[1] for row in values) / len(values),
                 "log_loss": sum(row[2] for row in values) / len(values)}
                for key, values in sorted(segments.items())
            ],
            "ece": sum(b["n"] * abs(b["predicted"] - b["observed"]) for b in calibration)
            / sum(b["n"] for b in calibration),
        })
    settled = [item for item in picks if item["status"] in {"won", "lost"}]
    running, peak, drawdown = 0.0, 0.0, 0.0
    for item in settled:
        running += item["profit_units"]
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
        item["cumulative_units"] = running
    breakdown = {}
    dimensions = {
        "competition": lambda item: item["competition"],
        "market": lambda item: item["market"],
        "matchday": lambda item: f"{item['competition']} · {item['matchday'] or '—'}",
        "odds": lambda item: "1,35–1,59" if item["odds"] < 1.60 else "1,60–1,90",
        "probability": lambda item: f"{min(9, int(item['probability'] * 10)) * 10}–{min(10, int(item['probability'] * 10)+1) * 10} %",
        "version": lambda item: item["ranking_version"],
    }
    for name, key_for in dimensions.items():
        by_key = defaultdict(list)
        for item in settled:
            by_key[key_for(item)].append(item)
        breakdown[name] = [
            {"label": key, "n": len(values), "wins": sum(item["status"] == "won" for item in values),
             "roi": sum(item["profit_units"] for item in values) / len(values)}
            for key, values in sorted(by_key.items())
        ]
    return {
        "cohort": "capturas_prepartido_desde_activacion",
        "metrics": summaries, "history": history[-200:][::-1],
        "recommendations": picks[-200:][::-1],
        "breakdown": breakdown,
        "performance": {"n": len(picks), "settled": len(settled), "wins": sum(p["status"] == "won" for p in picks),
                        "losses": sum(p["status"] == "lost" for p in picks), "void": sum(p["status"] == "void" for p in picks),
                        "profit_units": running, "roi": running / len(settled) if settled else None,
                        "drawdown_units": drawdown},
    }


def selection_outcome(market, home, away, corners):
    """Settle only the chosen selection from verified scores/counts."""
    import pandas as pd
    from backend.app.prediction.market_labels import MARKET_DEFINITIONS, label_for_market
    from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS

    if market in SECONDARY_MARKET_DEFINITIONS:
        if corners is None:
            return None
        spec = SECONDARY_MARKET_DEFINITIONS[market]
        return corners > spec.line if spec.kind == "over" else corners < spec.line
    if market not in MARKET_DEFINITIONS or home is None or away is None:
        return None
    return bool(label_for_market(pd.DataFrame({"home_goals": [home], "away_goals": [away]}), market).iloc[0])


def capture_due_publications(db):
    """Scan cached predictions independently of the slower provider refresh."""
    from backend.app.db.models.modeling import Prediction
    from backend.app.services.evaluation_service import market_track_records

    now = dt.datetime.utcnow()
    due = db.query(Prediction).join(Match, Match.id == Prediction.match_id).filter(
        Match.status == "scheduled",
        Match.kickoff_utc > now,
        Match.kickoff_utc <= now + dt.timedelta(hours=1),
        ~Match.id.in_(db.query(FixturePublication.match_id)),
    ).all()
    if due:
        capture_publications(db, due, market_track_records(db), timestamp=now)
    return len({prediction.match_id for prediction in due})
