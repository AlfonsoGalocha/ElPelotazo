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

import time

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.config.settings import get_settings
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

DIRECT_BASE_URL = "https://v3.football.api-sports.io"
RAPIDAPI_BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"


class ApiFootballRateLimitError(RuntimeError):
    """La API senhalo `errors.rateLimit` en el cuerpo de la respuesta (HTTP
    200 -- el error real viene DENTRO del JSON, ver docstring de `_get`).

    Bug real visto en vivo (seccion "Selecciones", 2026-11): el usuario
    corrio `debug_api_football_national_teams.py` (que encadena ~30
    llamadas a `/fixtures` sin pausa, una por competicion de interes) y
    varias fallaron con "Too many requests. You have exceeded the limit
    of requests per minute" -- ANTES de este cambio, `_get` solo lo
    logueaba con WARNING y devolvia `[]`, indistinguible de "de verdad no
    hay partidos programados" (el mismo problema que el comentario de
    `_get` ya describia para otros errores, pero sin cubrir este caso
    real). Transitorio por definicion (limite POR MINUTO): se reintenta
    con el mismo backoff exponencial que un error de red real, en vez de
    tratarse como "cero resultados" silenciosamente.
    """

# IDs de liga de API-Football (estables, documentados publicamente) para
# las 5 ligas del MVP.
LEAGUE_IDS: dict[str, int] = {
    "laliga": 140,
    "premier_league": 39,
    "bundesliga": 78,
    "serie_a": 135,
    "ligue_1": 61,
    "champions_league": 2,
    "liga_f": 142,
    "afc_champions_league": 17,
    # Seccion "Selecciones" -- confirmado contra la respuesta REAL de
    # /leagues?country=World (scripts/debug_api_football_national_teams.py,
    # ejecutado por el usuario, 2026-11-17), nunca un ID de memoria: id=5,
    # coverage.odds=True y partidos reales en curso en ese mismo momento
    # (Denmark vs Norway, Wales vs Portugal...). Es la UNICA competicion
    # de selecciones "top" con cuotas reales via este proveedor -- Mundial,
    # Eurocopa y sus clasificatorios salieron con coverage.odds=False en
    # esa misma respuesta real.
    "uefa_nations_league": 5,
    # Resto de "Selecciones" absoluta masculina (alcance decidido
    # explicitamente por el usuario), IDs confirmados contra la MISMA
    # respuesta real (seccion "ABSOLUTA MASCULINA" de
    # scripts/debug_api_football_national_teams.py, ejecutado por el
    # usuario, 2026-11-18) -- NINGUNO tiene coverage.odds=True salvo donde
    # se indica, asi que solo apareceran en el calendario de "Selecciones",
    # nunca en el ranking de senhales (sin cuota real no hay edge/EV que
    # calcular, igual que cualquier partido "solo modelo" en el resto de
    # la app).
    "world_cup": 1,
    "world_cup_qualification_europe": 32,
    "world_cup_qualification_south_america": 34,
    "world_cup_qualification_concacaf": 31,
    "world_cup_qualification_africa": 29,
    "world_cup_qualification_asia": 30,
    "world_cup_qualification_oceania": 33,
    "world_cup_qualification_intercontinental_playoffs": 37,
    "euro_championship": 4,
    "euro_championship_qualification": 960,
    "copa_america": 9,
    "africa_cup_of_nations": 6,
    # odds=True SOLO para su ultima edicion listada (2027) -- las
    # anteriores (2021/2023/2025) siguen sin cuota real.
    "africa_cup_of_nations_qualification": 36,
    # odds=True SOLO para su ultima edicion listada (2025) -- las
    # anteriores (2020/2022/2023/2024) siguen sin cuota real.
    "concacaf_nations_league": 536,
}

# API-Football etiqueta cada EDICION de una competicion de selecciones con
# un unico anho (nunca "YYYY/YY" como las ligas de club) -- aqui se
# convierte igualmente a traves de `season_label_from_date` (ver
# cli.py::update_international) para que comparta exactamente el mismo
# `Season.label` que `update-fixtures`/`update-odds` ya usan para la
# temporada EN CURSO, y asi nunca crear una fila de `Season` duplicada
# para la misma edicion real. NINGUNA de estas competiciones es anual:
# los anhos de cada lista son el HISTORIAL COMPLETO real devuelto por
# `/leagues?country=World` (nunca adivinados ni interpolados) -- ver la
# seccion "ABSOLUTA MASCULINA" de debug_api_football_national_teams.py.
INTERNATIONAL_COMPETITION_EDITIONS: dict[str, list[int]] = {
    "uefa_nations_league": [2018, 2020, 2022, 2024, 2026],
    "world_cup": [2010, 2014, 2018, 2022, 2026],
    "world_cup_qualification_europe": [2018, 2020, 2024],
    "world_cup_qualification_south_america": [2018, 2022, 2026],
    "world_cup_qualification_concacaf": [2018, 2022, 2026],
    "world_cup_qualification_africa": [2018, 2022, 2023],
    "world_cup_qualification_asia": [2018, 2022, 2026],
    "world_cup_qualification_oceania": [2018, 2022, 2026],
    "world_cup_qualification_intercontinental_playoffs": [2014, 2018, 2022, 2026],
    "euro_championship": [2008, 2012, 2016, 2020, 2024],
    # Solo UNA edicion real listada por la API bajo este ID especifico
    # (2023) -- nunca se inventan ciclos anteriores sin evidencia.
    "euro_championship_qualification": [2023],
    "copa_america": [2015, 2016, 2019, 2021, 2024],
    "africa_cup_of_nations": [2015, 2017, 2019, 2021, 2023, 2025],
    "africa_cup_of_nations_qualification": [2021, 2023, 2025, 2027],
    "concacaf_nations_league": [2020, 2022, 2023, 2024, 2025],
}


