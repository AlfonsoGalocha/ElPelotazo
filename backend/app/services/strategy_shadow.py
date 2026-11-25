"""Pre-kickoff comparisons with executable prices and independent market references."""

import datetime as dt
import math
import random
import statistics
from collections import defaultdict
from functools import lru_cache
from types import SimpleNamespace

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import joinedload

from backend.app.config.settings import get_settings
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.db.models.publication import StrategyCapture
from backend.app.models.calibration.coherent import RESULT, corrected_forecasts
from backend.app.normalization.bookmakers import is_excluded_bookmaker
from backend.app.prediction.eligibility import observed_corner_total
from backend.app.prediction.market_labels import DOUBLE_CHANCE_COMPONENTS
from backend.app.prediction.opportunities import confirmed_quote_age, ranking_score, valid_probability
from backend.app.services.publication_service import selection_outcome

from backend.app.normalization.competitions import API_CANDIDATE_COMPETITIONS, CONTINENTAL_CHAMPIONS

WEIGHTS = {"original": 1., "corregida": None, "mixta_80_20": .8, "mixta_65_35": .65,
           "mixta_50_50": .5, "mixta_35_65": .35, "mixta_20_80": .2, "mercado": 0.}
BANDS = {"principal": (1.35, 1.90), "1.20-1.34": (1.20, 1.35), "1.35-1.50": (1.35, 1.51),
         "1.51-1.70": (1.51, 1.71), "1.71-1.90": (1.71, 1.90), "1.20-1.90": (1.20, 1.90)}
LEAGUES = ("laliga", "premier_league", "bundesliga", "ligue_1", "serie_a", *API_CANDIDATE_COMPETITIONS)
# v2-2: quote currency follows the last confirmed download (as in the ranking), not the origin stamp.
SCHEMA_VERSION = "shadow-v2-2"


def in_band(price, name):
    lo, hi = BANDS[name]
    return lo <= price <= hi if hi == 1.90 else lo <= price < hi


def independent_reference(rows, market, lookup, quote):
    kind, line, side = lookup[market]
    if market in DOUBLE_CHANCE_COMPONENTS:
        kind, line = "match_result", None
        wanted = [lookup[m][2] for m in DOUBLE_CHANCE_COMPONENTS[market]]
    else:
        wanted = [side]
    sides = {"match_result": {"home", "draw", "away"}, "btts": {"yes", "no"}}.get(kind, {"over", "under"})
    by_house = defaultdict(dict)
    for row in rows:
        if (row.bookmaker != quote.bookmaker and (row.market, row.line) == (kind, line)
                and row.selection in sides
                and abs(row.provider_updated_at - quote.provider_updated_at) <= dt.timedelta(hours=1)):
            by_house[row.bookmaker][row.selection] = row
    values, origins = [], []
    for group in by_house.values():
        if group.keys() != sides:
            continue
        timestamps = [r.provider_updated_at for r in group.values()]
        if max(timestamps) - min(timestamps) > dt.timedelta(minutes=15):
            continue
        implied = {s: 1 / r.price for s, r in group.items()}
        values.append(sum(implied[s] for s in wanted) / sum(implied.values()))
        origins.extend(group.values())
    if len(values) < 2:
        return None
    return {
        "probability": statistics.mean(values), "houses": len(values),
        "quote_ids": sorted(r.history_quote_id for r in origins),
        "first_origin": min(r.provider_updated_at for r in origins).isoformat(),
        "last_origin": max(r.provider_updated_at for r in origins).isoformat(),
    }


def probabilities_for(original, corrected, reference):
    probabilities = {"original": original, "corregida": corrected}
    if reference:
        probabilities.update({
            name: weight * original + (1-weight) * reference["probability"]
            for name, weight in WEIGHTS.items() if name not in probabilities
        })
    return probabilities


def candidate_score(probability, price, data_quality, confidence, low, high, age, max_age):
    width_quality = 1 - (high-low) if valid_probability(low) and valid_probability(high) and low <= high else 0
    return ranking_score(probability, probability * price - 1,
                         confidence if valid_probability(confidence) else 0,
                         data_quality if valid_probability(data_quality) else 0,
                         max(0, 1-age/max_age) if max_age else float(age == 0),
                         width_quality)


def choose(candidates, strategy, band, minimum):
    ranked = []
    for r in candidates:
        p = r["probabilities"].get(strategy)
        if p is None or p < minimum or not in_band(r["price"], band) or p * r["price"] <= 1:
            continue
        score = r["scores"][strategy]
        ranked.append((score, p, p * r["price"], -r["quote_id"], r))
    if not ranked:
        return None
    score, probability, ev_plus_one, _, record = max(ranked, key=lambda a: a[:4])
    return {**record, "probability": probability, "ev": ev_plus_one-1, "score": score}


