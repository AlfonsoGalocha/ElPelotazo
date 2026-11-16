"""Limpieza puntual: borra de `MatchOdds` las cuotas de una casa ya
EXCLUIDA (ver `normalization/bookmakers.py::EXCLUDED_BOOKMAKERS`) que
quedaron guardadas de ANTES de excluirla -- el filtro en
`data_service.py` evita que se guarden NUEVAS, pero no toca las que ya
estaban en la base de datos.

Pedido real del usuario: "quiero fuera" a Betano, la unica casa que
metia cuotas sin sentido en corners (y podria estar haciendolo en otros
mercados sin que se hubiera notado todavia).

Solo toca `MatchOdds` (el snapshot VIGENTE que usa el consenso de
mercado en vivo), nunca `MatchOddsHistory`: esa tabla es APPEND-ONLY por
diseño (closing_line_value se calcula sobre ella, ver market/clv.py) y
nunca se borra ni se reescribe, ni siquiera para una casa que se decide
excluir despues.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/purge_excluded_bookmaker_odds.py

Tras correrlo, hace falta `football-edge predict-upcoming` para que el
consenso de mercado (market_probability/edge) de los partidos programados
se recalcule sin esa casa.
"""

from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.matches import MatchOdds
from backend.app.normalization.bookmakers import is_excluded_bookmaker


def main() -> None:
    with session_scope() as db:
        rows = db.query(MatchOdds).all()
        to_delete_ids = [row.id for row in rows if is_excluded_bookmaker(row.bookmaker)]
        bookmakers_seen = sorted({row.bookmaker for row in rows if row.id in to_delete_ids})

        if not to_delete_ids:
            print("No hay ninguna fila de MatchOdds de una casa excluida -- nada que borrar.")
            return

        db.query(MatchOdds).filter(MatchOdds.id.in_(to_delete_ids)).delete(synchronize_session=False)
        print(
            f"Borradas {len(to_delete_ids)} filas de MatchOdds de las casas excluidas: "
            f"{bookmakers_seen} (nombres exactos vistos en la BD, antes de normalizar)."
        )


if __name__ == "__main__":
    main()
