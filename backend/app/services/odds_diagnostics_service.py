"""Panel tecnico de diagnostico de cuotas (seccion "Configuracion",
pedido real del usuario tras investigar por que la app mostraba cuotas
desactualizadas/distintas de las reales): registra cada intento real de
`update-odds`/`update-secondary-odds` (`OddsIngestionRun`, APPEND-ONLY) y
responde "cuando fue la ultima vez que de verdad se intento, que paso, y
que tan fresca es la cuota que hay guardada ahora" sin tener que
reconstruirlo leyendo logs sueltos del CLI.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.db.models.core import OddsIngestionRun
from backend.app.db.models.matches import Match, MatchOdds


def record_ingestion_run(
    db: Session,
    provider: str,
    operation: str,
    competition_code: str,
    started_at: dt.datetime,
    finished_at: dt.datetime,
    fixtures_fetched: int | None = None,
    matched: int | None = None,
    error: str | None = None,
) -> None:
    """`db.flush()` solo (convencion del proyecto: el servicio nunca
    decide cuando confirmar la transaccion, ver promotion_service.py) --
    el llamador (`cli.py`, que ya usa `session_scope()`) es quien hace
    commit real al terminar."""
    db.add(
        OddsIngestionRun(
            provider=provider,
            operation=operation,
            competition_code=competition_code,
            started_at=started_at,
            finished_at=finished_at,
            fixtures_fetched=fixtures_fetched,
            matched=matched,
            error=error if error is None or len(error) <= 2000 else error[:1999] + "…",
        )
    )
    db.flush()


def latest_runs_by_competition(db: Session, limit_per_key: int = 1) -> list[OddsIngestionRun]:
    """La(s) fila(s) MAS RECIENTE(S) por (provider, operation,
    competition_code) -- nunca todo el historico completo (crece sin
    limite), pensado para el panel de Configuracion que solo necesita
    "el ultimo intento real de cada cosa"."""
    all_runs = (
        db.query(OddsIngestionRun)
        .order_by(OddsIngestionRun.competition_code, OddsIngestionRun.finished_at.desc())
        .all()
    )
    grouped: dict[tuple[str, str, str], list[OddsIngestionRun]] = {}
    for run in all_runs:
        key = (run.provider, run.operation, run.competition_code)
        grouped.setdefault(key, []).append(run)
    latest: list[OddsIngestionRun] = []
    for runs in grouped.values():
        latest.extend(runs[:limit_per_key])
    return sorted(latest, key=lambda r: r.finished_at, reverse=True)


def market_odds_freshness(db: Session) -> dict:
    """Evidencia REAL ya guardada en `MatchOdds` (nunca necesita una
    tabla nueva): cuando se registro la cuota mas reciente de verdad, y
    que casas de apuestas tenemos cubiertas actualmente -- complementa
    `latest_runs_by_competition` (que solo sabe "se intento" o "fallo",
    no "que precio quedo guardado")."""
    most_recent = db.query(func.max(MatchOdds.recorded_at)).scalar()
    bookmakers = sorted(
        {row[0] for row in db.query(MatchOdds.bookmaker).distinct().all() if row[0] is not None}
    )
    scheduled_with_odds = (
        db.query(func.count(func.distinct(MatchOdds.match_id)))
        .join(Match, Match.id == MatchOdds.match_id)
        .filter(Match.status == "scheduled")
        .scalar()
    )
    return {
        "most_recent_odds_recorded_at": most_recent,
        "bookmakers_seen": bookmakers,
        "scheduled_matches_with_odds": scheduled_with_odds or 0,
    }
