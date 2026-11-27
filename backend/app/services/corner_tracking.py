"""Pinned corner candidates evaluated inside the continuous-learning cuts."""

import datetime as dt
import io
import logging
from collections import defaultdict
from hashlib import sha256

import joblib
import numpy as np

from backend.app.config.settings import get_settings
from backend.app.market.prematch_reference import no_vig_reference, snapshot_markets
from backend.app.models.calibration.metrics import brier_score, expected_calibration_error, log_loss_score
from backend.app.prediction.opportunities import valid_probability
from backend.app.prediction.predictor import (
    apply_coherent_calibration,
    apply_corner_survival_correction,
    predict_secondary_markets_for_table,
)
from backend.app.services.prediction_service import SECONDARY_MARKET_TO_ODDS_LOOKUP

logger = logging.getLogger(__name__)

# The IDs belong to the isolated database. The version and digest identify
# the artifacts without registering or reusing those IDs in the live DB.
TRACKED = {
    "afc_champions_league": (
        264, "primary", "2026-11-27T11:06:21.077174",
        "3e065346f3874084ab985f240a979444ceeef233ac5928faf75466e22c7bed68",
    ),
    "laliga": (
        266, "secondary", "2026-11-27T11:06:39.901476",
        "2e451a137193fa51ee47b8499b6c426818511acf88e79f638a23c687bf81ec33",
    ),
}


def candidate_info(code):
    source_id, priority, version, digest = TRACKED[code]
    return dict(source_id=source_id, priority=priority, version=version, sha256=digest)


def tracked_artifact(code, cutoff):
    info = candidate_info(code)
    if dt.datetime.fromisoformat(info["version"]) > cutoff:
        return None
    name = info["version"].replace(":", "-") + ".joblib"
    path = get_settings().model_artifacts_path / "auditoria-corners-20261127" / code / name
    try:
        data = path.read_bytes()
        if sha256(data).hexdigest() != info["sha256"]:
            raise ValueError("unexpected artifact SHA-256")
        return joblib.load(io.BytesIO(data))
    except (AttributeError, EOFError, ImportError, OSError, ValueError):
        logger.exception("corner_tracking.artifact_unavailable: competition=%s path=%s", code, path)
        return None


def corner_probabilities(table, artifact):
    if not artifact or "corners_model" not in artifact:
        return {}
    rows = predict_secondary_markets_for_table(
        table, artifact["corners_model"], calibrators=artifact.get("calibrators"),
    )
    apply_corner_survival_correction(rows, artifact)
    if "coherent_calibration" in artifact:
        apply_coherent_calibration(rows, artifact["coherent_calibration"])
    forecasts = defaultdict(dict)
    for row in rows:
        if row.market in SECONDARY_MARKET_TO_ODDS_LOOKUP:
            forecasts[row.match_id][row.market] = (
                row.model_probability,
                row.calibrated_probability if row.calibrated_probability is not None else row.model_probability,
            )
    return forecasts


def frozen_rows(candidate, titular, quotes, cutoff):
    snapshot = snapshot_markets(quotes, cutoff)
    rows = []
    for key, (_, line, selection) in SECONDARY_MARKET_TO_ODDS_LOOKUP.items():
        raw, corrected = candidate.get(key, (None, None))
        if valid_probability(raw) and valid_probability(corrected):
            baseline = titular.get(key, (None, None))[1]
            rows.append(dict(
                market=key, line=line, selection=selection, candidate=corrected, poisson=raw,
                titular=baseline if valid_probability(baseline) else None,
                reference=no_vig_reference(snapshot, SECONDARY_MARKET_TO_ODDS_LOOKUP[key]),
            ))
    return rows


def probability_scores(y, p):
    y, p = np.asarray(y), np.asarray(p)
    return dict(brier=brier_score(y, p), log_loss=log_loss_score(y, p), ece=expected_calibration_error(y, p))


def summarize_corners(cuts):
    settled = defaultdict(list)
    coverage = defaultdict(lambda: dict(candidate=0, poisson=0, titular=0, reference=0))
    coherent = defaultdict(lambda: dict(checked=0, monotonicity=0, complement=0))
    for payload, corners in cuts:
        markets = {row["market"]: row for row in payload}
        for reference in ("candidate", "poisson", "titular"):
            ordered = [markets.get(key, {}).get(reference) for key, spec in SECONDARY_MARKET_TO_ODDS_LOOKUP.items()
                       if spec[2] == "over"]
            under = markets.get("corners_under_9_5", {}).get(reference)
            if all(p is not None for p in [*ordered, under]):
                counts = coherent[reference]
                counts["checked"] += 1
                counts["monotonicity"] += any(b > a + 1e-9 for a, b in zip(ordered, ordered[1:]))
                counts["complement"] += abs(ordered[1] + under - 1) > 1e-9
        for row in payload:
            for model in coverage[row["market"]]:
                coverage[row["market"]][model] += row[model] is not None
            if corners is not None:
                actual = corners > row["line"] if row["selection"] == "over" else corners < row["line"]
                settled[row["market"]].append((row, actual))

    metrics = {}
    for market, observations in settled.items():
        comparisons = {}
        for reference in ("poisson", "titular", "reference"):
            pairs = [(actual, row["candidate"], row[reference]) for row, actual in observations
                     if row[reference] is not None]
            if pairs:
                y, candidate, baseline = zip(*pairs)
                comparisons[reference] = dict(
                    n=len(pairs), candidate=probability_scores(y, candidate),
                    baseline=probability_scores(y, baseline),
                )
        metrics[market] = dict(settled=len(observations), comparisons=comparisons)
    return dict(captured=len(cuts), settled=sum(corners is not None for _, corners in cuts),
                coverage=dict(coverage), coherence=dict(coherent), metrics=metrics)
