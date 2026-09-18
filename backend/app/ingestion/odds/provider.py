"""Adapter para The Odds API (cuotas REALES de partidos futuros).

Sin esto, el sistema no tiene con que comparar la probabilidad del modelo:
la gracia del proyecto es precisamente detectar diferencias entre lo que
dice el modelo y lo que dice el mercado (edge estadistico). Los datasets
historicos usados en el resto del proyecto (ver ingestion/football_data/)
solo tienen cuotas de partidos YA JUGADOS; para partidos FUTUROS hace falta
una fuente de cuotas en vivo, y esa fuente necesita casi siempre una API
real (no hay ningun mirror de GitHub con cuotas actualizadas a diario).

Fuente: https://the-odds-api.com — plan gratuito: 500 requests/mes, cubre
1X2 (`h2h`) y Over/Under de goles (`totals`) para las 5 ligas del MVP.
Opcionalmente (ver `Settings.odds_api_fetch_additional_markets`), tambien
Over/Under a linea 1.5 (`alternate_totals`) y Ambos Marcan (`btts`).

Requiere registrarse (gratis) y configurar ODDS_API_KEY en `.env`. Sin key,
`is_available()` devuelve False y el resto del sistema sigue funcionando
(simplemente sin `market_probability`/`edge` para partidos futuros, exactamente
igual que hasta ahora).

IMPORTANTE — dos endpoints DISTINTOS, descubierto por un 422 real en
produccion: el endpoint masivo `/sports/{sport}/odds` (una request trae
TODOS los partidos de la liga) solo admite mercados "featured" (`h2h`,
`totals`, `spreads`); pedir un mercado "additional" como `alternate_totals`
o `btts` ahi devuelve 422 "Markets not supported by this endpoint" y
tumba TODA la peticion (tambien h2h/totals). Los mercados "additional"
solo se pueden pedir en el endpoint POR EVENTO,
`/sports/{sport}/events/{eventId}/odds` — una request POR PARTIDO, no
por liga. Por eso van en un metodo aparte (`_download_additional_markets`)
y detras de un flag desactivado por defecto: activarlo multiplica el
consumo de cuota (~1 request extra por partido en cartel, no por liga) y
podria agotar el plan gratuito de 500 req/mes rapidamente si se refresca
a menudo. `h2h`/`totals` via el endpoint masivo SI estan verificados
end-to-end en produccion (por el usuario) para las 5 ligas; el endpoint
por evento para `alternate_totals`/`btts` esta escrito siguiendo la
documentacion publica pero AUN NO verificado end-to-end.
"""

from __future__ import annotations

import datetime as dt

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.config.settings import get_settings
from backend.app.ingestion.base import DataProvider, FixtureOddsSnapshot, RawMatchRecord, RawOddsRecord
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4/sports"

COMPETITION_TO_SPORT_KEY: dict[str, str] = {
    "laliga": "soccer_spain_la_liga",
    "premier_league": "soccer_epl",
    # OJO: "soccer_germany_bundesliga1" (con el "1") es un sport_key
    # INVALIDO en The Odds API -- el key documentado para la 1. Bundesliga
    # es "soccer_germany_bundesliga" a secas ("soccer_germany_bundesliga2"
    # es la 2. Bundesliga). Con la key invalida, la API devuelve error en
    # cada peticion y Bundesliga se queda sin cuotas siempre, mientras el
    # resto de ligas funciona con normalidad -- exactamente el sintoma
    # reportado. No se ha podido verificar end-to-end contra la API real
    # desde este entorno (red restringida); si sigue sin funcionar, avisa.
    "bundesliga": "soccer_germany_bundesliga",
    "serie_a": "soccer_italy_serie_a",
    "ligue_1": "soccer_france_ligue_one",
}

# Lineas de goles que nos interesan (las que usan los mercados del MVP).
RELEVANT_TOTAL_LINES = {1.5, 2.5, 3.5}


def _merge_bookmaker_markets(base: list[dict], extra: list[dict]) -> list[dict]:
    """Combina la lista de bookmakers de la request masiva (h2h/totals) con
    la del endpoint por evento (alternate_totals/btts) para que
    `_parse_event` procese ambas fuentes como si fueran una sola
    respuesta. Se combina por `key` de bookmaker; si una casa solo aparece
    en una de las dos listas, se conserva tal cual."""
    merged: dict[str, dict] = {b["key"]: {**b, "markets": list(b.get("markets", []))} for b in base}
    for bookmaker in extra:
        key = bookmaker["key"]
        if key in merged:
            merged[key]["markets"].extend(bookmaker.get("markets", []))
        else:
            merged[key] = bookmaker
    return list(merged.values())


