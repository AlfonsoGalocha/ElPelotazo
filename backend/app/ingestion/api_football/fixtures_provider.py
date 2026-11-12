"""Adapter de FIXTURES + RESULTADOS reales (API-Football / api-sports.io).

Sustituye a `openfootball/football.json` (`football_data/fixtures_provider.py`)
y reduce la dependencia del mirror historico de `football_data/history_dataset.py`
para la TEMPORADA EN CURSO (ese mirror es mantenido por un tercero en
GitHub y puede quedarse semanas sin actualizar -- bug real reportado por
el usuario: histórico parado en partidos de septiembre con jornadas mas
recientes ya jugadas). Las temporadas YA CERRADAS (historico profundo
para entrenar) siguen viniendo del mirror -- no hay razon para pagar por
datos que ya se tienen y que no cambian.

Trae, en una sola llamada a `/fixtures?league=X&season=Y`, TODOS los
partidos de la temporada (jugados y por jugar) con su estado real --
nunca hace falta una ventana de fechas aqui (a diferencia de `/odds`,
`/fixtures` no se factura por partido).

IMPORTANTE -- sin verificar end-to-end (ver `client.py`): el entorno
donde se escribio este adapter no tiene acceso de red a
api-football.com/api-sports.io, asi que el formato exacto de
`league.round` ("Regular Season - 12", segun la documentacion publica)
se interpreta con una regex generica (numero final de la cadena) en vez
de asumir el texto exacto -- si el formato real difiere, `parse_matchday`
devuelve `None` en vez de un numero inventado, y se loguea con WARNING
cuantos partidos quedaron sin jornada reconocida.
"""

from __future__ import annotations

import re

from backend.app.ingestion.api_football.client import LEAGUE_IDS, ApiFootballClient
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

# Estados de partido terminado (API-Football): "FT" (tiempo reglamentario),
# "AET" (prorroga), "PEN" (penaltis) -- ninguna de las 5 ligas del MVP usa
# prorroga/penaltis en liga regular, pero se incluyen por si acaso en
# copas que se anhadan mas adelante. Cualquier otro estado ("NS" sin
# empezar, "1H"/"2H" en juego, "PST" postpuesto, "CANC" cancelado...) se
# trata como NO finalizado -- home_goals/away_goals quedan `None`, nunca
# se infiere un resultado de un partido que no ha terminado de verdad.
FINISHED_STATUSES = {"FT", "AET", "PEN"}

# "Regular Season - 12" -> 12. Generico (numero al final de la cadena) en
# vez de asumir el texto exacto -- ver docstring del modulo.
MATCHDAY_RE = re.compile(r"(\d+)\s*$")


def parse_matchday(round_name: str) -> int | None:
    match = MATCHDAY_RE.search(round_name or "")
    return int(match.group(1)) if match else None


class ApiFootballMatchProvider(ApiFootballClient, DataProvider):
    name = "api_football"

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        league_id = LEAGUE_IDS.get(competition_code)
        if league_id is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")
        season_year = int(season_label.split("/")[0])

        fixtures = self._get("/fixtures", {"league": league_id, "season": season_year})

        records: list[RawMatchRecord] = []
        unrecognized_rounds = 0
        for payload in fixtures:
            fixture = payload["fixture"]
            status_short = fixture.get("status", {}).get("short")
            finished = status_short in FINISHED_STATUSES
            goals = payload.get("goals", {}) or {}
            halftime = (payload.get("score", {}) or {}).get("halftime") or {}
            round_name = (payload.get("league", {}) or {}).get("round", "")
            matchday = parse_matchday(round_name)
            if matchday is None:
                unrecognized_rounds += 1

            records.append(
                RawMatchRecord(
                    provider=self.name,
                    provider_id=f"api-football-{fixture['id']}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=fixture["date"],
                    home_team_raw=payload["teams"]["home"]["name"],
                    away_team_raw=payload["teams"]["away"]["name"],
                    home_goals=goals.get("home") if finished else None,
                    away_goals=goals.get("away") if finished else None,
                    home_goals_ht=halftime.get("home"),
                    away_goals_ht=halftime.get("away"),
                    matchday=matchday,
                )
            )

        if fixtures and unrecognized_rounds:
            logger.warning(
                "ingestion.api_football.unrecognized_round_format: "
                "competition=%s unrecognized=%d of %d fixtures",
                competition_code,
                unrecognized_rounds,
                len(fixtures),
            )
        return records
