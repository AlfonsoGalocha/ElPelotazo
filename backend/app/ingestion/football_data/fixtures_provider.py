"""Adapter de FIXTURES reales (calendario, incl. partidos aun no jugados).

Fuente: openfootball/football.json (https://github.com/openfootball/football.json),
dominio publico, sin API key, auto-actualizado a diario desde datasets
Football.TXT mantenidos por la comunidad. A diferencia de
`history_dataset.py` (resultados ya jugados), esta fuente SI publica el
calendario completo de la temporada en curso con fechas futuras conocidas,
lo que permite alimentar "partidos de hoy" con partidos que realmente se
van a jugar (no solo backtesting sobre el pasado).

TAMBIEN trae el marcador de los partidos YA jugados de la temporada en
curso (seccion "resultados en tiempo real" -- problema real reportado por
un usuario: `history_dataset.py` es un mirror que puede tardar semanas en
reflejar los resultados de la temporada en curso -- ej. solo 31 partidos
de LaLiga ingeridos cuando ya se habian jugado muchos mas -- dejando
Historico/settlement parados). Como esta fuente SI se actualiza a diario,
`parse_payload` ahora ingiere esos partidos YA jugados con su marcador
real en vez de descartarlos, usando el MISMO `provider_id` determinista
que tenian de "programado" -> `_upsert_match` (services/data_service.py)
los actualiza en el sitio, nunca los duplica. Cuando `history_dataset.py`
los trae mas tarde con estadisticas/cuotas completas, `_find_absorbable_duplicate`
fusiona ambos en la misma fila (mismo mecanismo ya existente para el caso
inverso). Estos partidos NUNCA traen estadisticas de partido (tiros,
corners...) ni cuotas -- se dejan `None`, nunca inventadas; las features
siguen calculandose sobre todo del HISTORICO real cuando esta disponible.

Limitaciones honestas:
- Es un dataset mantenido por voluntarios: puede llevar retraso en cambios
  de ultima hora (aplazamientos, horarios). Para uso en produccion real
  conviene cruzarlo con una fuente oficial antes de apostar dinero real.
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
    def _parse_final_score(match: dict) -> tuple[int, int] | None:
        """`{"score": {"ft": [home, away]}}` es el formato estable de
        openfootball/football.json. `None` si el partido no tiene score
        (aun no jugado) o si el campo viene en un formato inesperado --
        nunca se inventa un marcador."""
        score = match.get("score")
        if not isinstance(score, dict):
            return None
        ft = score.get("ft")
        if not isinstance(ft, list) or len(ft) != 2:
            return None
        try:
            return int(ft[0]), int(ft[1])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def parse_payload(
        payload: dict, competition_code: str, season_label: str, min_date: dt.date | None = None
    ) -> list[RawMatchRecord]:
        """Devuelve tanto partidos SIN JUGAR (fecha `>= min_date`, por
        defecto hoy) como partidos YA JUGADOS con marcador real, de la
        MISMA temporada.

        Antes se descartaban los partidos ya jugados a proposito (para que
        `history_dataset.py` fuera la unica fuente de resultados, con
        estadisticas y cuotas completas). Problema real detectado: ese
        mirror puede tardar SEMANAS en reflejar la temporada en curso (caso
        real: solo 31 partidos de LaLiga ingeridos con muchos mas ya
        jugados), dejando Historico/settlement parados indefinidamente.
        Como openfootball SI se actualiza a diario, ahora se ingieren esos
        resultados como TAPAGUJERO -- sin estadisticas de partido ni
        cuotas (se dejan `None`, nunca inventadas) -- hasta que
        `history_dataset.py` los traiga con mas detalle y los fusione en la
        misma fila (`_find_absorbable_duplicate`,
        services/data_service.py). El `provider_id` es el MISMO
        deterministico que el partido ya tenia de "programado", asi que
        `_upsert_match` lo actualiza en el sitio -- nunca crea una fila
        duplicada.

        El filtro por `min_date` sigue aplicando SOLO a partidos SIN score:
        un dataset comunitario puede llevar "score" ausente durante dias
        despues de jugado un partido (retraso de actualizacion), lo que sin
        este filtro haria aparecer partidos YA JUGADOS (fecha pasada, sin
        score todavia) como "programados". Un partido CON score valido se
        incluye siempre, sin importar cuanto tiempo haya pasado.
        """
        if min_date is None:
            min_date = dt.date.today()

        records: list[RawMatchRecord] = []
        for match in payload.get("matches", []):
            final_score = OpenFootballFixturesProvider._parse_final_score(match)
            date_str = match.get("date")
            if not date_str:
                continue
            time_str = match.get("time", "00:00")
            try:
                kickoff = dt.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            except ValueError:
                kickoff = dt.datetime.strptime(date_str, "%Y-%m-%d")

            if final_score is None and kickoff.date() < min_date:
                continue  # fecha pasada pero sin "score": dato de la fuente desactualizado

            round_name = match.get("round", "")
            provider_id = f"{competition_code}:{season_label}:{date_str}:{match['team1']}:{match['team2']}:{round_name}"
            home_goals, away_goals = final_score if final_score is not None else (None, None)

            records.append(
                RawMatchRecord(
                    provider="openfootball_fixtures",
                    provider_id=provider_id,
                    competition_code=competition_code,
                    season_label=season_label,
                    date=kickoff.isoformat(),
                    home_team_raw=match["team1"],
                    away_team_raw=match["team2"],
                    home_goals=home_goals,
                    away_goals=away_goals,
                    matchday=parse_matchday(round_name),
                )
            )
        return records
