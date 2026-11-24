"""Orchestrate training and evaluate paired, frozen forecasts."""

import datetime as dt
import io
import json
from collections import defaultdict
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

import joblib
import numpy as np
from sqlalchemy import or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion
from backend.app.db.models.publication import ModelComparisonCut
from backend.app.features.goals import build_match_feature_table
from backend.app.normalization.competitions import CONTINENTAL_CHAMPIONS
from backend.app.prediction.eligibility import observed_corner_total
from backend.app.prediction.opportunities import valid_probability
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP, _predict_fixture_markets
from backend.app.services.publication_service import selection_outcome

TRAIN_LOCK = 16420825
COVERED = ("laliga", "premier_league", "bundesliga", "ligue_1", "serie_a",
           *CONTINENTAL_CHAMPIONS, "liga_f")
TERMINAL = {"finished", "cancelled"}
RESULT = ("home_win", "draw", "away_win")


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@contextmanager
def training_slot(db):
    # Training commits versions internally, so keep the lock on a dedicated
    # connection for the entire job rather than on the writing transaction.
    engine = db.get_bind()
    if engine.dialect.name != "postgresql":
        yield True
        return
    with engine.connect() as connection:
        acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": TRAIN_LOCK}).scalar()
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": TRAIN_LOCK})
                connection.commit()


def closed_round_results(fixtures):
    groups = defaultdict(list)
    for fixture in fixtures:
        key = fixture.round_name or fixture.matchday
        if key is not None:
            groups[fixture.season_id, key].append(fixture)
    return {
        match.id for group in groups.values()
        if all(match.status in TERMINAL for match in group)
        for match in group if match.status == "finished"
        and match.home_goals is not None and match.away_goals is not None
        and min(match.home_goals, match.away_goals) >= 0
    }


def versions_for(db, competition_code, cutoff):
    versions = db.query(ModelVersion).filter(
        or_(ModelVersion.dataset_version.like(f"{competition_code}:%"),
            ModelVersion.dataset_version.like(f"learning:{competition_code}:%")),
        ModelVersion.created_at <= cutoff,
    ).order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).all()
    active = next((version for version in versions if version.is_active is not False), None)
    candidate = next((version for version in versions if version.is_active is False
                      and version.training_end and version.training_end <= version.created_at.date()
                      and (active is None or version.created_at > active.created_at)), None)
    return active, candidate


def train_due(db, competition_codes=COVERED):
    reports = []
    with training_slot(db) as acquired:
        if not acquired:
            return [{"status": "training_already_running"}]
        for competition in db.query(Competition).filter(Competition.code.in_(competition_codes)).order_by(Competition.code):
            fixtures = db.query(Match).filter_by(competition_id=competition.id).all()
            completed = closed_round_results(fixtures)
            open_results = {
                match.id for match in fixtures if match.status == "finished"
                and (match.round_name is not None or match.matchday is not None) and match.id not in completed
            }
            if not completed:
                continue
            _, previous = versions_for(db, competition.code, utcnow())
            manifest = (previous.hyperparameters or {}).get("learning_manifest", {}) if previous else {}
            if completed <= set(manifest.get("closed_round_result_ids", [])):
                continue
            result = train_competition_models(
                db, competition.code, rolling=True, skip_unchanged=True, closed_result_ids=completed,
                open_round_result_ids=open_results,
            )
            reports.append(result)
    return reports


def _artifact(version):
    data = Path(version.artifact_path).read_bytes()
    return joblib.load(io.BytesIO(data)), sha256(data).hexdigest()


