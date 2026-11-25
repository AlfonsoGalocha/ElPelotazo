"""Close recently played API-Football fixtures between the hourly full refreshes.

Only terminal transitions are written (finished with its score, cancelled, postponed), with the
same status mapping as `update-fixtures`, which keeps being the source of every other field.
"""

import datetime as dt
import logging

import httpx

from backend.app.db.models.matches import Match
from backend.app.ingestion.api_football.client import ApiFootballClient
from backend.app.ingestion.api_football.fixtures_provider import _match_status

logger = logging.getLogger(__name__)
WINDOW = dt.timedelta(hours=5)


def refresh_recent_results(db, now=None):
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    pending = {
        match.provider_id.removeprefix("api-football-"): match
        for match in db.query(Match).filter(
            Match.provider == "api_football", Match.status.in_(("scheduled", "live")),
            Match.kickoff_utc <= now, Match.kickoff_utc >= now - WINDOW,
        )
    }
    if not pending:
        return 0
    with httpx.Client(timeout=10.0) as http_client:
        provider = ApiFootballClient(http_client)
        if not provider.is_available():
            return 0
        ids = sorted(pending)
        rows = [row for offset in range(0, len(ids), 20)
                for row in provider._get("/fixtures", {"ids": "-".join(ids[offset:offset + 20])})]
    closed = 0
    for row in rows:
        match = pending.get(str(row["fixture"]["id"]))
        status = _match_status(row["fixture"]["status"].get("short"))
        if match is None or status == "scheduled":
            continue
        goals = row.get("goals") or {}
        if status == "finished":
            home, away = goals.get("home"), goals.get("away")
            if not all(type(n) is int and n >= 0 for n in (home, away)):
                continue
            match.home_goals, match.away_goals = home, away
        match.status = status
        closed += 1
    db.flush()
    return closed