def capture_strategy_due(db, now=None):
    from backend.app.services.model_service import load_model_artifact
    from backend.app.services.prediction_service import (
        ALL_MARKET_TO_ODDS_LOOKUP as lookup, _market_line_and_selection, score_api_candidate, score_champions_candidate, shadow_result_forecasts,
    )

    cutoff = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    settings = get_settings()
    minimum = settings.shadow_min_probability
    max_age = settings.max_odds_age_minutes or settings.opportunity_max_odds_age_minutes

    @lru_cache(maxsize=None)
    def coherent_artifact(version):
        if (not version.artifact_path or not version.created_at or version.created_at > cutoff
                or not version.training_end or version.training_end > version.created_at.date()):
            return None
        try:
            artifact = load_model_artifact(version)
            return artifact if artifact.get("coherent_calibration", {}).get("temperature") is not None else None
        except (OSError, ValueError):
            return None

    @lru_cache(maxsize=None)
    def shadow_candidate(league):
        versions = db.query(ModelVersion).filter(
            ModelVersion.dataset_version.like(f"{league}:%"), ModelVersion.created_at <= cutoff,
            ModelVersion.is_active.is_(False),
        ).order_by(ModelVersion.created_at.desc()).limit(20)
        return next((version for version in versions if (version.metrics or {}).get(
            "joint_1x2", {}).get("reason") == "accepted" and coherent_artifact(version)), None)

    due = db.query(Match).join(Competition).filter(
        Competition.code.in_(LEAGUES), Match.status == "scheduled",
        Match.kickoff_utc > cutoff, Match.kickoff_utc <= cutoff + dt.timedelta(hours=1),
        ~Match.id.in_(db.query(StrategyCapture.match_id)),
    ).all()
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    captured = 0
    candidate_rows = defaultdict(dict)
    for code in API_CANDIDATE_COMPETITIONS:
        fixtures = [m for m in due if m.competition.code == code]
        if not fixtures:
            continue
        if code == "champions_league":
            candidate_version, outputs, _ = score_champions_candidate(db, fixtures, cutoff)
        else:
            candidate_version, outputs, _ = score_api_candidate(db, fixtures, cutoff, code)
        for row in outputs:
            if row.market not in lookup or not valid_probability(row.model_probability):
                continue
            line, selection = _market_line_and_selection(row.market)
            candidate_rows[row.match_id][row.market] = SimpleNamespace(
                id=None, created_at=cutoff, line=line, selection=selection,
                model_version=candidate_version, model_version_id=candidate_version.id,
                model_probability=row.model_probability, calibrated_probability=row.calibrated_probability,
                data_quality=row.data_quality, confidence=row.confidence, uncertainty_low=row.uncertainty_low,
                uncertainty_high=row.uncertainty_high, calibration_source=row.calibration_source,
            )
    for match in due:
        predictions = {}
        for p in db.query(Prediction).filter(
            Prediction.match_id == match.id, Prediction.created_at <= cutoff,
        ).options(joinedload(Prediction.model_version)).order_by(Prediction.created_at, Prediction.id):
            if p.market in lookup and valid_probability(p.model_probability):
                predictions[p.market] = p
        candidate_only = not predictions and bool(candidate_rows[match.id])
        if candidate_only:
            predictions = candidate_rows[match.id]
        raw = {m: p.model_probability for m, p in predictions.items()}
        marginals = {m: p.calibrated_probability for m, p in predictions.items()
                     if p.model_version.created_at <= cutoff and p.model_version.training_end
                     and p.model_version.training_end <= p.model_version.created_at.date()}
        result_versions = {predictions[m].model_version for m in RESULT if m in predictions}
        version = next(iter(result_versions)) if len(result_versions) == 1 else None
        artifact = coherent_artifact(version) if version else None
        correction_input = raw
        if artifact is None and all(m in raw for m in RESULT):
            candidate = shadow_candidate(match.competition.code)
            if candidate is not None:
                candidate_artifact = coherent_artifact(candidate)
                forecast = shadow_result_forecasts(db, match, candidate_artifact, cutoff)
                if all(m in forecast for m in RESULT):
                    correction_input = {**raw, **forecast}
                    artifact, version = candidate_artifact, candidate
        t = artifact["coherent_calibration"]["temperature"] if artifact else None
        corrected = ({
            market: (p.calibrated_probability if p.calibrated_probability is not None else p.model_probability,
                     p.calibration_source or "original_fallback")
            for market, p in predictions.items()
        } if candidate_only else corrected_forecasts(correction_input, marginals, t))
        quotes = [q for q in match.odds if q.snapshot_type == "pre_match" and not is_excluded_bookmaker(q.bookmaker)
                  and math.isfinite(q.price) and q.price > 1
                  and (age := confirmed_quote_age(q, cutoff)) is not None and age <= max_age]
        candidates = []
        for market, p in predictions.items():
            for quote in quotes:
                if (quote.market, quote.line, quote.selection) != lookup[market]:
                    continue
                reference = independent_reference(quotes, market, lookup, quote)
                corrected_p, source = corrected[market]
                probabilities = probabilities_for(raw[market], corrected_p, reference)
                age = confirmed_quote_age(quote, cutoff)
                scores = {name: candidate_score(prob, quote.price, p.data_quality, p.confidence, p.uncertainty_low,
                                                p.uncertainty_high, age, max_age)
                          for name, prob in probabilities.items()}
                candidates.append({
                    "market": market, "line": p.line, "selection": p.selection, "price": quote.price,
                    "bookmaker": quote.bookmaker, "quote_id": quote.history_quote_id,
                    "origin_at": quote.provider_updated_at.isoformat(), "fetched_at": quote.fetched_at.isoformat(),
                    "recorded_at": quote.recorded_at.isoformat(), "reference": reference,
                    "probabilities": probabilities, "scores": scores, "corrected_source": source,
                    "corrected_model_version_id": version.id if t and source in ("joint_temperature", "coherent_1x2") else p.model_version_id,
                    "prediction_id": p.id, "prediction_source": f"{match.competition.code.removesuffix('_league')}_candidate" if candidate_only else "stored_prediction",
                    "prediction_at": p.created_at.isoformat(), "model_version_id": p.model_version_id,
                })
        common = {}
        for row in sorted(candidates, key=lambda r: (-r["price"], r["quote_id"])):
            if row["reference"] and in_band(row["price"], "principal"):
                common.setdefault(row["market"], row)
        decisions = {f"{strategy}:{band}": choose(candidates, strategy, band, minimum)
                     for strategy in WEIGHTS for band in BANDS}
        passes = {
            key: ("selected" if selected else ("no_prediction" if not predictions else
                  "no_valid_quotes" if not candidates else
                  "no_independent_reference" if not any(key.split(":")[0] in row["probabilities"] for row in candidates)
                  else "no_eligible_market"))
            for key, selected in decisions.items()
        }
        reason = ("no_prediction" if not predictions else "no_valid_quotes" if not candidates else
                  "no_eligible_market" if not any(value is not None for value in decisions.values()) else "captured")
        # Give the regular refresh another chance when its inputs are absent.
        if reason in ("no_prediction", "no_valid_quotes") and match.kickoff_utc-cutoff > dt.timedelta(minutes=15):
            continue
        # Guard a long-running capture that crossed kickoff after querying the fixtures.
        if (now is None and dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) >= match.kickoff_utc):
            continue
        result = db.execute(insert(StrategyCapture).values(
            match_id=match.id, captured_at=cutoff,
            payload={"version": SCHEMA_VERSION, "scheduled_kickoff": match.kickoff_utc.isoformat(),
                     "minimum": minimum, "max_age_minutes": max_age, "universe": len(predictions),
                     "candidate_count": len(candidates), "bands": BANDS, "reason": reason,
                     "common": list(common.values()), "decisions": decisions, "decision_reasons": passes},
        ).on_conflict_do_nothing(index_elements=["match_id"]))
        captured += result.rowcount or 0
    return captured


