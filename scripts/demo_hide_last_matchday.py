#!/usr/bin/env python3
"""Utilidad de DEMO (no para produccion): permite ver el dashboard "en vivo"
usando datos 100% reales cuando la unica fuente disponible es un dataset
historico sin fixtures futuros (ver docs/data_sources.md).

Que hace, para cada una de las 5 competiciones:
1. Busca la fecha del ULTIMO partido real ya jugado.
2. Marca esos partidos como "scheduled" (oculta su resultado real).
3. Re-entrena el modelo usando SOLO datos estrictamente anteriores a esa
   fecha (sin leakage: el modelo nunca ve el resultado que va a "predecir").
4. Genera predicciones reales para esos partidos ocultos.

Despues de ejecutar esto, /predictions/today NO los mostrara (su fecha ya
paso), pero SI apareceran en /predictions/top-signals y en
/matches/{id}/predictions -> visibles en el dashboard (paginas "Top Signals"
y detalle de partido).

IMPORTANTE: esto modifica tu base de datos local (oculta resultados reales
para la demo). Si quieres tu BD intacta con todos los resultados reales,
haz una copia antes: cp data/processed/football_edge.db data/processed/football_edge_backup.db
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db.database import init_db, session_scope  # noqa: E402
from backend.app.db.models.core import Competition  # noqa: E402
from backend.app.db.models.matches import Match  # noqa: E402
from backend.app.ingestion.football_data.provider import COMPETITION_DIV_CODES  # noqa: E402
from backend.app.services.model_service import train_competition_models  # noqa: E402
from backend.app.services.prediction_service import (
    generate_predictions_for_competition,  # noqa: E402
)
from backend.app.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)


def hide_last_matchday(db, competition_id: int) -> dt.date | None:
    last = (
        db.query(Match.kickoff_utc)
        .filter(Match.competition_id == competition_id, Match.status == "finished")
        .order_by(Match.kickoff_utc.desc())
        .first()
    )
    if last is None:
        return None
    last_date = last[0].date()

    matches = (
        db.query(Match)
        .filter(
            Match.competition_id == competition_id,
            Match.kickoff_utc >= dt.datetime.combine(last_date, dt.time.min),
            Match.kickoff_utc <= dt.datetime.combine(last_date, dt.time.max),
        )
        .all()
    )
    for match in matches:
        match.status = "scheduled"
        match.home_goals = None
        match.away_goals = None
    return last_date


def main() -> None:
    init_db()
    codes = list(COMPETITION_DIV_CODES.keys())
    hidden_dates: dict[str, dt.date] = {}

    with session_scope() as db:
        for code in codes:
            competition = db.query(Competition).filter_by(code=code).one_or_none()
            if competition is None:
                print(f"[demo] {code}: sin datos, ejecuta antes scripts/update_data.py")
                continue
            hidden_date = hide_last_matchday(db, competition.id)
            if hidden_date:
                hidden_dates[code] = hidden_date
                print(f"[demo] {code}: oculto el partido/jornada del {hidden_date}")

    with session_scope() as db:
        for code in codes:
            if code not in hidden_dates:
                continue
            result = train_competition_models(db, code)
            print(f"[demo] {code}: reentrenado ({result['status']})")

    with session_scope() as db:
        for code, hidden_date in hidden_dates.items():
            predictions = generate_predictions_for_competition(db, code, hidden_date)
            print(f"[demo] {code} {hidden_date}: {len(predictions)} predicciones generadas")

    print("\nListo. Abre el dashboard y mira la pagina 'Top Signals'")
    print("(o /predictions/top-signals en la API).")


if __name__ == "__main__":
    main()
