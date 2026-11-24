"""One monotone map of the total-corners survival function."""

import numpy as np
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression

from backend.app.models.calibration.metrics import brier_score, log_loss_score

POLICY_VERSION = "corner-survival-logistic-v1"
MIN_FIXTURES_PER_BLOCK = 30


def correct_probability(probability, parameters, *, under=False):
    survival = 1 - probability if under else probability
    logits = logit(np.clip(survival, .001, .999))
    corrected = np.clip(expit(parameters["slope"] * logits + parameters["intercept"]), .001, .999)
    return 1 - corrected if under else corrected


def validate_corner_calibration(probabilities, labels, dates):
    """Fit, choose a regularization strength, and audit on later kickoff blocks."""
    p, y, dates = np.asarray(probabilities), np.asarray(labels), np.asarray(dates)
    unique_dates = np.unique(dates)
    if len(unique_dates) < 3:
        return None, {"reason": "insufficient_fixtures"}
    left, right = unique_dates[len(unique_dates) // 3], unique_dates[2 * len(unique_dates) // 3]
    fit, choose, test = dates < left, (dates >= left) & (dates < right), dates >= right
    sizes = [int(mask.sum()) for mask in (fit, choose, test)]
    report = {"version": POLICY_VERSION, "fixture_counts": sizes}
    if min(sizes) < MIN_FIXTURES_PER_BLOCK or any(
        np.any(y[mask].sum(axis=0) < 5) or np.any((1 - y[mask]).sum(axis=0) < 5)
        for mask in (fit, choose, test)
    ):
        return None, {**report, "reason": "insufficient_fixtures"}

    def metrics(observed, forecast):
        return {"brier": brier_score(observed.ravel(), forecast.ravel()),
                "log_loss": log_loss_score(observed.ravel(), forecast.ravel())}

    def improves(candidate, baseline):
        return all(candidate[key] < baseline[key] for key in baseline)

    baseline = metrics(y[choose], p[choose])
    candidates = []
    x = logit(np.clip(p[fit].reshape(-1, 1), .001, .999))
    # Each fixture contributes several thresholds from the same count.
    weights = np.full(y[fit].size, 1 / y.shape[1])
    for strength in (.1, 1., 10.):
        model = LogisticRegression(C=strength).fit(x, y[fit].ravel(), sample_weight=weights)
        params = {"slope": float(model.coef_[0, 0]), "intercept": float(model.intercept_[0]),
                  "version": POLICY_VERSION}
        if params["slope"] <= 0:
            continue
        scores = metrics(y[choose], correct_probability(p[choose], params))
        if improves(scores, baseline):
            candidates.append((scores["log_loss"], params))
    report["original_holdout"] = metrics(y[test], p[test])
    if not candidates:
        return None, {**report, "reason": "no_improvement_on_selection"}
    _, params = min(candidates, key=lambda candidate: candidate[0])
    report["corrected_holdout"] = metrics(y[test], correct_probability(p[test], params))
    accepted = improves(report["corrected_holdout"], report["original_holdout"])
    return (params if accepted else None), {**report, "reason": "accepted" if accepted else "holdout_rejected"}