def capture_comparisons(db, *, cutoff=None, clock=utcnow):
    cutoff = cutoff or clock()
    groups = defaultdict(list)
    due = db.query(Match).join(Competition).filter(
        Competition.code.in_(COVERED), Match.status == "scheduled",
        Match.kickoff_utc > cutoff, Match.kickoff_utc <= cutoff + dt.timedelta(hours=1),
        ~Match.id.in_(db.query(ModelComparisonCut.match_id)),
    ).all()
    for fixture in due:
        groups[fixture.competition.code].append(fixture)
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    count = 0
    for code, fixtures in groups.items():
        active, candidate = versions_for(db, code, cutoff)
        if not active or not candidate or not active.artifact_path or not candidate.artifact_path:
            continue
        target_ids = {match.id for match in fixtures}
        data = load_matches_dataframe(db, fixtures[0].competition_id)
        if data.empty:
            continue
        historical = (data["status"] == "finished") & (data["date"] < cutoff)
        data = data.loc[historical | data["match_id"].isin(target_ids)].copy()
        # Explicitly mask any score/stat fields on the target fixtures.
        factual_columns = [column for column in data if column.startswith(("home_", "away_", "observed_"))
                           and column not in ("home_team_id", "away_team_id")]
        data.loc[data["match_id"].isin(target_ids), factual_columns] = np.nan
        table = build_match_feature_table(data.sort_values(["date", "match_id"]))
        target = table[table["match_id"].isin(target_ids)]
        if target.empty:
            continue
        data_hash = sha256(data.to_json(orient="records", date_format="iso", double_precision=15).encode()).hexdigest()
        forecasts, versions = {}, []
        for version in (active, candidate):
            artifact, artifact_hash = _artifact(version)
            versions.append({"id": version.id, "sha256": artifact_hash})
            output = defaultdict(dict)
            for row in _predict_fixture_markets(target.copy(), artifact, {}):
                p = row.calibrated_probability if row.calibrated_probability is not None else row.model_probability
                if row.market in ALL_MARKET_TO_ODDS_LOOKUP and valid_probability(p):
                    _, line, side = ALL_MARKET_TO_ODDS_LOOKUP[row.market]
                    output[row.match_id][row.market] = {"p": float(p), "line": line, "selection": side}
            forecasts[version.id] = output
        for match in fixtures:
            a, b = [forecasts[version["id"]][match.id] for version in versions]
            common = sorted(a.keys() & b.keys())
            generated_at = clock()
            if not common or generated_at >= match.kickoff_utc:
                continue
            payload = {
                "models": versions, "input_sha256": data_hash, "data_cutoff": cutoff.isoformat() + "Z",
                "scheduled_kickoff": match.kickoff_utc.isoformat() + "Z",
                "feature_row": json.loads(target[target["match_id"] == match.id].iloc[0].to_json(
                    date_format="iso", double_precision=15,
                )),
                "round": str(match.round_name or match.matchday),
                "rows": [dict(market=market, line=a[market]["line"], selection=a[market]["selection"],
                              titular=a[market]["p"], candidate=b[market]["p"]) for market in common],
            }
            result = db.execute(insert(ModelComparisonCut).values(
                match_id=match.id, captured_at=generated_at, payload=payload,
            ).on_conflict_do_nothing(index_elements=["match_id"]))
            count += max(result.rowcount, 0)
    return count


def _scores(rows):
    a = np.asarray([row[2] for row in rows])
    b = np.asarray([row[3] for row in rows])
    y = np.asarray([row[4] for row in rows])
    multiclass = a.ndim == 2

    def losses(p):
        error = (p - y) ** 2
        if multiclass:
            return error.sum(axis=1), -np.log(np.clip((p * y).sum(axis=1), 1e-12, 1))
        actual = np.where(y, p, 1 - p)
        return error, -np.log(np.clip(actual, 1e-12, 1))

    a_brier, a_log = losses(a)
    b_brier, b_log = losses(b)
    clusters = defaultdict(list)
    for index, row in enumerate(rows):
        clusters[row[0]].append(index)
    rounds = defaultdict(list)
    for match_id, indices in clusters.items():
        rounds[rows[indices[0]][1]].extend(indices)

    def interval(delta):
        if len(rounds) < 3:
            return None
        values = list(rounds.values())
        rng = np.random.default_rng(42)
        samples = [
            float(delta[np.concatenate([values[i] for i in rng.integers(len(values), size=len(values))])].mean())
            for _ in range(400)
        ]
        return np.quantile(samples, [.025, .975]).tolist()

    def calibration(p):
        p, actual = p.ravel(), y.ravel()
        return [
            {"lower": index / 10, "n": int(mask.sum()), "predicted": float(p[mask].mean()),
             "observed": float(actual[mask].mean())}
            for index in range(10)
            if (mask := np.minimum((p * 10).astype(int), 9) == index).any()
        ]

    return {
        "matches": len(clusters), "observations": len(rows), "rounds": len(rounds),
        "titular": {"brier": float(a_brier.mean()), "log_loss": float(a_log.mean()), "calibration": calibration(a)},
        "candidate": {"brier": float(b_brier.mean()), "log_loss": float(b_log.mean()), "calibration": calibration(b)},
        "delta": {"brier": float((b_brier - a_brier).mean()), "log_loss": float((b_log - a_log).mean()),
                  "brier_interval": interval(b_brier - a_brier), "log_loss_interval": interval(b_log - a_log)},
    }


