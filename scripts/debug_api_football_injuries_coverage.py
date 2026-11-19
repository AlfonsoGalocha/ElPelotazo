"""Reconocimiento puntual (NO parte del pipeline, solo lectura) para el
siguiente paso real de la mision: antes de integrar `/injuries` de
API-Football, hace falta confirmar con DATOS REALES si la API cubre
lesiones para las 5 ligas del MVP -- mismo principio "nunca inventar" ya
aplicado al resto de `debug_*` de esta carpeta (ver
`debug_api_football_national_teams.py`).

Este entorno de desarrollo tiene el egress de red restringido a un
allowlist que NO incluye api-football.com/api-sports.io (ver docstring
de `ApiFootballClient`), asi que este script NUNCA pudo ejecutarse desde
aqui -- debe correrlo el usuario con su API key real:

    docker compose exec backend python scripts/debug_api_football_injuries_coverage.py

Que hace, en UN SOLO request barato (`/leagues?id=<liga>`, una vez por
cada una de las 5 ligas del MVP -- nunca `/injuries` en si, que cuenta
contra la cuota y requiere un `team`/`fixture` concreto): imprime
`coverage.injuries` (si la clave existe) de la temporada mas reciente de
cada liga. Si la clave `injuries` no aparece dentro de `coverage` en la
respuesta real, este script lo deja explicito en vez de asumir
True/False -- la documentacion publica de API-Football no confirma por
plan/liga que cubre este endpoint, solo que existe desde abril de 2021 y
que conviene comprobar `coverage.injuries` liga por liga (ver
docs/mission_continuity.md).
"""

from __future__ import annotations

from backend.app.ingestion.api_football.client import LEAGUE_IDS, ApiFootballClient

MVP_CLUB_LEAGUES = ("laliga", "premier_league", "bundesliga", "serie_a", "ligue_1")


def main() -> None:
    client = ApiFootballClient()
    if not client.is_available():
        print("API_FOOTBALL_KEY no configurada -- no se puede comprobar nada real. Aborta.")
        return

    for code in MVP_CLUB_LEAGUES:
        league_id = LEAGUE_IDS[code]
        response = client._get("/leagues", {"id": league_id})
        if not response:
            print(f"{code} (id={league_id}): la API no devolvio nada.")
            continue
        seasons = response[0].get("seasons", [])
        if not seasons:
            print(f"{code} (id={league_id}): sin temporadas en la respuesta.")
            continue
        latest = seasons[-1]
        coverage = latest.get("coverage", {})
        has_injuries_key = "injuries" in coverage
        print(
            f"{code} (id={league_id})  year={latest.get('year')}  "
            f"coverage.injuries={coverage.get('injuries')!r}  "
            f"(clave presente en la respuesta: {has_injuries_key})  "
            f"-- coverage completo: {coverage}"
        )


if __name__ == "__main__":
    main()
