"""`scripts/purge_excluded_team_matches.py` -- verificado contra la BD de
test real (mismo motivo que los demas scripts de mutacion puntual:
`purge_excluded_bookmaker_odds.py` ya establecio este patron). Cubre el
caso real que lo motivo: partidos de Russia (equipo excluido,
`normalization/teams.py::EXCLUDED_TEAMS`) con Prediction/PredictionResult
reales asociados (no solo partidos sin liquidar, a diferencia de
`cli.py::reset_scheduled`), y que un partido SIN ningun equipo excluido
nunca se toca."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.normalization.competitions import COMPETITIONS
from scripts.purge_excluded_team_matches import main


def test_main_purges_finished_and_scheduled_matches_of_an_excluded_team(capsys):
    competition_code = "purge-excluded-team-test-league"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    with session_scope() as db:
        comp = Competition(code=competition_code, name=competition_code, country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2021/22")
        db.add(season)
        model_version = ModelVersion(
            name="purge-excluded-team-test",
            version="v1",
            market_family="goals",
            features=[],
            hyperparameters={},
            metrics={},
            dataset_version="test",
        )
        db.add(model_version)
        # Nombres de prueba EXCLUSIVOS de este test, nunca un pais real
        # ("Spain"/"Portugal") -- `Team.canonical_name` es UNIQUE y el
        # fichero SQLite de test se comparte entre toda la sesion de
        # pytest (ver conftest.py); otro test real ya crea un Team real
        # con ese nombre. "Russia" si debe ser el nombre literal: es lo
        # unico que `is_excluded_team` compara.
        russia = Team(canonical_name="Russia")
        spain = Team(canonical_name="Purge Test Opponent A")
        portugal = Team(canonical_name="Purge Test Opponent B")
        db.add_all([russia, spain, portugal])
        db.flush()

        # Partido YA LIQUIDADO de Russia (jugado antes de su suspension
        # real) -- con Prediction Y PredictionResult reales, el caso que
        # `cli.py::reset_scheduled` nunca cubre (solo mira 'scheduled').
        finished_match = Match(
            provider="test",
            provider_id="purge-excluded-team-finished",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime(2021, 11, 1),
            home_team_id=russia.id,
            away_team_id=spain.id,
            status="finished",
            home_goals=1,
            away_goals=1,
        )
        # Partido "programado" de Russia atascado desde 2022 (el sintoma
        # real reportado por el usuario en "Selecciones").
        scheduled_match = Match(
            provider="test",
            provider_id="purge-excluded-team-scheduled",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime(2022, 6, 2),
            home_team_id=spain.id,
            away_team_id=russia.id,
            status="scheduled",
        )
        # Control: partido SIN ningun equipo excluido -- nunca debe tocarse.
        untouched_match = Match(
            provider="test",
            provider_id="purge-excluded-team-untouched",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime(2026, 1, 1),
            home_team_id=spain.id,
            away_team_id=portugal.id,
            status="scheduled",
        )
        db.add_all([finished_match, scheduled_match, untouched_match])
        db.flush()

        finished_prediction = Prediction(
            match_id=finished_match.id,
            market="home_win",
            selection="home",
            model_version_id=model_version.id,
            model_probability=0.4,
            fair_odds=2.5,
            confidence=0.7,
            data_quality=0.7,
            explanation={"factors": []},
            features_used={"feature_names": []},
            created_at=dt.datetime.utcnow(),
            signal_score=0.1,
        )
        scheduled_prediction = Prediction(
            match_id=scheduled_match.id,
            market="home_win",
            selection="home",
            model_version_id=model_version.id,
            model_probability=0.4,
            fair_odds=2.5,
            confidence=0.7,
            data_quality=0.7,
            explanation={"factors": []},
            features_used={"feature_names": []},
            created_at=dt.datetime.utcnow(),
            signal_score=0.1,
        )
        untouched_prediction = Prediction(
            match_id=untouched_match.id,
            market="home_win",
            selection="home",
            model_version_id=model_version.id,
            model_probability=0.4,
            fair_odds=2.5,
            confidence=0.7,
            data_quality=0.7,
            explanation={"factors": []},
            features_used={"feature_names": []},
            created_at=dt.datetime.utcnow(),
            signal_score=0.1,
        )
        db.add_all([finished_prediction, scheduled_prediction, untouched_prediction])
        db.flush()

        db.add(PredictionResult(prediction_id=finished_prediction.id, outcome=False))
        db.add(
            MatchOdds(
                match_id=scheduled_match.id,
                bookmaker="test-bookmaker",
                market="home_win",
                selection="home",
                price=2.4,
            )
        )
        db.flush()

        finished_match_id = finished_match.id
        scheduled_match_id = scheduled_match.id
        untouched_match_id = untouched_match.id
        finished_prediction_id = finished_prediction.id
        scheduled_prediction_id = scheduled_prediction.id

    main()

    with session_scope() as db:
        assert db.get(Match, finished_match_id) is None
        assert db.get(Match, scheduled_match_id) is None
        assert db.get(Match, untouched_match_id) is not None

        assert db.get(Prediction, finished_prediction_id) is None
        assert db.get(Prediction, scheduled_prediction_id) is None
        assert (
            db.query(PredictionResult).filter_by(prediction_id=finished_prediction_id).first()
            is None
        )
        assert db.query(MatchOdds).filter_by(match_id=scheduled_match_id).first() is None

    output = capsys.readouterr().out
    assert "Borrados 2 partidos de equipos excluidos" in output


def test_main_is_idempotent_when_nothing_to_purge(capsys):
    main()  # ya sin filas de un equipo excluido con partidos reales (o la primera vez)
    output = capsys.readouterr().out
    assert "nada que borrar" in output
