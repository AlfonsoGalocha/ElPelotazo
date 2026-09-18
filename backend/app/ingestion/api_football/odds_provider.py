"""Adapter de cuotas REALES de tarjetas y córners (API-Football / api-sports.io).

Por que hace falta una fuente DISTINTA de The Odds API: se comprobó que
The Odds API no ofrece mercados de tarjetas ni córners para fútbol en
NINGÚN plan (su catálogo se limita a resultado y totales de goles) — no
es una cuestión de configuración, esos mercados no existen ahí. API-Football
sí incluye, para bookmakers seleccionados, mercados de tipo "Cards Over/Under"
y "Corners Over/Under" en su endpoint `/odds`.

Fuente: https://www.api-football.com (o vía RapidAPI, mismos datos, distinta
autenticación — ver `Settings.api_football_use_rapidapi`). Plan gratuito:
100 requests/día — muy ajustado para 5 ligas si se pide toda la temporada,
por eso este adapter SOLO pide cuotas para una ventana de fechas concreta
(pensada para la jornada actual, ver `services/round_service.py`), nunca
la temporada completa: 1 request de fixtures + 1 request de odds POR
PARTIDO de esa ventana, típicamente ~10 partidos por liga.

IMPORTANTE — sin verificar end-to-end: el entorno donde se desarrolló este
adapter tiene el egress de red restringido a un allowlist que NO incluye
api-football.com/api-sports.io, así que este código se escribió siguiendo
la documentación pública de la API pero no pudo probarse contra la API
real. Por eso el matching de mercados de tarjetas/córners busca por
SUBCADENA en el nombre del mercado ("card"/"corner", insensible a
mayúsculas) en vez de un nombre exacto, y por REGEX genérico
"Over/Under N" en el valor (en vez de asumir un formato exacto): si el
nombre real difiere ligeramente de lo esperado, sigue funcionando; si no
se encuentra NADA parecido a tarjetas/córners tras procesar una liga
entera, se loguea con nivel WARNING la lista completa de nombres de
mercado vistos, para diagnosticar en un vistazo en vez de fallar en
silencio (la misma lección aprendida del bug del sport_key de Bundesliga
en The Odds API).
"""

from __future__ import annotations

import datetime as dt
import re

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.config.settings import get_settings
from backend.app.ingestion.base import FixtureOddsSnapshot, RawOddsRecord
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

DIRECT_BASE_URL = "https://v3.football.api-sports.io"
RAPIDAPI_BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

# IDs de liga de API-Football (estables, documentados publicamente).
LEAGUE_IDS: dict[str, int] = {
    "laliga": 140,
    "premier_league": 39,
    "bundesliga": 78,
    "serie_a": 135,
    "ligue_1": 61,
}

# Se busca por SUBCADENA (insensible a mayusculas), no por nombre exacto:
# API-Football puede nombrar el mercado de formas ligeramente distintas
# segun el bookmaker ("Cards Over/Under", "Total Cards", "Asian Cards"...).
MARKET_NAME_HINTS = {
    "cards_total": ("card",),
    "corners_total": ("corner",),
}

# "Over 3.5" / "Under 9.5" / "Over3.5" -> (selection, line)
OVER_UNDER_VALUE_RE = re.compile(r"(over|under)\s*([\d.]+)", re.IGNORECASE)


class ApiFootballOddsProvider:
    """No implementa `DataProvider` (esa interfaz es para ingesta de
    PARTIDOS; esta clase solo trae cuotas de mercados secundarios para
    partidos que YA existen en la BD, igual que `OddsApiProvider`)."""

    name = "api_football"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=30.0)

    def is_available(self) -> bool:
        settings = get_settings()
        return settings.api_football_enabled and bool(settings.api_football_key)

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        if settings.api_football_use_rapidapi:
            return {"X-RapidAPI-Key": settings.api_football_key or "", "X-RapidAPI-Host": RAPIDAPI_HOST}
        return {"x-apisports-key": settings.api_football_key or ""}

    def _base_url(self) -> str:
        settings = get_settings()
        return RAPIDAPI_BASE_URL if settings.api_football_use_rapidapi else DIRECT_BASE_URL

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def _get(self, path: str, params: dict) -> list[dict]:
        url = f"{self._base_url()}{path}"
        response = self._client.get(url, params=params, headers=self._headers())
        if response.status_code >= 400:
            logger.warning(
                "ingestion.api_football.error_response: path=%s status=%d body=%s",
                path,
                response.status_code,
                response.text[:500],
            )
        response.raise_for_status()
        return response.json().get("response", [])

    def fetch_secondary_odds(
        self, competition_code: str, season_year: int, date_from: dt.date, date_to: dt.date
    ) -> list[FixtureOddsSnapshot]:
        """Cuotas de tarjetas/córners para los partidos de una liga entre
        `date_from` y `date_to` (pensado para la ventana de la jornada
        actual, no la temporada completa: ver docstring del modulo sobre
        el limite de 100 requests/dia del plan gratuito)."""
        league_id = LEAGUE_IDS.get(competition_code)
        if league_id is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")

        fixtures = self._get(
            "/fixtures",
            {
                "league": league_id,
                "season": season_year,
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
            },
        )

        snapshots: list[FixtureOddsSnapshot] = []
        market_names_seen: set[str] = set()
        for fixture_payload in fixtures:
            fixture_id = fixture_payload["fixture"]["id"]
            home_name = fixture_payload["teams"]["home"]["name"]
            away_name = fixture_payload["teams"]["away"]["name"]
            commence_time = dt.datetime.fromisoformat(fixture_payload["fixture"]["date"])

            odds_response = self._get("/odds", {"fixture": fixture_id})
            odds_records, seen = self._parse_odds_response(odds_response)
            market_names_seen |= seen
            if odds_records:
                snapshots.append(FixtureOddsSnapshot(home_name, away_name, commence_time, odds_records))

        if fixtures and not any(s.odds for s in snapshots):
            logger.warning(
                "ingestion.api_football.no_cards_or_corners_market_found: "
                "competition=%s fixtures_checked=%d market_names_seen=%s",
                competition_code,
                len(fixtures),
                sorted(market_names_seen),
            )
        return snapshots

    @staticmethod
    def _parse_odds_response(odds_response: list[dict]) -> tuple[list[RawOddsRecord], set[str]]:
        records: list[RawOddsRecord] = []
        names_seen: set[str] = set()
        for entry in odds_response:
            for bookmaker in entry.get("bookmakers", []):
                bookmaker_key = bookmaker.get("name", "unknown")
                for bet in bookmaker.get("bets", []):
                    bet_name = bet.get("name", "")
                    names_seen.add(bet_name)
                    market_key = next(
                        (
                            key
                            for key, hints in MARKET_NAME_HINTS.items()
                            if any(hint in bet_name.lower() for hint in hints)
                        ),
                        None,
                    )
                    if market_key is None:
                        continue
                    for value in bet.get("values", []):
                        match = OVER_UNDER_VALUE_RE.search(str(value.get("value", "")))
                        if not match:
                            continue
                        selection = match.group(1).lower()
                        line = float(match.group(2))
                        try:
                            price = float(value.get("odd"))
                        except (TypeError, ValueError):
                            continue
                        records.append(RawOddsRecord(bookmaker_key, market_key, line, selection, price))
        return records, names_seen
