"""Diagnostico puntual (version generica de debug_match_odds.py, que solo
cubre tarjetas): para UN partido concreto y una PALABRA CLAVE de mercado,
vuelca la respuesta REAL y cruda de API-Football para los bets cuyo
nombre contenga esa palabra -- sin ningun parseo nuestro -- para poder
arreglar con datos de verdad en vez de a ciegas.

Pensado para una pregunta concreta reportada por el usuario: para el
mismo partido y el mismo bookmaker (p.ej. Bet365), la cuota real de
"Over 2.5 goles" que ve en una app de cuotas (bet365.com) no coincide con
la que guardamos nosotros para ese mismo bookmaker -- mientras que OTROS
bookmakers del mismo partido (BetVictor, Betfair) si coinciden. Para
saber si es un bug de parseo (linea/mercado equivocado para ciertos
bookmakers) o un dato real distinto, hace falta ver el bloque RAW tal
cual lo manda la API para ese bookmaker concreto.

Uso (dentro del contenedor, con la API key real):
    docker compose exec backend python scripts/debug_match_odds_market.py <match_id> <keyword>

Ejemplo (el caso reportado, goles):
    docker compose exec backend python scripts/debug_match_odds_market.py 62 over > /tmp/odds_debug.txt
    cat /tmp/odds_debug.txt

Si el log se corta en tu terminal, redirige a un fichero y pega ese
fichero en vez de la pantalla -- no depende del scrollback.
"""

from __future__ import annotations

import json
import sys

from backend.app.config.settings import get_settings
from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match
from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider


def main(match_id: int, keyword: str) -> None:
    settings = get_settings()
    if not settings.api_football_enabled or not settings.api_football_key:
        print("API-Football no esta activado o falta la key -- revisa tu .env.")
        return

    with session_scope() as db:
        match = db.get(Match, match_id)
        if match is None:
            print(f"No existe ningun partido con id={match_id}.")
            return
        home, away = match.home_team.canonical_name, match.away_team.canonical_name
        kickoff = match.kickoff_utc
        provider, provider_id = match.provider, match.provider_id

    print(
        f"Partido: {home} vs {away} ({kickoff}) -- "
        f"provider={provider!r} provider_id={provider_id!r}"
    )

    if provider != "api_football" or not provider_id.startswith("api-football-"):
        print(
            "\nEste partido no vino originalmente de api_football (provider_id no empieza por "
            "'api-football-') -- no puedo pedir /odds con un fixture_id fiable para el."
        )
        return
    fixture_id = int(provider_id.removeprefix("api-football-"))

    print(
        f"\n=== Respuesta RAW de /odds?fixture={fixture_id} "
        f"(api-football, sin parseo nuestro, filtrado por keyword={keyword!r}) ==="
    )
    client = ApiFootballOddsProvider()
    odds_response = client._get("/odds", {"fixture": fixture_id})
    if not odds_response:
        print("  La API no devolvio NINGUNA cuota para este partido (respuesta vacia).")
        return

    keyword_lower = keyword.lower()
    matching_blocks: list[dict] = []
    all_names: dict[str, set[str]] = {}
    for entry in odds_response:
        for bookmaker in entry.get("bookmakers", []):
            bk_label = f"{bookmaker.get('id')}:{bookmaker.get('name')}"
            for bet in bookmaker.get("bets", []):
                name = bet.get("name", "")
                all_names.setdefault(name, set()).add(bk_label)
                if keyword_lower in name.lower():
                    matching_blocks.append({"bookmaker": bk_label, "bet": bet})

    print("\n--- Todos los mercados que devuelve la API para este partido ---")
    for name in sorted(all_names):
        print(f"  {name!r}: {len(all_names[name])} casas")

    print(f"\n--- Bloques RAW que contienen {keyword!r} (name/values tal cual los manda la API) ---")
    if not matching_blocks:
        print(f"  NINGUNO -- ningun mercado contiene {keyword!r} en el nombre para este partido.")
    else:
        print(json.dumps(matching_blocks, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3 or not sys.argv[1].isdigit():
        print("Uso: python scripts/debug_match_odds_market.py <match_id> <keyword>")
        sys.exit(1)
    main(int(sys.argv[1]), sys.argv[2])
