"""Adapter de FIXTURES reales (calendario, incl. partidos aun no jugados).

Fuente: openfootball/football.json (https://github.com/openfootball/football.json),
dominio publico, sin API key, auto-actualizado a diario desde datasets
Football.TXT mantenidos por la comunidad. A diferencia de
`history_dataset.py` (resultados ya jugados), esta fuente SI publica el
calendario completo de la temporada en curso con fechas futuras conocidas,
lo que permite alimentar "partidos de hoy" con partidos que realmente se
van a jugar (no solo backtesting sobre el pasado).

Limitaciones honestas:
- Es un dataset mantenido por voluntarios: puede llevar retraso en cambios
  de ultima hora (aplazamientos, horarios). Para uso en produccion real
  conviene cruzarlo con una fuente oficial antes de apostar dinero real.
- No trae estadisticas del partido (tiros, corners...) ni cuotas: solo
  equipos, fecha/hora y, si ya se jugo, el resultado. Las features se
  calculan igualmente a partir del HISTORICO real ya ingerido
  (`history_dataset.py`), no de este fixture.
- Los nombres de equipo son los oficiales completos ("Real Madrid CF") y
  deben resolverse al mismo team_id que el historico
  ("Real Madrid") via `normalization/teams.py::KNOWN_ALIASES` — critico
  para que el modelo tenga historial real de ese equipo en vez de arrancar
  en frio.
"""

from __future__ import annotations

import datetime as dt
import re

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"

# Competicion -> codigo de liga usado por openfootball/football.json.
COMPETITION_LEAGUE_CODES: dict[str, str] = {
    "laliga": "es.1",
    "premier_league": "en.1",
    "bundesliga": "de.1",
    "serie_a": "it.1",
    "ligue_1": "fr.1",
}


def season_label_to_openfootball(season_label: str) -> str:
    """"2026/27" -> "2026-27" (formato de carpeta del repo)."""
    start, end = season_label.split("/")
    return f"{start}-{end}"


def parse_matchday(round_name: str) -> int | None:
    """openfootball usa el formato "Matchday N" de forma consistente en las
    5 ligas del MVP (verificado contra el JSON real de cada una: es.1,
    en.1, de.1, it.1, fr.1 todas usan "Matchday N", nunca "Round N" ni
    nombres de fase de eliminatoria). Devuelve None ante cualquier formato
    inesperado en vez de asumir un numero (p.ej. competiciones con fases de
    grupos/eliminatorias que este adapter no cubre en el MVP)."""
    match = re.fullmatch(r"Matchday (\d+)", round_name.strip())
    return int(match.group(1)) if match else None


class OpenFootballFixturesProvider(DataProvider):
    name = "openfootball_fixtures"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=30.0)

    def is_available(self) -> bool:
        return True

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def _download(self, league_code: str, season_folder: str) -> dict:
        url = f"{BASE_URL}/{season_folder}/{league_code}.json"
        logger.info("ingestion.openfootball_fixtures.download", extra={"url": url})
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        league_code = COMPETITION_LEAGUE_CODES.get(competition_code)
        if league_code is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")

        season_folder = season_label_to_openfootball(season_label)
        payload = self._download(league_code, season_folder)
        return self.parse_payload(payload, competition_code, season_label)

    @staticmethod
    def parse_payload(
        payload: dict, competition_code: str, season_label: str, min_date: dt.date | None = None
    ) -> list[RawMatchRecord]:
        """Solo devuelve partidos SIN JUGAR (`score` ausente) y con fecha
        `>= min_date` (por defecto, hoy).

        Los partidos ya jugados de esta misma fuente se descartan a
        proposito: `history_dataset.py` ya los tiene con estadisticas
        completas y cuotas; ingerirlos tambien aqui (con un provider_id
        distinto) crearia una fila de partido DUPLICADA sin stats, inflando
        el conteo de partidos de cada equipo y corrompiendo las features de
        forma. Esta fuente existe solo para tapar el hueco de fixtures
        futuros que el dataset historico no puede cubrir.

        El filtro por `min_date` cubre un caso real detectado: un dataset
        comunitario puede llevar "score" ausente durante dias despues de
        jugado un partido (retraso de actualizacion), lo que sin este filtro
        haria aparecer partidos YA JUGADOS (fecha pasada) como "programados"
        en el buscador/calendario. Se descartan explicitamente en vez de
        presentarlos como si fueran a jugarse.
        """
        if min_date is None:
            min_date = dt.date.today()

        records: list[RawMatchRecord] = []
        for match in payload.get("matches", []):
            if match.get("score"):
                continue  # ya jugado: lo cubre history_dataset.py con mas detalle
            date_str = match.get("date")
            if not date_str:
                continue
            time_str = match.get("time", "00:00")
            try:
                kickoff = dt.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            except ValueError:
                kickoff = dt.datetime.strptime(date_str, "%Y-%m-%d")

            if kickoff.date() < min_date:
                continue  # fecha pasada pero sin "score": dato de la fuente desactualizado

            round_name = match.get("round", "")
            provider_id = f"{competition_code}:{season_label}:{date_str}:{match['team1']}:{match['team2']}:{round_name}"

            records.append(
                RawMatchRecord(
                    provider="openfootball_fixtures",
                    provider_id=provider_id,
                    competition_code=competition_code,
                    season_label=season_label,
                    date=kickoff.isoformat(),
                    home_team_raw=match["team1"],
                    away_team_raw=match["team2"],
                    home_goals=None,
                    away_goals=None,
                    matchday=parse_matchday(round_name),
                )
            )
        return records
