"""Bounded ingestion and isolated model lifecycle for the first experimental leagues."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.ingestion.api_football.fixtures_provider import ApiFootballMatchProvider
from backend.app.normalization.competitions import EXPERIMENTAL_COMPETITIONS
from backend.app.services.champions_statistics import refresh_champions_statistics
from backend.app.services.data_service import ingest_matches
from backend.app.utils.dates import season_label

# Leave headroom for all established jobs and unexpected provider retries.
RESERVED_REQUESTS = 2500
MIN_FREE_BYTES = 5_000_000_000
FIRST_HISTORICAL_SEASON = 2023


def provider_budget(provider, estimated_requests):
    if not provider.is_available() or shutil.disk_usage(Path.cwd()).free < MIN_FREE_BYTES:
        return False
    status, _ = provider._get_page("/status", {})
    requests = status.get("requests", {}) if isinstance(status, dict) else {}
    usage, limit = requests.get("current"), requests.get("limit_day")
    return (type(usage) is int and type(limit) is int
            and limit - usage >= RESERVED_REQUESTS + estimated_requests)


def import_league(db, code, *, batches=25, historical=True, provider=None):
    if code not in EXPERIMENTAL_COMPETITIONS:
        raise ValueError(f"Competition is outside phase one: {code}")
    provider = provider or ApiFootballMatchProvider()
    if not provider_budget(provider, batches + 5):
        return {"competition": code, "status": "deferred_resource_budget"}
    today = datetime.now(timezone.utc).date()
    provider.fixture_window = None if historical else (today - timedelta(days=3), today + timedelta(days=10))
    current = season_label(today)
    year = int(current.split("/")[0])
    seasons = [f"{y}/{(y + 1) % 100:02d}" for y in range(FIRST_HISTORICAL_SEASON, year + 1)][-5:]
    counts = {}
    for label in seasons if historical else [current]:
        already_imported = db.query(Match.id).join(Competition).join(Season, Match.season_id == Season.id).filter(
            Competition.code == code, Season.label == label, Match.provider == provider.name,
        ).first()
        if label == current or not already_imported:
            counts[label] = ingest_matches(db, provider, code, label, operation="experimental_fixtures")
    count = refresh_champions_statistics(db, provider, batches, code)
    return {"competition": code, "status": "imported", "fixtures_refreshed": counts, "statistics_updated": count}


def refresh_live_odds(db, code, *, limit=12):
    """Refresh upcoming fixtures only, using the established quote parser and archive."""
    from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider
    from backend.app.ingestion.base import FixtureOddsSnapshot
    from backend.app.services.data_service import _archive_if_api_football, _attach_odds_snapshots
    from backend.app.services.odds_diagnostics_service import record_ingestion_run

    provider = ApiFootballOddsProvider()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    competition = db.query(Competition).filter_by(code=code).one_or_none()
    if competition is None:
        return 0
    fixtures = db.query(Match).filter(
        Match.competition_id == competition.id, Match.provider == "api_football", Match.status == "scheduled",
        Match.kickoff_utc > now, Match.kickoff_utc <= now + timedelta(hours=6),
    ).order_by(Match.kickoff_utc).limit(limit).all()
    if not fixtures or not provider_budget(provider, len(fixtures) * 2):
        return 0
    snapshots = []
    for match in fixtures:
        fixture_id = int(match.provider_id.removeprefix("api-football-"))
        entries = [entry for entry in provider._get("/odds", {"fixture": fixture_id})
                   if entry.get("fixture", {}).get("id") == fixture_id]
        suspended = set()
        rows, _ = provider._parse_odds_response(entries, suspended_markets=suspended)
        rows = [row for row in rows if not row.market.startswith("cards") and row.provider_updated_at
                and row.provider_updated_at <= row.fetched_at < match.kickoff_utc]
        if (rows or suspended) and datetime.now(timezone.utc).replace(tzinfo=None) < match.kickoff_utc:
            snapshots.append(FixtureOddsSnapshot(
                match.home_team.canonical_name, match.away_team.canonical_name,
                match.kickoff_utc, rows, match.provider_id, frozenset(suspended),
            ))
    _archive_if_api_football(db, provider, "experimental_odds", code)
    result = _attach_odds_snapshots(db, snapshots, provider.name, competition.id, "experimental_odds")
    record_ingestion_run(db, provider.name, "experimental_odds", code, now,
                         datetime.now(timezone.utc).replace(tzinfo=None),
                         fixtures_fetched=len(fixtures), matched=result.matched)
    return result.matched


def refresh_experiment(db, code, *, historical=False, batches=1):
    from backend.app.services.continuous_learning import train_due
    from backend.app.services.evaluation_service import settle_finished_predictions
    from backend.app.services.prediction_service import generate_predictions_for_competition

    imported = import_league(db, code, batches=batches, historical=historical)
    if imported["status"] != "imported":
        return imported
    odds_matches = refresh_live_odds(db, code)
    training = train_due(db, (code,))
    settled = settle_finished_predictions(db, code)
    forecasts = generate_predictions_for_competition(db, code)
    return {**imported, "odds_matches": odds_matches, "settled": settled, "forecasts": len(forecasts),
            "training": [{k: v for k, v in row.items() if k != "metrics"} for row in training]}
