"""Adapter para The Odds API (cuotas REALES de partidos futuros).

Sin esto, el sistema no tiene con que comparar la probabilidad del modelo:
la gracia del proyecto es precisamente detectar diferencias entre lo que
dice el modelo y lo que dice el mercado (edge estadistico). Los datasets
historicos usados en el resto del proyecto (ver ingestion/football_data/)
solo tienen cuotas de partidos YA JUGADOS; para partidos FUTUROS hace falta
una fuente de cuotas en vivo, y esa fuente necesita casi siempre una API
real (no hay ningun mirror de GitHub con cuotas actualizadas a diario).

Fuente: https://the-odds-api.com — plan gratuito: 500 requests/mes, cubre
1X2 (`h2h`), Over/Under de goles (`totals` + `alternate_totals` para lineas
extra como 1.5) y Ambos Marcan (`btts`) para las 5 ligas del MVP.

Requiere registrarse (gratis) y configurar ODDS_API_KEY en `.env`. Sin key,
`is_available()` devuelve False y el resto del sistema sigue funcionando
(simplemente sin `market_probability`/`edge` para partidos futuros, exactamente
igual que hasta ahora).

IMPORTANTE — verificado end-to-end en produccion (por el usuario, no desde
este entorno de desarrollo con red restringida) para `h2h` y `totals` en
las 5 ligas. `alternate_totals` y `btts` son mercados anadidos despues y
AUN NO verificados end-to-end: The Odds API cobra el consumo de cuota por
cada "grupo de mercados" pedido (h2h/totals cuenta como 1, additional
markets como `alternate_totals`/`btts` puede contar como consumo extra
segun su tabla de precios), asi que anadirlos puede agotar el plan
gratuito de 500 req/mes mas rapido de lo esperado -- si eso pasa, se
puede pedir `alternate_totals`/`btts` en una llamada aparte y con menos
frecuencia que `h2h,totals`, en vez de en todas las peticiones.
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
            # "totals" es la linea PRINCIPAL de cada bookmaker (normalmente
            # 2.5, a veces 3.5 segun la casa) -- por eso antes solo
            # aparecian esas dos lineas y nunca 1.5: no es un filtro
            # nuestro, es que "totals" a secas no incluye lineas
            # alternativas. "alternate_totals" es el mercado que expone
            # TODAS las lineas extra (incluida 1.5) que cada bookmaker
            # ofrezca. "btts" (Both Teams To Score) es un mercado aparte
            # que antes no se pedia en absoluto, así que nunca podía llegar
            # aunque el modelo ya lo soporta (ver prediction/market_labels.py).
            # Sin verificar end-to-end (ver docstring del modulo): "alternate_totals"
            # es un mercado "additional" en The Odds API que puede no estar
            # cubierto por todos los bookmakers de la region "eu" ni contar
            # igual contra la cuota del plan gratuito -- si tras esto sigue
            # sin verse la linea 1.5 para una liga concreta, puede ser que
            # ningun bookmaker cubierto la ofrezca ese dia, no un fallo.
            "markets": "h2h,totals,alternate_totals,btts",
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

    def fetch_odds(self, competition_code: str) -> list[FixtureOddsSnapshot]:
        sport_key = COMPETITION_TO_SPORT_KEY.get(competition_code)
        if sport_key is None:
            raise ValueError(f"Competicion no soportada por este adapter: {competition_code}")
        payload = self._download(sport_key)
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