class OddsApiProvider(DataProvider):
    name = "the_odds_api"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=30.0)

    def is_available(self) -> bool:
        settings = get_settings()
        return settings.odds_api_enabled and bool(settings.odds_api_key)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def _download(self, sport_key: str) -> list[dict]:
        settings = get_settings()
        url = f"{BASE_URL}/{sport_key}/odds"
        params = {
            "apiKey": settings.odds_api_key,
            "regions": "eu",
            # SOLO mercados "featured": el endpoint masivo (todos los
            # partidos de la liga en 1 request) devuelve 422 para
            # cualquier mercado "additional" (alternate_totals, btts...),
            # ver docstring del modulo.
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        logger.info("ingestion.odds_api.download", extra={"sport_key": sport_key})
        response = self._client.get(url, params=params)
        if response.status_code >= 400:
            # El mensaje por defecto de raise_for_status() no incluye el
            # cuerpo de la respuesta, que es donde The Odds API explica el
            # motivo real (p.ej. "Unknown sport_key"). Sin esto, un
            # sport_key invalido para UNA sola liga (como paso con
            # Bundesliga) es dificil de diagnosticar: solo se ve un 4xx
            # generico en vez del motivo exacto.
            logger.warning(
                "ingestion.odds_api.error_response: sport_key=%s status=%d body=%s",
                sport_key,
                response.status_code,
                response.text[:500],
            )
        response.raise_for_status()
        return response.json()

    def _download_additional_markets(self, sport_key: str, event_id: str) -> dict | None:
        """Mercados "additional" (alternate_totals, btts): solo se pueden
        pedir por evento, 1 request por partido -- ver docstring del
        modulo. `None` si la request falla, para no tumbar el resto del
        refresco por un evento suelto (p.ej. sin cuotas todavia)."""
        settings = get_settings()
        url = f"{BASE_URL}/{sport_key}/events/{event_id}/odds"
        params = {
            "apiKey": settings.odds_api_key,
            "regions": "eu",
            "markets": "alternate_totals,btts",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        response = self._client.get(url, params=params)
        if response.status_code >= 400:
            logger.warning(
                "ingestion.odds_api.additional_markets_error: sport_key=%s event_id=%s status=%d body=%s",
                sport_key,
                event_id,
                response.status_code,
                response.text[:500],
            )
            return None
        return response.json()

    def fetch_odds(self, competition_code: str) -> list[FixtureOddsSnapshot]:
        sport_key = COMPETITION_TO_SPORT_KEY.get(competition_code)
        if sport_key is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")
        payload = self._download(sport_key)
        settings = get_settings()
        if settings.odds_api_fetch_additional_markets:
            for event in payload:
                additional = self._download_additional_markets(sport_key, event["id"])
                if additional:
                    event["bookmakers"] = _merge_bookmaker_markets(
                        event.get("bookmakers", []), additional.get("bookmakers", [])
                    )
        return [self._parse_event(event) for event in payload]

    @staticmethod
    def _parse_event(event: dict) -> FixtureOddsSnapshot:
        home_team = event["home_team"]
        away_team = event["away_team"]
        commence_time = dt.datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))

        odds: list[RawOddsRecord] = []
        for bookmaker in event.get("bookmakers", []):
            bookmaker_key = bookmaker.get("key", "unknown")
            for market in bookmaker.get("markets", []):
                if market["key"] == "h2h":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home_team:
                            selection = "home"
                        elif outcome["name"] == away_team:
                            selection = "away"
                        else:
                            selection = "draw"
                        odds.append(
                            RawOddsRecord(bookmaker_key, "match_result", None, selection, float(outcome["price"]))
                        )
                elif market["key"] in ("totals", "alternate_totals"):
                    for outcome in market["outcomes"]:
                        point = outcome.get("point")
                        if point not in RELEVANT_TOTAL_LINES:
                            continue
                        selection = "over" if outcome["name"].lower() == "over" else "under"
                        odds.append(
                            RawOddsRecord(
                                bookmaker_key, "over_under_goals", float(point), selection, float(outcome["price"])
                            )
                        )
                elif market["key"] == "btts":
                    for outcome in market["outcomes"]:
                        selection = "yes" if outcome["name"].lower() == "yes" else "no"
                        odds.append(
                            RawOddsRecord(bookmaker_key, "btts", None, selection, float(outcome["price"]))
                        )
        return FixtureOddsSnapshot(home_team, away_team, commence_time, odds)

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        raise NotImplementedError(
            "OddsApiProvider no ingiere partidos (usa fetch_odds + "
            "services/data_service.py::attach_odds_to_scheduled_matches)."
        )
