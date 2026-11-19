"""Archiva la respuesta cruda de cada llamada real a un proveedor externo
(Bloque 3.4/3.5 de la mision: `ProviderResponse`, APPEND-ONLY, mismo
patron que `odds_diagnostics_service.py::record_ingestion_run`) -- sirve
de evidencia retroactiva de que devolvio exactamente la API en cada
momento, nunca solo lo que el pipeline de normalizacion decidio conservar.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from backend.app.db.models.core import ProviderResponse


def archive_raw_responses(
    db: Session,
    provider_name: str,
    operation: str,
    competition_code: str | None,
    raw_responses: list[dict],
) -> int:
    """Inserta una fila por cada pagina real capturada en
    `ApiFootballClient.last_raw_responses` (`{"path", "params", "response"}`).

    `db.flush()` solo, misma convencion que `record_ingestion_run`: el
    llamador (`cli.py`, con `session_scope()`) decide cuando hacer commit.
    No falla nunca por un `raw_responses` vacio (proveedores sin HTTP real,
    o un proveedor que todavia no implementa esta captura) -- simplemente
    no archiva nada."""
    now = dt.datetime.utcnow()
    for raw in raw_responses:
        db.add(
            ProviderResponse(
                provider=provider_name,
                operation=operation,
                competition_code=competition_code,
                path=raw["path"],
                params=raw["params"],
                response=raw["response"],
                fetched_at=now,
            )
        )
    if raw_responses:
        db.flush()
    return len(raw_responses)
