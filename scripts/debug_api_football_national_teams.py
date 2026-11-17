"""Reconocimiento puntual (NO parte del pipeline, solo lectura): pedido
real del usuario para arrancar la seccion "Selecciones" ("quiero que
funcione igual que con los clubes pero para selecciones... recoge todos
los datos que puedas de la API"). Antes de escribir ningun codigo de
ingesta/modelado nuevo, hace falta saber CON DATOS REALES (nunca
adivinando IDs de memoria) que competiciones de selecciones ofrece
realmente API-Football, con que cobertura (fixtures/cuotas/estadisticas)
y cuantos partidos reales hay disponibles -- mismo principio que todo
`debug_*` de esta carpeta: nunca construir sobre una suposicion cuando se
puede comprobar.

Qué hace, en dos pasos SIEMPRE baratos (1 request fijo + unos pocos de
fixtures, nunca de /odds salvo que pidas explicitamente un fixture_id):

1. `/leagues?country=World` (1 request): API-Football agrupa TODAS las
   competiciones de selecciones bajo country="World" (Mundial, Eurocopa,
   Nations League, Clasificacion para Mundial/Eurocopa por confederacion,
   amistosos, Copa America, Copa Africa de Naciones, etc.) -- un unico
   fetch trae el catalogo completo real, con el `id` exacto de cada una
   (NUNCA hardcodeado de memoria) y, por temporada, flags de cobertura
   (`coverage.fixtures`, `coverage.odds`, `coverage.standings`,
   `coverage.predictions`).
2. Para las que coincidan con una lista de nombres de interés (Mundial,
   Eurocopa, Nations League, clasificatorios, amistosos -- ver
   `INTEREST_KEYWORDS`), pide ademas `/fixtures?league=X&season=Y&next=10`
   (barato, sin coste de /odds) para ver partidos reales programados.

Uso (dentro del contenedor, con tu API key real):
    docker compose exec backend python scripts/debug_api_football_national_teams.py

Para además comprobar cuotas reales de UN fixture concreto (más caro, solo
cuando ya sepas qué liga/fixture te interesa, reutiliza el mismo patrón
que `debug_api_football_odds.py`):
    docker compose exec backend python scripts/debug_api_football_national_teams.py \
        --odds-for-fixture <fixture_id>
"""

from __future__ import annotations

import argparse

from backend.app.config.settings import get_settings
from backend.app.ingestion.api_football.client import ApiFootballClient

# Nombres (subcadena, insensible a mayusculas) de las competiciones de
# selecciones que de verdad interesan para esta seccion -- el resto del
# catalogo real de country="World" incluye cosas irrelevantes aqui
# (futbol femenino, sub-20/sub-23, futsal, Juegos Olimpicos...) que no se
# filtran de la API, solo del resumen que imprime este script.
INTEREST_KEYWORDS = (
    "world cup",
    "euro championship",
    "nations league",
    "world cup - qualification",
    "euro championship - qualification",
    "friendlies",
    "copa america",
    "africa cup of nations",
)


def _print_league_summary(league_entry: dict) -> None:
    league = league_entry["league"]
    country = league_entry.get("country", {})
    seasons = league_entry.get("seasons", [])
    latest = seasons[-1] if seasons else None
    print(
        f"\nid={league['id']}  nombre={league['name']!r}  tipo={league.get('type')}  "
        f"pais={country.get('name')}"
    )
    if latest is None:
        print("  (sin temporadas listadas)")
        return
    coverage = latest.get("coverage", {})
    print(
        f"  ultima temporada={latest.get('year')}  "
        f"fixtures={coverage.get('fixtures')}  "
        f"odds={coverage.get('odds')}  "
        f"standings={coverage.get('standings')}  "
        f"predictions={coverage.get('predictions')}"
    )


def main(odds_for_fixture: int | None = None) -> None:
    settings = get_settings()
    print(
        f"api_football_enabled={settings.api_football_enabled} "
        f"key_present={bool(settings.api_football_key)} "
        f"use_rapidapi={settings.api_football_use_rapidapi}"
    )
    if not settings.api_football_enabled or not settings.api_football_key:
        print(
            "API-Football no esta activado o falta la key -- revisa tu .env "
            "(API_FOOTBALL_ENABLED=true, API_FOOTBALL_KEY=...) antes de seguir."
        )
        return

    client = ApiFootballClient()

    if odds_for_fixture is not None:
        print(f"\n=== Cuotas RAW para fixture_id={odds_for_fixture} ===")
        odds_response = client._get("/odds", {"fixture": odds_for_fixture})
        if not odds_response:
            print("La API no devolvio NINGUNA cuota para este fixture.")
            return
        names_to_bookmakers: dict[str, set[str]] = {}
        for entry in odds_response:
            for bookmaker in entry.get("bookmakers", []):
                bk_label = f"{bookmaker.get('id')}:{bookmaker.get('name')}"
                for bet in bookmaker.get("bets", []):
                    names_to_bookmakers.setdefault(bet.get("name", ""), set()).add(bk_label)
        for name, books in sorted(names_to_bookmakers.items()):
            print(f"  {name!r}: {len(books)} casas -> {sorted(books)[:5]}")
        return

    print("\n=== /leagues?country=World (catalogo real de selecciones) ===")
    leagues = client._get("/leagues", {"country": "World"})
    print(f"Total competiciones devueltas: {len(leagues)}")

    of_interest = [
        entry
        for entry in leagues
        if any(kw in entry["league"]["name"].lower() for kw in INTEREST_KEYWORDS)
    ]
    print(f"\n=== De interés para 'Selecciones' ({len(of_interest)} de {len(leagues)}) ===")
    for entry in of_interest:
        _print_league_summary(entry)

    print("\n=== Resto del catalogo real (para no asumir que la lista de interés es completa) ===")
    rest = [entry for entry in leagues if entry not in of_interest]
    for entry in rest:
        league = entry["league"]
        print(f"  id={league['id']}  nombre={league['name']!r}  tipo={league.get('type')}")

    print(
        "\nSiguiente paso sugerido: para 1-2 competiciones de la lista de interés con "
        "coverage.fixtures=True, pedir /fixtures?league=<id>&season=<year>&next=10 para ver "
        "partidos reales programados antes de decidir cuales soportar."
    )
    for entry in of_interest:
        league = entry["league"]
        seasons = entry.get("seasons", [])
        if not seasons or not seasons[-1].get("coverage", {}).get("fixtures"):
            continue
        season_year = seasons[-1]["year"]
        fixtures = client._get(
            "/fixtures", {"league": league["id"], "season": season_year, "next": 10}
        )
        print(f"\n--- Proximos partidos reales: {league['name']!r} (season={season_year}) ---")
        if not fixtures:
            print("  (ninguno programado en este momento)")
            continue
        for payload in fixtures:
            fixture = payload["fixture"]
            round_name = payload["league"].get("round")
            print(
                f"  {payload['teams']['home']['name']} vs {payload['teams']['away']['name']} "
                f"({fixture['date']}) -- fixture_id={fixture['id']} round={round_name}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--odds-for-fixture", type=int, default=None)
    args = parser.parse_args()
    main(odds_for_fixture=args.odds_for_fixture)
