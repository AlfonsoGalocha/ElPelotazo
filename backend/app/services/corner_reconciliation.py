"""Archive verified corner counts from API-Football."""

from __future__ import annotations

import datetime as dt

from backend.app.db.models.core import ProviderResponse
from backend.app.db.models.matches import CornerObservation, Match
from backend.app.ingestion.api_football.client import ApiFootballClient


def extract_corner_total(entries: list[dict]) -> int | None:
    counts: dict[int, int] = {}
    if len(entries) != 2:
        return None
    for entry in entries:
        team_id = entry.get("team", {}).get("id")
        values = [
            stat.get("value") for stat in entry.get("statistics", [])
            if stat.get("type") == "Corner Kicks"
        ]
        if (
            type(team_id) is not int or team_id <= 0 or team_id in counts
            or len(values) != 1 or type(values[0]) is not int or values[0] < 0
        ):
            return None
        counts[team_id] = values[0]
    return sum(counts.values())


def reconcile(db, client: ApiFootballClient, match_id: int, persist: bool = False) -> int | None:
    match = db.get(Match, match_id)
    prefix = "api-football-"
    if (
        match is None or match.status != "finished" or match.provider != "api_football"
        or match.kickoff_utc >= dt.datetime.utcnow()
        or not match.provider_id.startswith(prefix)
        or not match.provider_id[len(prefix):].isdigit()
    ):
        raise ValueError("Se requiere un partido finalizado con identificador de API-Football")
    if not client.is_available():
        raise RuntimeError("API-Football no está configurada")

    params = {"fixture": int(match.provider_id[len(prefix):])}
    path = "/fixtures/statistics"
    cursor = len(client.last_raw_responses)
    entries = client._get(path, params)
    pages = client.last_raw_responses[cursor:]
    total = extract_corner_total(entries) if len(pages) == 1 else None
    if persist:
        for page in pages:
            response = ProviderResponse(
                provider="api_football", operation="reconcile_corners",
                path=path, params=page["params"], response=page["response"],
                fetched_at=dt.datetime.utcnow(),
            )
            db.add(response)
            if total is not None:
                db.flush()
                db.add(CornerObservation(match_id=match.id, provider_response_id=response.id, total=total))
    return total