def _probability_metrics(pairs):
    blocks = defaultdict(list)
    for p, outcome, kickoff in pairs:
        blocks[kickoff[:10]].append(((p-outcome)**2, -math.log(max(1e-12, p if outcome else 1-p))))
    values = [value for block in blocks.values() for value in block]
    interval = None
    if len(blocks) >= 5:
        rng = random.Random(42)
        samples = sorted(statistics.mean(v[0] for block in sample for v in block)
                         for _ in range(500) if (sample := rng.choices(list(blocks.values()), k=len(blocks))))
        interval = [samples[12], samples[-13]]
    return {
        "n": len(pairs), "date_blocks": len(blocks),
        "brier": statistics.mean(v[0] for v in values) if values else None,
        "log_loss": statistics.mean(v[1] for v in values) if values else None,
        "brier_interval": interval,
    }


def economics(rows):
    from decimal import Decimal, ROUND_HALF_UP

    settled = [r for r in rows if r["outcome"] in ("won", "lost")]
    running = peak = drawdown = 0
    curve = []
    daily = defaultdict(int)
    by_kickoff = defaultdict(int)
    for r in settled:
        returned = int((Decimal(str(r["price"])) * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if r["outcome"] == "won" else 0
        by_kickoff[r["kickoff"]] += returned-1000
        daily[r["kickoff"][:10]] += returned-1000
    for kickoff, value in sorted(by_kickoff.items()):
        running += value
        peak = max(peak, running)
        drawdown = max(drawdown, peak-running)
        curve.append({"at": kickoff, "profit_cents": running})
    n = len(settled)
    interval = None
    if len(daily) >= 5:
        rng = random.Random(42)
        blocks = [(daily[day], sum(r["kickoff"].startswith(day) for r in settled)) for day in daily]
        boot = sorted(sum(v for v,_ in sample)/(1000*sum(k for _,k in sample))
                      for _ in range(500) if (sample := rng.choices(blocks, k=len(blocks))))
        interval = [boot[12], boot[-13]]
    return {
        "selections": len(rows), "settled": n, "wins": sum(r["outcome"] == "won" for r in settled),
        "losses": sum(r["outcome"] == "lost" for r in settled), "void": sum(r["outcome"] == "void" for r in rows),
        "accuracy": sum(r["outcome"] == "won" for r in settled)/n if n else None,
        "mean_odds": statistics.mean(r["price"] for r in settled) if n else None,
        "mean_probability": statistics.mean(r["probability"] for r in settled) if n else None,
        "mean_ev": statistics.mean(r["ev"] for r in settled) if n else None,
        "profit_cents": running, "roi": running/(1000*n) if n else None, "drawdown_cents": drawdown,
        "roi_interval": interval, "curve": curve,
    }


def strategy_report(db, band="principal", competition=None):
    query = db.query(StrategyCapture, Match, observed_corner_total()).join(
        Match, Match.id == StrategyCapture.match_id,
    ).filter(StrategyCapture.captured_at < Match.kickoff_utc)
    if competition:
        codes = CONTINENTAL_CHAMPIONS if competition == "continental_champions" else (competition,)
        query = query.join(Competition).filter(Competition.code.in_(codes))
    captures = query.order_by(Match.kickoff_utc, Match.id).all()
    decisions = defaultdict(list)
    comparisons = defaultdict(list)
    history = []
    for capture, match, corners in captures:
        stats = match.statistics
        if corners is None and stats and stats.home_corners is not None and stats.away_corners is not None:
            if min(stats.home_corners, stats.away_corners) >= 0:
                corners = stats.home_corners + stats.away_corners

        def outcome(market):
            if match.status == "cancelled":
                return "void"
            if match.status != "finished":
                return "pending"
            settled = selection_outcome(market, match.home_goals, match.away_goals, corners)
            return "unverified" if settled is None else "won" if settled else "lost"

        label = f"{match.home_team.canonical_name} – {match.away_team.canonical_name}"
        fixture = {"match_id": match.id, "match": label, "league": match.competition.code,
                   "kickoff": match.kickoff_utc.isoformat()+"Z", "capture": capture.captured_at.isoformat()+"Z"}
        tickets = {}
        for name in WEIGHTS:
            row = capture.payload["decisions"].get(f"{name}:{band}")
            tickets[name] = {**fixture, **row, "outcome": outcome(row["market"])} if row else None
            if row:
                decisions[name].append(tickets[name])
        history.append({**fixture, "decisions": tickets, "decision_reasons": {
            name: capture.payload.get("decision_reasons", {}).get(f"{name}:{band}") for name in WEIGHTS
        }})
        for row in capture.payload["common"]:
            result = outcome(row["market"])
            if result in ("won", "lost") and all(name in row["probabilities"] for name in WEIGHTS):
                for name in WEIGHTS:
                    comparisons[name].append((row["probabilities"][name], result == "won", fixture["kickoff"]))

    def breakdown(rows, key):
        groups = defaultdict(list)
        for row in rows:
            groups[key(row)].append(row)
        return {k: economics(group) for k, group in groups.items()}

    return {
        "mode": "prospective_shadow", "active_strategy": "existing_production", "validation": "experimental",
        "band": band, "bands": BANDS, "fixtures": len(captures),
        "strategies": {
            name: {**economics(decisions[name]), "common_quality": _probability_metrics(comparisons[name]),
                   "by_league": breakdown(decisions[name], lambda r: r["league"]),
                   "by_market": breakdown(decisions[name], lambda r: r["market"]),
                   "by_odds": breakdown(decisions[name], lambda r: next(
                       (b for b in list(BANDS)[1:-1] if in_band(r["price"], b)), "other"))}
            for name in WEIGHTS
        },
        "history": history[-60:][::-1],
    }
