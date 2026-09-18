"""Configuracion centralizada via variables de entorno.

Toda configuracion sensible o dependiente de entorno vive aqui. Nunca
hardcodear API keys ni URLs de base de datos en el resto del codigo.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from numpy import rint
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
    odds_api_enabled: bool = False
    odds_api_key: str | None = None

    model_artifacts_dir: str = "models/artifacts"
    random_seed: int = 42

    print("REPO_ROOT:", REPO_ROOT)
    print("ENV FILE:", REPO_ROOT / ".env")
    print("ENV EXISTS:", (REPO_ROOT / ".env").exists())

    @property
    def model_artifacts_path(self) -> Path:
        path = REPO_ROOT / self.model_artifacts_dir
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