def comparison_report(db, *, as_of=None):
    from backend.app.services.promotion_service import MIN_OUT_OF_SAMPLE_MATCHES

    cutoff = as_of or utcnow()
    observations = defaultdict(lambda: defaultdict(list))
    for cut, match, corners in db.query(ModelComparisonCut, Match, observed_corner_total()).join(
        Match, Match.id == ModelComparisonCut.match_id,
    ).filter(Match.status == "finished", Match.kickoff_utc <= cutoff,
             ModelComparisonCut.captured_at < Match.kickoff_utc):
        payload = cut.payload
        key = tuple(model["id"] for model in payload["models"])
        if corners is None and match.statistics:
            home, away = match.statistics.home_corners, match.statistics.away_corners
            if home is not None and away is not None and min(home, away) >= 0:
                corners = home + away
        markets = {row["market"]: row for row in payload["rows"]}
        if match.home_goals is None or match.away_goals is None:
            continue
        outcome = "home_win" if match.home_goals > match.away_goals else (
            "away_win" if match.away_goals > match.home_goals else "draw"
        )
        round_id = (match.season_id, payload["round"])
        if all(market in markets for market in RESULT):
            sides = [[markets[market][model] for market in RESULT] for model in ("titular", "candidate")]
            if all(abs(sum(side) - 1) < 1e-6 for side in sides):
                observations[key]["1x2"].append((match.id, round_id, *sides, [m == outcome for m in RESULT]))
        for market, row in markets.items():
            if market in RESULT:
                continue
            actual = selection_outcome(market, match.home_goals, match.away_goals, corners)
            if actual is not None:
                observations[key][market].append((match.id, round_id, row["titular"], row["candidate"], actual))
    def info(version):
        if version is None:
            return None
        manifest = (version.hyperparameters or {}).get("learning_manifest", {})
        ids = manifest.get("new_base_fit_match_ids")
        last_id = (manifest.get("base_fit_match_ids") or [None])[-1]
        last_match = db.get(Match, last_id) if last_id is not None else None
        status = "unverified_historical_availability" if manifest else "legacy_manifest_unverified"
        if version.training_end and version.training_end > version.created_at.date():
            status = "inconsistent_training_cutoff"
        return {"id": version.id, "trained_at": version.created_at.isoformat() + "Z",
                "last_round": (last_match.round_name or str(last_match.matchday)) if last_match else None,
                "new_fit_matches": len(ids) if ids is not None else None,
                "dataset_sha256": manifest.get("dataset_sha256"),
                "certification": status}

    output = []
    for competition in db.query(Competition).filter(Competition.code.in_(COVERED)).order_by(Competition.name):
        active, candidate = versions_for(db, competition.code, cutoff)
        metrics = {}
        if active and candidate:
            metrics = {k: _scores(rows) for k, rows in observations[active.id, candidate.id].items() if rows}
        paired = max((metric["matches"] for metric in metrics.values()), default=0)
        history = db.query(ModelVersion).filter(
            or_(ModelVersion.dataset_version.like(f"{competition.code}:%"),
                ModelVersion.dataset_version.like(f"learning:{competition.code}:%")),
            ModelVersion.created_at <= cutoff,
        ).order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).limit(5)
        output.append({
            "competition": competition.code, "name": competition.name,
            "titular": info(active), "candidate": info(candidate), "metrics": metrics,
            "comparison_status": "no_titular" if not active else (
                "insufficient_sample" if paired < MIN_OUT_OF_SAMPLE_MATCHES else "paired_evidence_available"
            ),
            "promotion_status": "blocked_uncertified_provenance",
            "history": [{"id": row.id, "created_at": row.created_at.isoformat() + "Z",
                         "state": "in_service" if active and row.id == active.id else
                         "inactive" if row.is_active is False else "legacy"}
                        for row in history],
        })
    return output
