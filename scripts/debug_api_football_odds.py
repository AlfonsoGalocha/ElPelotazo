"""Diagnostico puntual (NO parte del pipeline): vuelca la respuesta REAL
de API-Football para un partido, para poder arreglar odds_provider.py con
datos de verdad en vez de a ciegas -- ver el aviso en la cabecera de ese
modulo ("sin verificar end-to-end", el entorno de desarrollo no tiene
acceso de red a api-football.com/api-sports.io).

Uso (dentro del contenedor, que SI tiene tu API key real):
    docker compose exec backend python scripts/debug_api_football_odds.py laliga

Imprime, para el PRIMER partido programado de esa liga:
- Si API-Football esta realmente activado (flag + key presentes).
- Todos los nombres de mercado ("bet name") que devuelve la API para ese
  partido, con cuantas casas distintas aparecen bajo cada uno.
- El bloque RAW (sin ningun parseo nuestro) de los mercados cuyo nombre
  contiene "card" o "corner" -- para ver el formato exacto de linea/
  seleccion y si una misma casa aparece mas de una vez con IDs distintos.
"""

from __future__ import annotations

import json
import sys

from backend.app.config.settings import get_settings
from backend.app.ingestion.api_football.client import LEAGUE_IDS
from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider
from backend.app.utils.dates import season_label


def main(competition_code: str) -> None:
    settings = get_settings()
    print(f"api_football_enabled={settings.api_football_enabled} "
          f"key_present={bool(settings.api_football_key)} "
          f"use_rapidapi={settings.api_football_use_rapidapi}")
    if not settings.api_football_enabled or not settings.api_football_key:
        print("API-Football no esta activado o falta la key -- revisa tu .env "
              "(API_FOOTBALL_ENABLED=true, API_FOOTBALL_KEY=...) antes de seguir.")
        return

    if competition_code not in LEAGUE_IDS:
        print(f"Competicion no soportada: {competition_code!r}. Opciones: {sorted(LEAGUE_IDS)}")
        return

    provider = ApiFootballOddsProvider()
    import datetime as dt

    today = dt.date.today()
    season_year = int(season_label(today).split("/")[0])

    fixtures = provider._get(
        "/fixtures",
        {
            "league": LEAGUE_IDS[competition_code],
            "season": season_year,
            "from": today.isoformat(),
            "to": (today + dt.timedelta(days=45)).isoformat(),
        },
    )
    if not fixtures:
        print(f"Sin partidos programados para {competition_code!r} en los proximos 45 dias.")
        return

    fixture = fixtures[0]
    fixture_id = fixture["fixture"]["id"]
    print(
        f"Partido: {fixture['teams']['home']['name']} vs {fixture['teams']['away']['name']} "
        f"({fixture['fixture']['date']}) -- fixture_id={fixture_id}"
    )

    odds_response = provider._get("/odds", {"fixture": fixture_id})
    if not odds_response:
        print("La API no devolvio NINGUNA cuota para este partido (respuesta vacia).")
        return

    names_to_bookmakers: dict[str, set[str]] = {}
    raw_card_corner_blocks: list[dict] = []
    for entry in odds_response:
        for bookmaker in entry.get("bookmakers", []):
            bk_label = f"{bookmaker.get('id')}:{bookmaker.get('name')}"
            for bet in bookmaker.get("bets", []):
                name = bet.get("name", "")
                names_to_bookmakers.setdefault(name, set()).add(bk_label)
                if "card" in name.lower() or "corner" in name.lower():
                    raw_card_corner_blocks.append({"bookmaker": bk_label, "bet": bet})

    print("\n=== Mercados vistos (nombre -> casas que lo ofrecen) ===")
    for name, books in sorted(names_to_bookmakers.items()):
        extra = " ..." if len(books) > 5 else ""
        print(f"  {name!r}: {len(books)} casas -> {sorted(books)[:5]}{extra}")

    print("\n=== Bloques RAW de tarjetas/corners (sin ningun parseo nuestro) ===")
    if not raw_card_corner_blocks:
        print("  NINGUNO -- la API no devuelve ningun mercado con 'card'/'corner' en el nombre "
              "para este partido. Puede que esta liga/partido concreto no los tenga disponibles "
              "(no todos los partidos tienen el mismo catalogo de mercados), prueba con otro "
              "partido/liga antes de concluir que es un bug.")
    else:
        print(json.dumps(raw_card_corner_blocks[:8], indent=2, ensure_ascii=False))
        if len(raw_card_corner_blocks) > 8:
            print(f"  ... ({len(raw_card_corner_blocks) - 8} bloques mas, omitidos)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python scripts/debug_api_football_odds.py <competition_code>")
        print("competition_code: laliga | premier_league | bundesliga | serie_a | ligue_1")
        sys.exit(1)
    main(sys.argv[1])
