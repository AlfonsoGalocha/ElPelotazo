"""Cliente HTTP compartido para api-sports.io/api-football.com, usado por
`fixtures_provider.py` (calendario + resultados reales) y `odds_provider.py`
(cuotas de goles/1X2/BTTS/tarjetas/corners) -- misma autenticacion, mismo
manejo de errores, una sola vez.

IMPORTANTE -- sin verificar end-to-end: el entorno donde se escribio este
cliente (y los dos adapters que lo usan) tiene el egress de red
restringido a un allowlist que NO incluye api-football.com/api-sports.io,
asi que este codigo se escribio siguiendo la documentacion publica de la
API pero no pudo probarse contra la API real. El manejo de errores (ver
`_get`) y el matching de mercados por nombre (ver odds_provider.py) estan
escritos deliberadamente a la defensiva por esto mismo: si algo no
responde exactamente como se espera, el objetivo es que se vea claro en
los logs (WARNING con los nombres/campos reales vistos), nunca que falle
en silencio o reviente el pipeline entero.
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.config.settings import get_settings
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

DIRECT_BASE_URL = "https://v3.football.api-sports.io"
RAPIDAPI_BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

# IDs de liga de API-Football (estables, documentados publicamente) para
# las 5 ligas del MVP.
LEAGUE_IDS: dict[str, int] = {
    "laliga": 140,
    "premier_league": 39,
    "bundesliga": 78,
    "serie_a": 135,
    "ligue_1": 61,
}


class ApiFootballClient:
    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=30.0)

    def is_available(self) -> bool:
        settings = get_settings()
        return settings.api_football_enabled and bool(settings.api_football_key)

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        if settings.api_football_use_rapidapi:
            return {
                "X-RapidAPI-Key": settings.api_football_key or "",
                "X-RapidAPI-Host": RAPIDAPI_HOST,
            }
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
        payload = response.json()

        # CRITICO: API-Football (api-sports.io) devuelve HTTP 200 incluso
        # cuando hay un error de parametros/plan -- el error real viene
        # DENTRO del JSON (clave "errors": {...} o [...]), no en el codigo
        # HTTP. Sin esto, un error real ("esta temporada requiere un plan
        # de pago", "parametro invalido"...) se traga en silencio y solo se
        # ve "0 partidos consultados", indistinguible de "no hay partidos
        # en esas fechas".
        errors = payload.get("errors")
        if errors:
            logger.warning(
                "ingestion.api_football.api_errors: path=%s params=%s errors=%s",
                path,
                params,
                errors,
            )
        return payload.get("response", [])
