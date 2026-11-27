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
from backend.app.normalization.competitions import API_CANDIDATE_COMPETITIONS, CONTINENTAL_CHAMPIONS, EXPERIMENTAL_CATALOG, EXPERIMENTAL_CONTINENTAL, LIGA_F, competition_team_name
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

# Seccion "Rendimiento del modelo" (pedido real del usuario): bug real
# reportado -- un partido aplazado o cancelado quedaba indistinguible de
# "scheduled" para siempre (nunca se marcaba `finished`, pero tampoco nada
# lo distinguia de un partido normal todavia por jugar), asi que sus
# predicciones quedaban "pendientes" eternamente y nunca se liberaban.
# "CANC"/"ABD"/"AWD"/"WO" son definitivos (ese partido, con ese id, nunca
# va a dar un resultado real) -- sus predicciones se liquidan como "void"
# (ver evaluation_service.py::void_cancelled_predictions). "PST"/"SUSP"/
# "INT" NO se tratan igual a proposito: API-Football normalmente
# REPROGRAMA un partido aplazado bajo el MISMO fixture id (vuelve a "NS"
# con fecha nueva) -- marcarlo "cancelled" demasiado pronto liquidaria
# como anulada una prediccion que en realidad se jugara mas tarde,
# violando la inmutabilidad (seccion 2 del brief: nunca se retoca una
# prediccion ya hecha). Mientras tanto queda "postponed": ni se liquida
# ni se vuelve a tratar como "scheduled" para generar NUEVAS predicciones
# o casar cuotas nuevas (ver data_service.py, filtros `status ==
# "scheduled"`).
#
# Deliberadamente NO se distingue un estado "live" aparte de "scheduled"
# (aunque API-Football si lo reporta: "1H"/"HT"/"2H"/"ET"/"BT"/"P"): nada
# en este sistema consume ese detalle hoy (no hay apuestas en vivo ni
# cuotas in-play) y anhadirlo fragmentaria los muchos filtros existentes
# `Match.status == "scheduled"` (listados de proximos partidos,
# generacion de predicciones, casar cuotas...) sin ningun beneficio real
# todavia -- ver limitaciones conocidas en architecture_audit.md.
CANCELLED_STATUSES = {"CANC", "ABD", "AWD", "WO"}
POSTPONED_STATUSES = {"PST", "SUSP", "INT"}


def _match_status(status_short: str | None) -> str:
    if status_short in FINISHED_STATUSES:
        return "finished"
    if status_short in CANCELLED_STATUSES:
        return "cancelled"
    if status_short in POSTPONED_STATUSES:
        return "postponed"
    return "scheduled"  # "NS", en juego ("1H"/"2H"/...), o cualquier otro no reconocido

# "Regular Season - 12" -> 12. Generico (numero al final de la cadena) en
# vez de asumir el texto exacto -- ver docstring del modulo.
MATCHDAY_RE = re.compile(r"(\d+)\s*$")


def parse_matchday(round_name: str) -> int | None:
    match = MATCHDAY_RE.search(round_name or "")
    return int(match.group(1)) if match else None


class ApiFootballMatchProvider(ApiFootballClient, DataProvider):
    name = "api_football"

    fixture_window = None

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        league_id = LEAGUE_IDS.get(competition_code)
        if league_id is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")
        season_year = int(season_label.split("/")[0])

        params = {"league": league_id, "season": season_year}
        if self.fixture_window:
            start, end = self.fixture_window
            params.update({"from": start.isoformat(), "to": end.isoformat()})
        fixtures = self._get("/fixtures", params)

        records: list[RawMatchRecord] = []
        unrecognized_rounds = 0
        for payload in fixtures:
            fixture = payload["fixture"]
            status_short = fixture.get("status", {}).get("short")
            finished = status_short in FINISHED_STATUSES
            match_status = _match_status(status_short)
            goals = payload.get("goals", {}) or {}
            halftime = (payload.get("score", {}) or {}).get("halftime") or {}
            round_name = (payload.get("league", {}) or {}).get("round", "")
            matchday = parse_matchday(round_name)
            champions = competition_code in (*CONTINENTAL_CHAMPIONS, *EXPERIMENTAL_CONTINENTAL)
            if champions:
                if not re.match(r"^(League Stage|League Phase|Group Stage|Group [A-Z]) - \d+$", round_name or "", re.I):
                    matchday = None
                if status_short in ("AET", "PEN"):
                    goals = (payload.get("score") or {}).get("fulltime") or {}
                if status_short in {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"}:
                    match_status = "live"
            api_candidate = competition_code in API_CANDIDATE_COMPETITIONS or competition_code in EXPERIMENTAL_CATALOG
            if (competition_code == LIGA_F or competition_code in EXPERIMENTAL_CATALOG) and status_short in {
                "1H", "HT", "2H", "ET", "BT", "P", "LIVE",
            }:
                match_status = "live"
            if matchday is None and not champions:
                unrecognized_rounds += 1

            records.append(
                RawMatchRecord(
                    provider=self.name,
                    provider_id=f"api-football-{fixture['id']}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=fixture["date"],
                    home_team_raw=competition_team_name(payload["teams"]["home"]["name"], competition_code),
                    away_team_raw=competition_team_name(payload["teams"]["away"]["name"], competition_code),
                    home_goals=goals.get("home") if finished else None,
                    away_goals=goals.get("away") if finished else None,
                    home_goals_ht=halftime.get("home"),
                    away_goals_ht=halftime.get("away"),
                    matchday=matchday,
                    status=match_status,
                    round_name=round_name if api_candidate else None,
                    provider_status=status_short if api_candidate else None,
                    # API-Football trae el escudo real en el mismo payload
                    # del fixture, sin request ni coste adicional (pedido
                    # real del usuario: escudos reales via API-Football,
                    # ya que el proyecto tiene key de pago activa).
                    home_logo_url=payload["teams"]["home"].get("logo"),
                    away_logo_url=payload["teams"]["away"].get("logo"),
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