class ApiFootballClient:
    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=30.0)
        # Bloque 3.4/3.5 de la mision (provider_responses): respuesta
        # cruda de CADA pagina de CADA llamada real a la API durante la
        # vida de esta instancia -- una instancia nueva por comando CLI
        # (ver cli.py), asi que acumular sin resetear entre llamadas
        # (p.ej. `fetch_odds_for_window` hace un `_get("/odds", ...)` por
        # fixture) es correcto: el llamador archiva todo el lote junto al
        # terminar la operacion (ver
        # services/provider_response_service.py::archive_raw_responses).
        self.last_raw_responses: list[dict] = []

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
        retry=retry_if_exception_type((httpx.HTTPError, ApiFootballRateLimitError)),
    )
    def _get_page(self, path: str, params: dict) -> tuple[list[dict], dict | None]:
        """Una sola pagina. Devuelve `(response, paging)` -- `paging` es el
        objeto `{"current": N, "total": M}` que API-Football devuelve en
        TODOS sus endpoints (nunca `None` salvo error real/respuesta
        vacia), para que `_get` decida si hace falta pedir mas paginas."""
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
            # Unico tipo de error confirmado contra una respuesta REAL
            # (ver `ApiFootballRateLimitError`) que se trata como fallo de
            # verdad en vez de "cero resultados" -- cualquier otro error
            # sigue solo logueado (conservador: nunca se ha visto que
            # otros tipos de error devuelvan datos parciales validos a la
            # vez, pero tampoco se asume sin evidencia).
            if isinstance(errors, dict) and "rateLimit" in errors:
                raise ApiFootballRateLimitError(
                    f"API-Football rate limit: path={path} params={params} errors={errors}"
                )
        # Solo se archiva una respuesta que de verdad llego hasta aqui
        # (nunca un intento que reventó antes, ver `ApiFootballRateLimitError`
        # arriba) -- `payload` completo, no solo `response`, para poder
        # auditar tambien `errors`/`paging` tal cual los devolvio la API.
        self.last_raw_responses.append({"path": path, "params": dict(params), "response": payload})
        return payload.get("response", []), payload.get("paging")

    def _get(self, path: str, params: dict) -> list[dict]:
        """Pide TODAS las paginas, nunca solo la primera. Bug real
        investigado por el usuario (cuotas/fixtures incompletos): todos
        los endpoints de API-Football devuelven un sobre `paging`
        (`{"current": N, "total": M}`, p.ej. `/odds` solo trae 10
        resultados por pagina segun su documentacion oficial) -- antes de
        este cambio, `_get` solo leia `response` de la PRIMERA pagina y
        descartaba `paging` sin comprobarlo, perdiendo en silencio
        cualquier resultado a partir del 11. Una pausa de 1s entre
        paginas (solo cuando hace falta pedir una segunda, nunca en el
        caso normal de una sola pagina) evita agotar el limite de
        peticiones por minuto, mismo principio que ya se aplica entre
        partidos en `fetch_odds_for_window`/`update_international`.

        REGRESION REAL vista en produccion (el usuario corrio
        `update-odds` de verdad, 2026-11-18): la primera version de este
        metodo anhadia `page=1` explicitamente incluso en la PRIMERA
        peticion -- `/fixtures` (que nunca pagina, siempre devuelve
        `paging: {current: 1, total: 1}`) rechazo ese parametro con
        `errors={'page': 'The Page field do not exist.'}` en TODAS las
        ligas, dejando `fixtures_fetched=0` en todas partes (el mensaje
        de la CLI lo atribuia erroneamente a "el plan gratuito"). La
        `page` query param solo se envia a partir de la SEGUNDA peticion
        -- la primera pagina se pide sin ella (equivalente documentado a
        `page=1` en la API, y el unico que `/fixtures` acepta)."""
        combined: list[dict] = []
        page = 1
        while True:
            page_params = params if page == 1 else {**params, "page": page}
            page_response, paging = self._get_page(path, page_params)
            combined.extend(page_response)
            total_pages = (paging or {}).get("total") or 1
            current_page = (paging or {}).get("current") or page
            if current_page >= total_pages:
                break
            if page == 1:
                logger.info(
                    "ingestion.api_football.paginated_response: path=%s params=%s total_pages=%d",
                    path,
                    params,
                    total_pages,
                )
            page += 1
            time.sleep(1)
        return combined
