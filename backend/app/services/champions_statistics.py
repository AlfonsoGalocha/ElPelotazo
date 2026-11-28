"""Populate regulation-time Champions League statistics from API-Football."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from backend.app.db.models.core import Competition, ProviderResponse, Season
from backend.app.db.models.matches import Match, MatchStatistics
from backend.app.normalization.competitions import CHAMPIONS_LEAGUE, EXPERIMENTAL_COMPETITIONS, LIGA_F
from backend.app.services.data_service import _archive_if_api_football
from backend.app.utils.dates import season_label

# Domestic leagues whose current-season statistics are completed through the same batched call.
DOMESTIC_LEAGUES = ("laliga", "premier_league", "serie_a", "bundesliga", "ligue_1")

FIELDS = {
    "Total Shots": "shots", "Shots on Goal": "shots_on_target", "Corner Kicks": "corners",
    "Fouls": "fouls", "Yellow Cards": "yellow_cards", "Red Cards": "red_cards",
}


def regulation_statistics(payload):
    fixture, teams = payload.get("fixture", {}), payload.get("teams", {})
    if fixture.get("status", {}).get("short") != "FT":
        return {}
    expected = {teams.get(side, {}).get("id"): side for side in ("home", "away")}
    if None in expected or len(expected) != 2:
        return {}
    groups = payload.get("statistics") or []
    if len(groups) != 2 or {row.get("team", {}).get("id") for row in groups} != expected.keys():
        return {}
    return {
        f"{expected[row['team']['id']]}_{FIELDS[item['type']]}": item["value"]
        for row in groups for item in row.get("statistics", [])
        if item.get("type") in FIELDS and type(item.get("value")) is int and item["value"] >= 0
    }


def refresh_champions_statistics(db, provider, batches, competition=CHAMPIONS_LEAGUE):
    operation = (
        "experimental_statistics" if competition in EXPERIMENTAL_COMPETITIONS
        else f"update_{competition.removesuffix('_league')}_statistics"
    )
    if not provider.is_available():
        return 0
    recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    tried = {
        f"api-football-{fixture_id}"
        for (params,) in db.query(ProviderResponse.params).filter(
            ProviderResponse.operation == operation, ProviderResponse.competition_code == competition,
            ProviderResponse.fetched_at >= recent,
        )
        for fixture_id in str(params.get("ids", "")).split("-") if fixture_id.isdigit()
    }
    missing = db.query(Match).join(Competition).outerjoin(MatchStatistics).filter(
        Competition.code == competition, Match.provider == "api_football",
        Match.status == "finished",
        # Domestic fixtures do not store the provider status; the payload itself must say FT.
        or_(Match.provider_status == "FT", Competition.code.in_(DOMESTIC_LEAGUES)),
        or_(*(getattr(MatchStatistics, f"{side}_{stat}").is_(None)
              for side in ("home", "away") for stat in ("corners", "shots", "shots_on_target"))),
    ).order_by(Match.kickoff_utc.desc(), Match.id)
    if competition == LIGA_F:
        missing = missing.join(Season, Season.id == Match.season_id).filter(Season.label.in_(("2023/24", "2024/25")))
    elif competition in DOMESTIC_LEAGUES:
        # Earlier seasons come complete from the historical CSV; only the running season is fetched.
        missing = missing.join(Season, Season.id == Match.season_id).filter(
            Season.label == season_label(datetime.now(timezone.utc).date()))
    matches = [m for m in missing if m.provider_id not in tried][:batches*20]
    updated = 0
    for start in range(0, len(matches), 20):
        batch = {m.provider_id: m for m in matches[start:start+20]}
        ids = "-".join(key.removeprefix("api-football-") for key in batch)
        payloads = provider._get("/fixtures", {"ids": ids})
        _archive_if_api_football(db, provider, operation, competition)
        for payload in payloads:
            match = batch.get(f"api-football-{payload.get('fixture', {}).get('id')}")
            values = regulation_statistics(payload)
            if match is None or not values:
                continue
            if match.statistics is None:
                match.statistics = MatchStatistics(match_id=match.id)
            for column, value in values.items():
                setattr(match.statistics, column, value)
            updated += 1
        db.commit()
    return updated
