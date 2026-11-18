"""Limpieza puntual: borra de `Match` (y sus `Prediction`/`PredictionResult`/
`MatchOdds`/`MatchStatistics`/`MatchOddsHistory` asociados) cualquier
partido donde aparezca un equipo de `normalization/teams.py::EXCLUDED_TEAMS`
-- ya sea de local o de visitante -- que quedara guardado de ANTES de
excluirlo. El filtro en `services/data_service.py::ingest_matches` evita
que se guarden partidos NUEVOS de un equipo excluido, pero no toca los
que ya estaban en la base de datos.

Pedido real del usuario (seccion "Selecciones", 2026-11): "Selecciones"
mostraba partidos de Russia con fecha de kickoff de 2022 como "proximo
partido" -- Russia esta suspendida de UEFA/FIFA desde entonces, asi que
esos partidos nunca se van a jugar, pero API-Football los sigue
devolviendo con estado "scheduled" indefinidamente. "supongo que es
porque es Rusia y la quitaron de todas las competiciones, quitemosla
nosotros tambien" -- se borran TODOS sus partidos (programados Y ya
jugados, no solo los que causaron el sintoma visible), igual que las
competiciones reales la excluyeron por completo, no solo sus partidos
futuros.

Borra Prediction/PredictionResult ANTES del Match (igual que
`cli.py::reset_scheduled`, pero cubriendo TAMBIEN partidos finalizados
con PredictionResult real, que `reset_scheduled` nunca toca): `Match` no
tiene cascade ORM hacia `Prediction` (ver comentario en
`db/models/matches.py`), y `PredictionResult.prediction_id` no tiene
`ON DELETE CASCADE` a nivel de BD, asi que hay que borrarlo explicitamente
en ese orden o Postgres rechaza el borrado por la FK.

Uso (dentro del contenedor):
    docker compose exec backend python scripts/purge_excluded_team_matches.py
"""

from __future__ import annotations

from backend.app.db.database import session_scope
from backend.app.db.models.core import Team
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.normalization.teams import is_excluded_team


def main() -> None:
    with session_scope() as db:
        excluded_team_ids = [
            team.id for team in db.query(Team).all() if is_excluded_team(team.canonical_name)
        ]
        if not excluded_team_ids:
            print("Ningun equipo excluido tiene filas en Team todavia -- nada que borrar.")
            return

        matches = (
            db.query(Match)
            .filter(
                (Match.home_team_id.in_(excluded_team_ids))
                | (Match.away_team_id.in_(excluded_team_ids))
            )
            .all()
        )
        if not matches:
            print(
                f"Equipos excluidos encontrados ({excluded_team_ids}) pero sin ningun "
                "partido asociado -- nada que borrar."
            )
            return

        match_ids = [m.id for m in matches]
        prediction_ids_for_matches = (
            db.query(Prediction.id).filter(Prediction.match_id.in_(match_ids)).scalar_subquery()
        )
        predictions_deleted = (
            db.query(PredictionResult)
            .filter(PredictionResult.prediction_id.in_(prediction_ids_for_matches))
            .delete(synchronize_session=False)
        )
        db.query(Prediction).filter(Prediction.match_id.in_(match_ids)).delete(
            synchronize_session=False
        )
        for match in matches:
            db.delete(match)

        print(
            f"Borrados {len(matches)} partidos de equipos excluidos, "
            f"{predictions_deleted} PredictionResult y sus Prediction asociadas "
            "(MatchOdds/MatchStatistics/MatchOddsHistory via cascade)."
        )


if __name__ == "__main__":
    main()
