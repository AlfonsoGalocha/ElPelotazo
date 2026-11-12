"""Bug real reportado por el usuario: antes de anhadir el alias que faltaba
en normalization/teams.py, una fuente nueva (`api_football`) que mandara un
nombre de equipo con un prefijo distinto al ya conocido ("SV Elversberg" en
vez de "SV 07 Elversberg") creaba un Team FANTASMA con su propio id, en vez
de resolver al Team ya existente. El resultado real de esos partidos se
escribia contra el fantasma -- el partido original se quedaba 'scheduled'
para siempre, duplicado con una fila 'finished' bajo el equipo fantasma.
Rompia ademas la deteccion de "jornada actual" (round_service.py) para toda
la liga, porque esa logica solo mira `status`.

`merge_duplicate_teams` (cli.py) repara datos YA DANHADOS por ese bug:
funde el Team fantasma en el original y resuelve el partido duplicado que
resulta, quedandose con la fila 'finished' (dato real, autoritativo)."""

from __future__ import annotations

import datetime as dt

from backend.app.cli import _merge_team
from backend.app.db.models.core import Competition, Season, Team, TeamNameMapping
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction


def _make_competition_and_season(db) -> tuple[Competition, Season]:
    comp = Competition(code="bundesliga_test", name="Bundesliga", country="Germany")
    db.add(comp)
    db.flush()
    season = Season(competition_id=comp.id, label="2026/27")
    db.add(season)
    db.flush()
    return comp, season


def test_merge_team_resolves_scheduled_vs_finished_duplicate(db_session):
    db = db_session
    comp, season = _make_competition_and_season(db)

    schalke = Team(canonical_name="Schalke 04")
    elversberg = Team(canonical_name="Elversberg")  # el original, usado desde el principio
    ghost = Team(canonical_name="SV Elversberg")  # el fantasma creado por el bug
    db.add_all([schalke, elversberg, ghost])
    db.flush()
    db.add(TeamNameMapping(source="api_football", source_name="SV Elversberg", team_id=ghost.id))
    db.flush()

    # El fixture original (openfootball), nunca se jugo segun la BD: quedo "scheduled" para siempre.
    stale = Match(
        provider="openfootball_fixtures",
        provider_id="bund:2026/27:schalke-elversberg",
        competition_id=comp.id,
        season_id=season.id,
        matchday=4,
        kickoff_utc=dt.datetime(2026, 9, 20, 17, 30),
        home_team_id=schalke.id,
        away_team_id=elversberg.id,
        status="scheduled",
    )
    # El resultado real, ingerido despues por api_football, pero resuelto
    # (antes del fix de alias) contra el Team fantasma -- nunca actualizo `stale`.
    real_result = Match(
        provider="api_football",
        provider_id="apif:999",
        competition_id=comp.id,
        season_id=season.id,
        matchday=4,
        kickoff_utc=dt.datetime(2026, 9, 20, 17, 30),
        home_team_id=schalke.id,
        away_team_id=ghost.id,
        status="finished",
        home_goals=2,
        away_goals=1,
    )
    db.add_all([stale, real_result])
    db.flush()
    model_version = ModelVersion(
        name="logistic_baseline",
        version="test",
        market_family="goals",
        features=[],
        hyperparameters={},
        metrics={},
        dataset_version="test",
    )
    db.add(model_version)
    db.flush()
    db.add(
        Prediction(
            match_id=stale.id,
            market="match_result",
            line=None,
            selection="home",
            model_version_id=model_version.id,
            model_probability=0.5,
            market_probability=0.45,
            market_odds=2.0,
            fair_odds=2.0,
            edge=0.05,
            expected_value=0.05,
            confidence=0.8,
            data_quality=0.8,
            bookmakers_used=4,
            bookmakers_count=4,
            explanation={"factors": []},
            features_used={"feature_names": []},
            created_at=dt.datetime(2026, 9, 15),
        )
    )
    db.flush()

    repointed, merged = _merge_team(db, duplicate=ghost, canonical=elversberg)
    db.flush()

    assert repointed == 1
    assert merged == 1

    remaining = db.query(Match).filter(Match.competition_id == comp.id).all()
    assert len(remaining) == 1
    assert remaining[0].status == "finished"
    assert remaining[0].home_goals == 2
    assert remaining[0].away_goals == 1
    assert remaining[0].away_team_id == elversberg.id

    assert db.query(Team).filter_by(id=ghost.id).one_or_none() is None
    assert db.query(Prediction).filter_by(match_id=stale.id).one_or_none() is None

    mapping = db.query(TeamNameMapping).filter_by(source="api_football", source_name="SV Elversberg").one()
    assert mapping.team_id == elversberg.id


def test_merge_team_repoints_without_conflict_when_no_sibling_match_exists(db_session):
    """Cuando el Team fantasma solo tiene partidos SIN duplicado (todavia no
    se habia ingerido ningun resultado real para esa fecha), fundir debe
    limitarse a repuntar -- sin borrar nada."""
    db = db_session
    comp, season = _make_competition_and_season(db)

    schalke = Team(canonical_name="Schalke 04")
    elversberg = Team(canonical_name="Elversberg")
    ghost = Team(canonical_name="SV Elversberg")
    db.add_all([schalke, elversberg, ghost])
    db.flush()

    only_match = Match(
        provider="api_football",
        provider_id="apif:future-1",
        competition_id=comp.id,
        season_id=season.id,
        matchday=20,
        kickoff_utc=dt.datetime(2027, 3, 1, 17, 30),
        home_team_id=schalke.id,
        away_team_id=ghost.id,
        status="scheduled",
    )
    db.add(only_match)
    db.flush()

    repointed, merged = _merge_team(db, duplicate=ghost, canonical=elversberg)
    db.flush()

    assert repointed == 1
    assert merged == 0
    assert db.query(Match).filter_by(id=only_match.id).one().away_team_id == elversberg.id
    assert db.query(Team).filter_by(id=ghost.id).one_or_none() is None
