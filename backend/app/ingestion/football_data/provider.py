"""Adapter para football-data.co.uk.

Fuente elegida como principal para el MVP porque:
- Publica CSV publicos, sin autenticacion ni API key.
- Cubre las 5 ligas objetivo desde mediados de los 90 (sobra para el
  requisito de 2018/19 en adelante).
- Incluye cuotas de cierre de varias casas de apuestas (Bet365, Pinnacle,
  William Hill, Betfair Exchange...), imprescindible para el modulo de mercado.
- Formato estable desde hace anhos (columnas consistentes por temporada).

Limitaciones conocidas (documentadas tambien en docs/data_sources.md):
- No incluye xG. Para eso esta el adapter de Understat (deshabilitado por
  defecto, pendiente de validar estabilidad del scraping).
- Las cuotas son un snapshot de cierre agregado por partido, no series
  temporales de movimiento de linea.
- Terminos de uso: uso personal/educativo, sin garantias formales de SLA.
  No hacer scraping agresivo: un fetch por temporada, con backoff.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://www.football-data.co.uk/mmz4281"

# Competiciones soportadas en el MVP (Fase 1).
COMPETITION_DIV_CODES: dict[str, str] = {
    "laliga": "SP1",
    "premier_league": "E0",
    "bundesliga": "D1",
    "serie_a": "I1",
    "ligue_1": "F1",
}

# Bookmaker -> prefijo de columna en el CSV, para cuotas 1X2 y Over/Under 2.5.
_BOOKMAKER_PREFIXES = {
    "bet365": "B365",
    "pinnacle": "P",
    "william_hill": "WH",
    "betfair_exchange": "B",
}


def season_to_url_label(season_label: str) -> str:
    """"2023/24" -> "2324" (formato usado en las URLs de football-data.co.uk)."""
    start, end = season_label.split("/")
    return f"{start[-2:]}{end}"


class FootballDataCoUkProvider(DataProvider):
    name = "football_data_co_uk"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=30.0)

    def is_available(self) -> bool:
        return True  # no requiere API key

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def _download_csv(self, div_code: str, season_url_label: str) -> str:
        url = f"{BASE_URL}/{season_url_label}/{div_code}.csv"
        logger.info("ingestion.football_data.download", extra={"url": url})
        response = self._client.get(url)
        response.raise_for_status()
        return response.content.decode("utf-8-sig", errors="replace")

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        div_code = COMPETITION_DIV_CODES.get(competition_code)
        if div_code is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")
        csv_text = self._download_csv(div_code, season_to_url_label(season_label))
        return self.parse_csv(csv_text, competition_code, season_label)

    @staticmethod
    def parse_csv(csv_text: str, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        reader = csv.DictReader(io.StringIO(csv_text))
        records: list[RawMatchRecord] = []
        for row in reader:
            if not row.get("HomeTeam") or not row.get("Date"):
                continue
            match_date = _parse_date(row["Date"])
            if match_date is None:
                continue
            provider_id = f"{competition_code}:{season_label}:{match_date.isoformat()}:{row['HomeTeam']}:{row['AwayTeam']}"
            record = RawMatchRecord(
                provider="football_data_co_uk",
                provider_id=provider_id,
                competition_code=competition_code,
                season_label=season_label,
                date=match_date.isoformat(),
                home_team_raw=row["HomeTeam"].strip(),
                away_team_raw=row["AwayTeam"].strip(),
                home_goals=_to_int(row.get("FTHG")),
                away_goals=_to_int(row.get("FTAG")),
                home_goals_ht=_to_int(row.get("HTHG")),
                away_goals_ht=_to_int(row.get("HTAG")),
                referee_raw=(row.get("Referee") or "").strip() or None,
                home_shots=_to_int(row.get("HS")),
                away_shots=_to_int(row.get("AS")),
                home_shots_on_target=_to_int(row.get("HST")),
                away_shots_on_target=_to_int(row.get("AST")),
                home_corners=_to_int(row.get("HC")),
                away_corners=_to_int(row.get("AC")),
                home_fouls=_to_int(row.get("HF")),
                away_fouls=_to_int(row.get("AF")),
                home_yellow_cards=_to_int(row.get("HY")),
                away_yellow_cards=_to_int(row.get("AY")),
                home_red_cards=_to_int(row.get("HR")),
                away_red_cards=_to_int(row.get("AR")),
                odds=_extract_odds(row),
            )
            records.append(record)
        return records


def _parse_date(raw: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _to_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _extract_odds(row: dict[str, str]) -> list[RawOddsRecord]:
    odds: list[RawOddsRecord] = []
    for bookmaker, prefix in _BOOKMAKER_PREFIXES.items():
        home = _to_float(row.get(f"{prefix}H"))
        draw = _to_float(row.get(f"{prefix}D"))
        away = _to_float(row.get(f"{prefix}A"))
        if home:
            odds.append(RawOddsRecord(bookmaker, "match_result", None, "home", home))
        if draw:
            odds.append(RawOddsRecord(bookmaker, "match_result", None, "draw", draw))
        if away:
            odds.append(RawOddsRecord(bookmaker, "match_result", None, "away", away))

        over = _to_float(row.get(f"{prefix}>2.5"))
        under = _to_float(row.get(f"{prefix}<2.5"))
        if over:
            odds.append(RawOddsRecord(bookmaker, "over_under_goals", 2.5, "over", over))
        if under:
            odds.append(RawOddsRecord(bookmaker, "over_under_goals", 2.5, "under", under))
    return odds
