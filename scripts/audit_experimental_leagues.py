"""Read-only API-Football reconnaissance for the experimental club leagues."""

import datetime as dt
import json
from collections import Counter

from backend.app.ingestion.api_football.client import ApiFootballClient

LEAGUES = {
    "primeira_liga": ("Portugal", "Primeira Liga"),
    "eredivisie": ("Netherlands", "Eredivisie"),
    "segunda_division": ("Spain", "Segunda División"),
    "championship": ("England", "Championship"),
    "jupiler_pro_league": ("Belgium", "Jupiler Pro League"),
    "super_lig": ("Turkey", "Süper Lig"),
    "europa_league": ("World", "UEFA Europa League"),
    "conference_league": ("World", "UEFA Conference League"),
}


def audit(client):
    status, _ = client._get_page("/status", {})
    print(json.dumps({"quota": status.get("requests") if isinstance(status, dict) else None}), flush=True)
    catalogs = {}
    for code, (country, name) in LEAGUES.items():
        if country not in catalogs:
            catalogs[country] = client._get("/leagues", {"country": country})
        aliases = {name}
        if code == "conference_league":
            aliases.add("UEFA Europa Conference League")
        if code == "segunda_division":
            aliases.update(("Segunda Division", "La Liga 2"))
        matches = [row for row in catalogs[country] if row["league"]["name"] in aliases]
        if len(matches) != 1:
            print(json.dumps({"code": code, "catalog_matches": len(matches)}), flush=True)
            continue
        entry = matches[0]
        league = entry["league"]["id"]
        seasons = sorted(entry["seasons"], key=lambda season: season["year"])
        current = next((s["year"] for s in seasons if s["current"]), seasons[-1]["year"])
        counts, sample_ids, recent = {}, [], []
        start = len(client.last_raw_responses)
        for season in [s for s in seasons if s["year"] <= current][-4:]:
            year = season["year"]
            rows = client._get("/fixtures", {"league": league, "season": year})
            finished = [r for r in rows if r["fixture"]["status"]["short"] in {"FT", "AET", "PEN"}]
            finished.sort(key=lambda r: r["fixture"]["date"])
            counts[year] = {
                "fixtures": len(rows), "finished": len(finished),
                "goals": sum(all(r["goals"].get(side) is not None for side in ("home", "away")) for r in finished),
                "coverage": season["coverage"],
            }
            if finished:
                sample_ids.append(finished[len(finished) // 2]["fixture"]["id"])
                recent.append(finished[-1]["fixture"]["date"])
        stats = Counter()
        if sample_ids:
            for row in client._get("/fixtures", {"ids": "-".join(map(str, sample_ids))}):
                observed = Counter(item["type"] for team in row.get("statistics", []) for item in team["statistics"]
                                   if item["value"] is not None)
                stats.update({key: 1 for key, n in observed.items() if n == 2})
        standings = client._get("/standings", {"league": league, "season": current})
        odds, paging = client._get_page("/odds", {"league": league, "season": current})
        markets = Counter()
        for fixture in odds:
            markets.update({bet["name"] for book in fixture.get("bookmakers", []) for bet in book["bets"]})
        print(json.dumps(dict(
            code=code, id=league, name=entry["league"]["name"], current=current, seasons=[s["year"] for s in seasons],
            counts=counts, sample_size=len(sample_ids), stats=stats, standings=bool(standings), latest_finished=max(recent, default=None),
            odds_sample=len(odds), odds_pages=(paging or {}).get("total"), odds_markets=markets,
            observed_requests=len(client.last_raw_responses) - start,
        )), flush=True)
    print(json.dumps({"completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                      "total_responses": len(client.last_raw_responses)}), flush=True)


if __name__ == "__main__":
    api = ApiFootballClient()
    if not api.is_available():
        raise SystemExit("API-Football deshabilitada o sin credenciales")
    audit(api)
