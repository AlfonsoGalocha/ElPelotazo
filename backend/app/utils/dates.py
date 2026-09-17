"""Utilidades de fechas/temporadas usadas para evitar leakage temporal."""

from __future__ import annotations

import datetime as dt


def season_label(match_date: dt.date) -> str:
    """Devuelve la temporada europea estandar, p.ej. 2024-08-15 -> "2024/25"."""
    if match_date.month >= 7:
        start_year = match_date.year
    else:
        start_year = match_date.year - 1
    return f"{start_year}/{str(start_year + 1)[-2:]}"


def is_strictly_before(reference: dt.datetime, cutoff: dt.datetime) -> bool:
    """True si `reference` es estrictamente anterior a `cutoff`.

    Usado para filtrar cualquier dato/feature que deba excluirse de una
    prediccion por haber ocurrido en o despues del momento de prediccion.
    """
    return reference < cutoff
