"""Regularized simplex calibration with a chronological acceptance gate."""

import numpy as np

RESULT = ("home_win", "draw", "away_win")
COMPLEMENTS = (("over_2_5", "under_2_5"), ("corners_over_9_5", "corners_under_9_5"))
TEMPERATURES = (.75, .85, .95, 1., 1.05, 1.15, 1.30, 1.50, 1.75, 2.)
EPS = 1e-6


def simplex(probabilities, temperature=1.):
    logits = np.log(np.clip(np.asarray(probabilities, dtype=float), EPS, 1.)) / temperature
    weights = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return weights / weights.sum(axis=-1, keepdims=True)


def fit_temperature(probabilities, targets, dates):
    """Choose one parameter on earlier dates, then gate on unseen later dates."""
    p, y, dates = np.asarray(probabilities), np.asarray(targets), np.asarray(dates)
    if len(p) < 60 or len(np.unique(dates)) < 2:
        return None, {"reason": "insufficient_sample", "n": len(p)}
    split = np.sort(dates)[int(.7 * len(dates))]
    train = dates < split
    test = ~train
    if train.sum() < 30 or test.sum() < 20 or len(np.unique(y[train])) != p.shape[1]:
        return None, {"reason": "insufficient_temporal_support", "n": len(p)}

    def evaluate(prediction, labels):
        targets = np.eye(p.shape[1])[labels]
        return float(-np.log(prediction[np.arange(len(labels)), labels]).mean()), float(((prediction - targets) ** 2).sum(axis=1).mean())

    def metrics(temperature, mask):
        return evaluate(simplex(p[mask], temperature), y[mask])

    from sklearn.isotonic import IsotonicRegression

    marginal = np.column_stack([
        IsotonicRegression(out_of_bounds="clip").fit(p[train, k], y[train] == k).predict(p[test, k])
        for k in range(p.shape[1])
    ])
    baseline = evaluate(simplex(marginal), y[test])
    temperature = min(TEMPERATURES, key=lambda t: metrics(t, train)[0] + .02 * np.log(t) ** 2)
    raw, calibrated = metrics(1., test), metrics(temperature, test)
    accepted = temperature != 1. and calibrated[0] < raw[0] and calibrated[1] <= raw[1]
    return (temperature if accepted else None), {
        "reason": "accepted" if accepted else "no_temporal_improvement",
        "fit": int(train.sum()), "validation": int(test.sum()), "validation_start": str(split),
        "raw_log_loss_brier": raw, "temperature_log_loss_brier": calibrated,
        "normalized_isotonic_log_loss_brier": baseline,
    }


def corrected_forecasts(raw, calibrated, temperature=None):
    """Return coherent probabilities and an explicit source per market."""
    result = {
        market: (float(np.clip(calibrated[market], EPS, 1-EPS)), "validated_marginal")
        if calibrated.get(market) is not None and 0 < calibrated[market] < 1
        else (float(np.clip(p, EPS, 1-EPS)), "original_fallback")
        for market, p in raw.items()
    }
    for group in (RESULT, *COMPLEMENTS):
        if not all(m in raw for m in group):
            for m in group:
                if m in raw:
                    result[m] = float(np.clip(raw[m], EPS, 1-EPS)), "original_fallback"
            continue
        if group == RESULT:
            values = simplex([raw[m] for m in group], temperature or 1.)
            source = "joint_temperature" if temperature is not None else "original_simplex_fallback"
        else:
            p, source = result[group[0]]
            values = simplex([p, 1-p])
        result.update({m: (float(p), source) for m, p in zip(group, values)})

    if all(m in result for m in RESULT):
        home, draw, away = (result[m][0] for m in RESULT)
        for market, probability in zip(("double_chance_1x", "double_chance_x2", "double_chance_12"),
                                       (home+draw, draw+away, home+away)):
            if market in raw:
                result[market] = probability, "coherent_1x2" if temperature is not None else "original_derived_1x2"
    return result
