"""Diagnostico puntual: para UN partido concreto (el match_id que ves en
la URL /matches/<id>), vuelca la respuesta REAL y cruda de API-Football
para tarjetas y corners -- sin ningun parseo nuestro -- para poder
arreglar con datos de verdad en vez de a ciegas (ver el aviso en la
cabecera de odds_provider.py: "sin verificar end-to-end").

Pensado para dos preguntas concretas reportadas por el usuario:
1. ¿Por que nunca hay cuota real de tarjetas? ¿La API no ofrece ese
   mercado para este partido, o lo llama de otra forma que no
   reconocemos?
2. ¿Por que algunas lineas de corners salen con cuotas absurdas (p.ej.
   "mas de 8.5" mas cara que "mas de 9.5", imposible en la realidad)?
   ¿Vienen de verdad de una casa con ese precio, o es un fallo de
   parseo que mezcla lineas/mercados distintos bajo la misma clave?

Uso (dentro del contenedor, con la API key real):
    docker compose exec backend python scripts/debug_match_odds.py <match_id>
"""

from __future__ import annotations

import json
import sys

from backend.app.config.settings import get_settings
from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.ingestion.api_football.odds_provider import ApiFootballOddsProvider


def main(match_id: int) -> None:
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

        print("\n=== Lo que YA tenemos guardado en MatchOdds (tarjetas/corners) ===")
        stored = (
            db.query(MatchOdds)
            .filter(
                MatchOdds.match_id == match.id,
                MatchOdds.market.in_(("cards_total", "corners_total")),
            )
            .order_by(MatchOdds.market, MatchOdds.line, MatchOdds.selection, MatchOdds.bookmaker)
            .all()
        )
        if not stored:
            print("  (nada guardado)")
        for row in stored:
            print(
                f"  {row.market} line={row.line} selection={row.selection} "
                f"bookmaker={row.bookmaker!r} price={row.price}"
            )

    if provider != "api_football" or not provider_id.startswith("api-football-"):
        print(
            "\nEste partido no vino originalmente de api_football (provider_id no empieza por "
            "'api-football-') -- no puedo pedir /odds con un fixture_id fiable para el. Prueba con "
            "otro match_id cuyo 'provider' sea 'api_football' (lo imprime arriba)."
        )
        return
    fixture_id = int(provider_id.removeprefix("api-football-"))

    print(
        f"\n=== Respuesta RAW de /odds?fixture={fixture_id} "
        "(api-football, sin parseo nuestro) ==="
    )
    client = ApiFootballOddsProvider()
    odds_response = client._get("/odds", {"fixture": fixture_id})
    if not odds_response:
        print("  La API no devolvio NINGUNA cuota para este partido (respuesta vacia).")
        return

    names_to_bookmakers: dict[str, set[str]] = {}
    card_corner_blocks: list[dict] = []
    for entry in odds_response:
        for bookmaker in entry.get("bookmakers", []):
            bk_label = f"{bookmaker.get('id')}:{bookmaker.get('name')}"
            for bet in bookmaker.get("bets", []):
                name = bet.get("name", "")
                names_to_bookmakers.setdefault(name, set()).add(bk_label)
                if "card" in name.lower() or "corner" in name.lower():
                    card_corner_blocks.append({"bookmaker": bk_label, "bet": bet})

    print("\n--- Todos los mercados que devuelve la API para este partido ---")
    for name in sorted(names_to_bookmakers):
        print(f"  {name!r}: {len(names_to_bookmakers[name])} casas")

    print("\n--- Bloques RAW de tarjetas/corners (name/values tal cual los manda la API) ---")
    if not card_corner_blocks:
        print("  NINGUNO -- la API no ofrece ningun mercado con 'card'/'corner' en el nombre para "
              "este partido concreto (puede que sea especifico de este partido/liga, no un bug).")
    else:
        print(json.dumps(card_corner_blocks, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Uso: python scripts/debug_match_odds.py <match_id>")
        sys.exit(1)
    main(int(sys.argv[1]))
