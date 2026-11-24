"""Read-only comparison of both Hoy policies on one pre-kickoff input sample."""

import datetime as dt
import json

import joblib
import numpy as np

from sqlalchemy import text

from backend.app.db.database import SessionLocal
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.features.goals import build_match_feature_table
from backend.app.models.calibration.corners import validate_corner_calibration
from backend.app.prediction.secondary_markets import (
    SECONDARY_MARKET_DEFINITIONS, has_real_count_data, label_for_secondary_market,
)
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.evaluation_service import market_track_records_with_provenance
from backend.app.services.prediction_service import (
    legacy_champions_preview, rank_champions_preview, score_champions_candidate,
)


def audit_corners(db, version, competition_id):
    artifact = joblib.load(version.artifact_path)
    if "corners_model" not in artifact:
        return None
    manifest = artifact["training_manifest"]
    table = build_match_feature_table(load_matches_dataframe(db, competition_id))
    base = table[table.match_id.isin(manifest["base_fit_match_ids"])]
    test = table[table.match_id.isin(manifest["holdout_match_ids"])]
    test = test[has_real_count_data(test, "corners")]
    if base.empty or test.empty or base.date.max() >= test.date.min():
        return {"reason": "temporal_coverage_not_verified"}
    by_market = artifact["corners_model"].predict_market_probabilities(test)
    markets = [key for key in by_market if SECONDARY_MARKET_DEFINITIONS[key].kind == "over"]
    p = np.column_stack([by_market[key] for key in markets])
    y = np.column_stack([label_for_secondary_market(test, key) for key in markets])
    _, report = validate_corner_calibration(p, y, test.date.to_numpy())
    intervals = []
    for lower, upper in zip(np.arange(0, 1, .1), np.arange(.1, 1.1, .1)):
        mask = (p >= lower) & (p < upper)
        if mask.any():
            intervals.append({"lower": round(lower, 1), "n": int(mask.sum()),
                              "mean": float(p[mask].mean()), "observed": float(y[mask].mean())})
    return {"fixtures": len(test), "over_80": int((p > .8).sum()), "over_90": int((p > .9).sum()),
            "intervals": intervals, "validation": report}


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        fixtures = db.query(Match).join(Competition).filter(
            Competition.code == "champions_league", Match.status == "scheduled",
            Match.kickoff_utc > now, Match.kickoff_utc <= now + dt.timedelta(days=10),
        ).all()
        version, outputs, _ = score_champions_candidate(db, fixtures, now, preview=True)
        if version is None:
            print("No hay candidato disponible")
            return
        print(json.dumps({"corner_audit_reconstructed": audit_corners(db, version, fixtures[0].competition_id)}))
        old = legacy_champions_preview(outputs, version.id)
        reliability = market_track_records_with_provenance(db, "champions_league", as_of=now)
        new, diagnostics = rank_champions_preview(fixtures, outputs, version.id, now, reliability)
        for match in sorted(fixtures, key=lambda match: (match.kickoff_utc, match.id)):
            previous = old.get(match.id)
            print(json.dumps({
                "match_id": match.id, "before": previous, "after": new.get(match.id),
                "previous_market_diagnostics": diagnostics.get((match.id, previous["market"])) if previous else None,
            }))


if __name__ == "__main__":
    main()
