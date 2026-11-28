"""Operational monitoring for the local scheduler and its capture windows."""

import datetime as dt

from sqlalchemy import func, or_

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, OddsIngestionRun
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.db.models.publication import StrategyCapture, StrategyScan
from backend.app.normalization.competitions import EXPERIMENTAL_COMPETITIONS
from backend.app.services.strategy_shadow import LEAGUES, capture_strategy_due, strategy_report

# First observed deployment with the capture job, from the Docker start record.
FIRST_SHADOW_DEPLOYMENT = dt.datetime(2026, 11, 23, 11, 6, 24)
SCAN_INTERVAL_SECONDS = 600


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def run_strategy_scan(*, experimental=False):
    scope = "experimental" if experimental else "primary"
    started = utc_now()
    try:
        with session_scope() as db:
            count = capture_strategy_due(db, experimental=True) if experimental else capture_strategy_due(db)
            db.add(StrategyScan(started_at=started, finished_at=utc_now(), ok=True, captures=count, competition_scope=scope))
        return count
    except Exception as exc:
        # Keep the error observation even if the capture transaction rolled back.
        with session_scope() as db:
            db.add(StrategyScan(started_at=started, finished_at=utc_now(), ok=False,
                                error_type=type(exc).__name__, competition_scope=scope))
        raise


def _iso(value):
    return value.isoformat() + "Z" if value is not None else None


def missed_reason(kickoff, scanned):
    if kickoff <= FIRST_SHADOW_DEPLOYMENT:
        return "before_deployment"
    if not scanned:
        return "no_recorded_scan"
    if all(not scan.ok for scan in scanned):
        return "scan_failed"
    return "unknown"


def operations_report(db, now=None, *, scope="primary"):
    leagues = EXPERIMENTAL_COMPETITIONS if scope == "experimental" else LEAGUES
    scan_scope = StrategyScan.competition_scope == scope
    if scope == "primary":
        scan_scope = or_(scan_scope, StrategyScan.competition_scope.is_(None))
    now = now or utc_now()
    since = now - dt.timedelta(days=7)
    scans = db.query(StrategyScan).filter(StrategyScan.finished_at >= since, scan_scope).order_by(
        StrategyScan.finished_at.desc()
    ).all()
    last, last_ok = (scans[0] if scans else None), next((scan for scan in scans if scan.ok), None)
    recent_runs = db.query(OddsIngestionRun).filter(
        OddsIngestionRun.competition_code.in_(leagues), OddsIngestionRun.operation == ("experimental_odds" if scope == "experimental" else "update_odds"),
    ).order_by(OddsIngestionRun.finished_at.desc()).limit(30).all()
    odds_ok = next((run for run in recent_runs if not run.error and (run.matched or 0) > 0), None)
    latest_runs = {}
    for run in recent_runs:
        latest_runs.setdefault(run.competition_code, run)
    newest_quote_origin, newest_quote_query = db.query(
        func.max(MatchOdds.provider_updated_at), func.max(MatchOdds.fetched_at),
    ).join(Match, Match.id == MatchOdds.match_id).join(Competition).filter(
        Competition.code.in_(leagues), Match.status == "scheduled", Match.kickoff_utc > now,
        MatchOdds.snapshot_type == "pre_match",
    ).one()

    fixture_since = since
    if scope == "experimental":
        first_model = db.query(func.min(ModelVersion.created_at)).filter(or_(*(
            condition for code in leagues for condition in (
                ModelVersion.dataset_version.like(f"{code}:%"),
                ModelVersion.dataset_version.like(f"learning:{code}:%"),
            )
        ))).scalar()
        fixture_since = max(since, first_model or now)

    future, missed, captured = [], [], []
    for match, capture in db.query(Match, StrategyCapture).join(Competition).outerjoin(
        StrategyCapture, StrategyCapture.match_id == Match.id,
    ).filter(Competition.code.in_(leagues), Match.status.in_(("scheduled", "finished")),
             Match.kickoff_utc >= fixture_since, Match.kickoff_utc <= now + dt.timedelta(days=7)).order_by(
                 Match.kickoff_utc, Match.id):
        window = match.kickoff_utc - dt.timedelta(hours=1)
        row = dict(match_id=match.id, match=f"{match.home_team.canonical_name} – {match.away_team.canonical_name}",
                   league=match.competition.code, kickoff=_iso(match.kickoff_utc), window_opens=_iso(window))
        if capture:
            payload = capture.payload
            captured.append(dict(row, captured_at=_iso(capture.captured_at),
                reason=payload.get("reason", "captured"),
                match_status=match.status,
                selections=sum(value is not None for key, value in payload["decisions"].items() if key.endswith(":principal"))))
        elif match.kickoff_utc > now and match.status == "scheduled":
            future.append(dict(row, reason="pending_analysis"))
        elif match.kickoff_utc <= now:
            overlapping = [scan for scan in scans if window <= scan.started_at < match.kickoff_utc]
            missed.append(dict(row, status="missed_capture", reason=missed_reason(match.kickoff_utc, overlapping)))

    active = last is not None and (now-last.finished_at).total_seconds() <= 2.5 * SCAN_INTERVAL_SECONDS
    if not active:
        status = "scheduler_inactive"
    elif not last.ok:
        status = "scan_error"
    elif any(run.error for run in latest_runs.values()):
        status = "provider_error"
    elif not future:
        status = "waiting"
    elif newest_quote_origin is None or now-newest_quote_origin > dt.timedelta(hours=6):
        status = "stale_data"
    else:
        status = "operational"

    reporting = strategy_report(db, scope=scope)
    pending = sum(row["selections"] - row["settled"] - row["void"]
                  for row in reporting["strategies"].values())
    versions = db.query(ModelVersion).filter(
        ModelVersion.metrics.is_not(None), ModelVersion.created_at >= FIRST_SHADOW_DEPLOYMENT,
    ).order_by(ModelVersion.id.desc()).limit(30).all()
    calibrations = [{"id": row.id, "dataset": row.dataset_version, **row.metrics["joint_1x2"]}
                    for row in versions if "joint_1x2" in row.metrics]
    if scope == "experimental":
        calibrations = [row for row in calibrations if any(
            row["dataset"].startswith((f"{code}:", f"learning:{code}:")) for code in leagues
        )]
    last_prediction = db.query(Prediction.model_version_id).join(Match).join(Competition).filter(
        Competition.code.in_(leagues), Prediction.created_at <= now).order_by(
            Prediction.created_at.desc(), Prediction.id.desc()).first()
    return dict(
        status=status, scheduler_active=active, last_scan=_iso(last.finished_at if last else None),
        last_success=_iso(last_ok.finished_at if last_ok else None),
        last_odds_update=_iso(odds_ok.finished_at if odds_ok else None),
        latest_quote_origin=_iso(newest_quote_origin), latest_quote_query=_iso(newest_quote_query),
        next_capture=future[0] if future else None, upcoming=future[:20],
        missed=missed[::-1], captured=captured[::-1], pending_selections=pending,
        errors=[{"at": _iso(s.finished_at), "type": s.error_type} for s in scans if not s.ok][:8],
        provider_errors=[{"at": _iso(r.finished_at), "league": r.competition_code, "provider": r.provider}
                         for r in latest_runs.values() if r.error],
        last_prediction_model=last_prediction[0] if last_prediction else None,
        calibrations=calibrations,
    )
