"""Configuracion centralizada via variables de entorno.

Toda configuracion sensible o dependiente de entorno vive aqui. Nunca
hardcodear API keys ni URLs de base de datos en el resto del codigo.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # Ruta ABSOLUTA a proposito (no ".env" relativo): pydantic-settings
    # resuelve una ruta relativa contra el cwd del proceso que arranca la
    # app, no contra la raiz del repo. Si uvicorn/la CLI se lanzan desde
    # otro directorio, un ".env" relativo se "encuentra" (o no) de forma
    # silenciosa y sin ningun error -> variables como ODDS_API_KEY parecen
    # configuradas pero nunca se cargan. Con ruta absoluta, siempre es el
    # `.env` de la raiz del proyecto, se lance como se lance.
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = f"sqlite:///{REPO_ROOT}/data/processed/football_edge.db"
    redis_url: str | None = None

    football_data_co_uk_enabled: bool = True
    understat_enabled: bool = False
    fbref_enabled: bool = False
    api_football_enabled: bool = False
    api_football_key: str | None = None
    # api-football.com emite keys para su propio host directamente
    # ("v3.football.api-sports.io", header x-apisports-key) o, si te
    # registras via RapidAPI, para un host distinto con otras cabeceras
    # ("api-football-v1.p.rapidapi.com", headers X-RapidAPI-Key/-Host).
    # Ambas son la MISMA API/datos, solo cambia como se autentica: por eso
    # este flag en vez de asumir uno de los dos.
    api_football_use_rapidapi: bool = False
    odds_api_enabled: bool = False
    odds_api_key: str | None = None
    # "alternate_totals" (linea 1.5 de goles) y "btts" son mercados
    # "additional" de The Odds API: a diferencia de h2h/totals, la API los
    # factura aparte y solo se pueden pedir por evento individual (ver
    # ingestion/odds/provider.py), lo que multiplica las requests por
    # partido en vez de 1 por liga. Desactivado por defecto para no agotar
    # el plan gratuito (500 req/mes) sin que el usuario lo decida.
    odds_api_fetch_additional_markets: bool = False

    model_artifacts_dir: str = "models/artifacts"
    random_seed: int = 42

    # --- Filtro de calidad para ranking de senhales (prediction/ranking.py) ---
    # Deliberadamente permisivos por defecto (seccion 7 de la revision de
    # arquitectura: "no hardcodear un umbral agresivo sin estudiar antes su
    # efecto"). El filtro DURO solo descarta datos invalidos o sin
    # evidencia real; degradar una senhal de baja calidad (cuota 1.02, poco
    # edge, pocas casas) es trabajo del SCORING de ranking, no de este
    # filtro. Ajustables sin tocar codigo.
    # 1.45 (no 1.01): BUG REAL corregido (feedback de usuario, 2026-09-19).
    # Con 1.01, una cuota 1.02 (modelo 98%, prediccion casi segura, edge
    # practicamente nulo) PASABA el filtro duro -- solo la quedaba abajo
    # el SCORING (probabilidad^2 x edge), pero si un dia habia pocas
    # senhales candidatas, esa cuota irrisoria podia colarse igual en el
    # top-N por falta de competencia, exactamente el mismo problema que
    # `max_signal_odds` resuelve en el extremo alto. 1.45 es simetrico a
    # esa logica: por debajo, el margen para que exista edge real es tan
    # estrecho que casi nunca compensa el riesgo de un modelo mal
    # calibrado. Ajustable; nunca hardcodeado en el frontend.
    min_signal_odds: float = 1.45
    min_edge_pp: float = 0.0  # exige edge NO negativo (el modelo no puede ir peor que el mercado)
    min_bookmakers: int = 1  # al menos una casa real respaldando la cuota (nunca 0 = "sin mercado")
    min_data_quality: float = 0.0  # sin filtro adicional por defecto; confidence ya lo pondera
    # UNICA excepcion deliberada a "el filtro duro no juzga calidad": un
    # "edge" grande en un resultado muy improbable (p.ej. modelo ~12% /
    # cuota justa 8, mercado ofrece 15) es matematicamente un edge real,
    # pero no es una prediccion util para destacar en "Mejores señales" --
    # sigue siendo mas probable que falle que que acierte, y el cuadrado
    # de la probabilidad en el scoring no basta para que quede fuera del
    # top-N si un dia hay pocas senhales candidatas (feedback real de
    # usuario, 2026-09-18). 6.0 es un valor de partida razonable (cuotas
    # por encima implican probabilidad implicita < ~17%), no un limite
    # universal -- ajustalo segun tu propio criterio de riesgo. `None`
    # desactiva el filtro por completo.
    max_signal_odds: float | None = 6.0

    # --- Calidad de mercado (prediction/market_quality.py) ---
    # Clasificacion HIGH/MEDIUM/LOW basada en cuantas casas respaldan el
    # consenso y cuanto se dispersan sus cuotas -- NUNCA se mezcla con la
    # calibracion del modelo (eso es `confidence`, prediction/confidence.py).
    # Umbrales de partida, no un estandar universal: ajustar segun cuantas
    # casas cubre realmente tu fuente de cuotas.
    market_quality_high_min_bookmakers: int = 10
    market_quality_medium_min_bookmakers: int = 4
    # Dispersion relativa = (cuota_max - cuota_min) / cuota_mediana. Por
    # encima de este umbral, muchas casas respaldando el mercado ya NO
    # basta para "HIGH": una dispersion grande sugiere que el "consenso"
    # es menos fiable de lo que el numero de casas por si solo sugiere.
    market_quality_max_dispersion_ratio: float = 0.15

    # --- Frescura de cuotas ---
    # `None` (por defecto): no se excluye nada por antigueedad, solo se
    # muestra la edad de la cuota (seccion 12: "no inventar frescura", pero
    # tampoco descartar senhales sin evidencia de que haga falta). Fija un
    # entero para activar la exclusion dura (`ExclusionReason.STALE_ODDS`).
    max_odds_age_minutes: int | None = None

    @property
    def model_artifacts_path(self) -> Path:
        path = REPO_ROOT / self.model_artifacts_dir
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
