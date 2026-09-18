"""Adapter para el dataset "Club Football Match Data" (Adam Gabor, 2026).

Este dataset es un MIRROR real y publico de football-data.co.uk: mismo
origen de datos (resultados, estadisticas de partido y cuotas de Bet365),
pero agregado en un unico CSV historico (2000/01 -> temporada actual) y
publicado en GitHub, lo que lo hace descargable via `raw.githubusercontent.com`.

Por que este adapter existe ADEMAS de `provider.py` (football-data.co.uk
directo): el entorno donde se desarrollo este proyecto tiene el egress de
red restringido a un allowlist que incluye GitHub pero NO
football-data.co.uk. Este adapter permite poblar la base de datos con
partidos REALES (no sinteticos) sin depender de esa restriccion. En un
entorno sin restricciones de red, ambos adapters son intercambiables para
las 5 ligas del MVP; este se prefiere solo por disponibilidad de red.

Fuente: https://github.com/xgabora/Club-Football-Match-Data-2000-2025
Cita: Gabor, A. (2026). Club Football Match Data.
Licencia/uso: dataset publico de investigacion, se solicita citar la fuente
(ver README del repositorio). No se redistribuye aqui: se descarga y
cachea localmente en `data/external/`.

Cobertura de cuotas: el dataset solo incluye cuotas de Bet365 para 1X2 y
Over/Under 2.5 goles (una unica linea de goles). Los mercados Over 1.5,
Over 3.5 y BTTS del MVP NO tienen `market_probability` real desde esta
fuente (se deja `None` explicitamente, nunca se inventa una cuota).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.config.settings import REPO_ROOT
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.ingestion.football_data.provider import COMPETITION_DIV_CODES
from backend.app.utils.dates import season_label as season_label_from_date
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

DATASET_URL = (
    "https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data-2000-2025"
    "/master/data/Matches.csv"
)
CACHE_PATH = REPO_ROOT / "data" / "external" / "club_football_match_data_matches.csv"


class ClubFootballMatchDataProvider(DataProvider):
    name = "club_football_match_data"

    def __init__(self, http_client: httpx.Client | None = None, cache_path: Path = CACHE_PATH) -> None:
        self._client = http_client or httpx.Client(timeout=120.0)
        self._cache_path = cache_path
        self._df: pd.DataFrame | None = None

    def is_available(self) -> bool:
        return True

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def _download(self) -> bytes:
        logger.info("ingestion.club_football_match_data.download", extra={"url": DATASET_URL})
        response = self._client.get(DATASET_URL)
        response.raise_for_status()
        return response.content

    def _load_dataframe(self, force_refresh: bool = False) -> pd.DataFrame:
        if self._df is not None and not force_refresh:
            return self._df

        if force_refresh or not self._cache_path.exists():
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_bytes(self._download())
            logger.info("ingestion.club_football_match_data.cached", extra={"path": str(self._cache_path)})

        df = pd.read_csv(self._cache_path, low_memory=False)
        df["MatchDate"] = pd.to_datetime(df["MatchDate"])
        df["season_label"] = df["MatchDate"].apply(lambda d: season_label_from_date(d.date()))
        self._df = df
        return df

    def refresh_cache(self) -> None:
        """Fuerza una re-descarga real en la proxima llamada a
        `fetch_matches`, ignorando el cache en disco.

        BUG REAL corregido: una vez descargado el CSV la primera vez,
        `_load_dataframe()` nunca volvia a comprobar si habia partidos
        nuevos jugados -- `force_refresh` existia como parametro pero
        nada lo pasaba nunca en `True`. El sintoma exacto: `football-edge
        update` (o `refresh`, que lo incluye) se podia re-ejecutar cien
        veces despues de que terminara una jornada entera y JAMAS se
        actualizaban los resultados reales, silenciosamente -- el cache
        de 45MB en `data/external/` se quedaba congelado en el estado del
        primer `update` que se ejecuto en el proyecto. Sin esto, no hay
        forma de que "recopilar los datos reales tras la jornada"
        funcione nunca, por mucho que se reentrene despues."""
        self._df = None
        if self._cache_path.exists():
            self._cache_path.unlink()

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        div_code = COMPETITION_DIV_CODES.get(competition_code)
        if div_code is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")

        df = self._load_dataframe()
        rows = df[(df["Division"] == div_code) & (df["season_label"] == season_label)]
        return [self._row_to_record(row, competition_code, season_label) for row in rows.itertuples()]

    @staticmethod
    def _row_to_record(row, competition_code: str, season_label: str) -> RawMatchRecord:
        match_date = row.MatchDate.date().isoformat()
        provider_id = f"{competition_code}:{season_label}:{match_date}:{row.HomeTeam}:{row.AwayTeam}"

        odds: list[RawOddsRecord] = []
        if pd.notna(row.OddHome):
            odds.append(RawOddsRecord("bet365", "match_result", None, "home", float(row.OddHome)))
        if pd.notna(row.OddDraw):
            odds.append(RawOddsRecord("bet365", "match_result", None, "draw", float(row.OddDraw)))
        if pd.notna(row.OddAway):
            odds.append(RawOddsRecord("bet365", "match_result", None, "away", float(row.OddAway)))
        if pd.notna(row.Over25):
            odds.append(RawOddsRecord("bet365", "over_under_goals", 2.5, "over", float(row.Over25)))
        if pd.notna(row.Under25):
            odds.append(RawOddsRecord("bet365", "over_under_goals", 2.5, "under", float(row.Under25)))

        return RawMatchRecord(
            provider="club_football_match_data",
            provider_id=provider_id,
            competition_code=competition_code,
            season_label=season_label,
            date=match_date,
            home_team_raw=str(row.HomeTeam),
            away_team_raw=str(row.AwayTeam),
            home_goals=_to_int(row.FTHome),
            away_goals=_to_int(row.FTAway),
            home_goals_ht=_to_int(row.HTHome),
            away_goals_ht=_to_int(row.HTAway),
            referee_raw=None,
            home_shots=_to_int(row.HomeShots),
            away_shots=_to_int(row.AwayShots),
            home_shots_on_target=_to_int(row.HomeTarget),
            away_shots_on_target=_to_int(row.AwayTarget),
            home_corners=_to_int(row.HomeCorners),
            away_corners=_to_int(row.AwayCorners),
            home_fouls=_to_int(row.HomeFouls),
            away_fouls=_to_int(row.AwayFouls),
            home_yellow_cards=_to_int(row.HomeYellow),
            away_yellow_cards=_to_int(row.AwayYellow),
            home_red_cards=_to_int(row.HomeRed),
            away_red_cards=_to_int(row.AwayRed),
            odds=odds,
        )


def _to_int(value) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)
