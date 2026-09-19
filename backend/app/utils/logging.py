"""Logging estructurado (JSON en produccion, legible en desarrollo)."""

from __future__ import annotations

import logging
import sys

from backend.app.config.settings import get_settings

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    settings = get_settings()
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # CRITICO (seguridad real, evidenciada en produccion): httpx emite su
    # propio log a nivel INFO por cada request ("HTTP Request: GET <url>
    # ...") que incluye la URL COMPLETA con query params -- The Odds API
    # pasa la key como `?apiKey=...` en la URL, asi que con el nivel INFO
    # global (por defecto de este proyecto) esa key aparecia en texto
    # plano en la consola en cada llamada, aunque nuestro propio codigo
    # nunca la loguea (ver ingestion/odds/provider.py: nuestros logs solo
    # incluyen sport_key/status/body, nunca la URL con la key). Se silencia
    # el logger de httpx a WARNING para cortar esa fuga sin perder nuestros
    # propios logs estructurados.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
