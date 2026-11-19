"""`promotion_service.evaluate_and_promote` (seccion "Rendimiento del
modelo", pedido real del usuario: "Solo se activa una nueva version
cuando demuestra mejoras fuera de muestra... No quiero reentrenamientos
automaticos que puedan empeorar el sistema sin validacion").

Antes de este modulo, `generate_predictions_for_competition` usaba
SIEMPRE la ModelVersion mas reciente, sin comparar nunca con la anterior.
Mismo patron de datos sinteticos que test_settlement.py/test_void_settlement.py
para reutilizar infraestructura real de entrenamiento (nunca mocks del
propio modelo: la comparacion solo tiene sentido con artefactos
entrenados de verdad)."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches
from backend.app.services.model_service import train_competition_models
from backend.app.services.promotion_service import evaluate_and_promote
from backend.tests.fixtures.synthetic import generate_synthetic_matches


class _FakeProvider(DataProvider):
    def __init__(self, synthetic, competition_code: str):
        self._synthetic = synthetic
        self.name = f"promotion_test_provider_{competition_code}"
        self._competition_code = competition_code

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        rows = self._synthetic[self._synthetic["season_label"] == season_label]
        records = []
        for _, r in rows.iterrows():
            records.append(
                RawMatchRecord(
                    provider=self.name,
                    provider_id=f"promo-test-{self._competition_code}-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"PromoTeam{int(r.home_team_id)}",
                    away_team_raw=f"PromoTeam{int(r.away_team_id)}",
                    home_goals=int(r.home_goals),
                    away_goals=int(r.away_goals),
                    home_shots=int(r.home_shots),
                    away_shots=int(r.away_shots),
                )
            )
        return records


def _ingest_and_train(competition_code: str, seed: int, n_seasons: int = 3):
    COMPETITIONS.setdefault(competition_code, ("Promotion Test League", "Testland"))
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=n_seasons, seed=seed)
    provider = _FakeProvider(synthetic, competition_code)
    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)
    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"
        return result["model_version_id"]


def test_first_candidate_stays_inactive_without_an_incumbent():
    competition_code = "promotion_test_league_first"
    version_id = _ingest_and_train(competition_code, seed=101)

    with session_scope() as db:
        candidate = db.get(ModelVersion, version_id)
        decision = evaluate_and_promote(db, competition_code, candidate)
        assert decision["promoted"] is False
        assert decision["reason"] == "no_previous_version"

    with session_scope() as db:
        assert db.get(ModelVersion, version_id).is_active is False


def test_candidate_not_promoted_when_too_few_out_of_sample_matches():
    """Con una UNICA temporada de datos, `_pick_holdout_season` (ver
    model_service.py: exige al menos 2 temporadas) no separa ningun
    holdout -- `training_end` coincide con la fecha mas reciente de TODO
    el dataset, asi que no existe ningun partido real estrictamente
    posterior con el que comparar. Entrenar una segunda vez sobre los
    MISMOS datos (nada nuevo jugado) debe seguir sin poder promocionar,
    por fiable que parezca el candidato en el holdout de siempre."""
    competition_code = "promotion_test_league_insufficient"
    first_id = _ingest_and_train(competition_code, seed=102, n_seasons=1)
    with session_scope() as db:
        first = db.get(ModelVersion, first_id)
        first.is_active = True

    # Segundo entrenamiento, MISMOS datos (nada nuevo jugado desde el primero).
    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        second_id = result["model_version_id"]

    with session_scope() as db:
        second = db.get(ModelVersion, second_id)
        decision = evaluate_and_promote(db, competition_code, second)
        assert decision["promoted"] is False
        assert decision["reason"] == "insufficient_out_of_sample_data"

    with session_scope() as db:
        # El candidato queda guardado (nunca se borra), solo inactivo; el
        # anterior sigue siendo el activo real.
        assert db.get(ModelVersion, second_id).is_active is False
        assert db.get(ModelVersion, first_id).is_active is True


def test_matches_used_by_new_candidate_do_not_replace_the_active_version():
    """Only results after both model cuts can support a comparison."""
    competition_code = "promotion_test_league_worse"
    first_id = _ingest_and_train(competition_code, seed=103)
    with session_scope() as db:
        first = db.get(ModelVersion, first_id)
        first.is_active = True

    # Partidos NUEVOS, finalizados, con fecha posterior al training_end
    # del primer modelo -- evidencia real fuera de muestra con la que
    # comparar.
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        latest = (
            db.query(Match)
            .filter(Match.competition_id == comp.id, Match.home_goals.isnot(None))
            .order_by(Match.kickoff_utc.desc())
            .first()
        )
        base_date = latest.kickoff_utc
        for i in range(25):
            db.add(
                Match(
                    provider="promotion_test_manual",
                    provider_id=f"promo-oos-{competition_code}-{i}",
                    competition_id=comp.id,
                    season_id=latest.season_id,
                    kickoff_utc=base_date + dt.timedelta(days=i + 1),
                    home_team_id=1,
                    away_team_id=2,
                    home_goals=5,
                    away_goals=0,
                    status="finished",
                )
            )

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        second_id = result["model_version_id"]

    with session_scope() as db:
        second = db.get(ModelVersion, second_id)
        decision = evaluate_and_promote(db, competition_code, second)
        assert decision["promoted"] is False
        assert decision["reason"] == "insufficient_out_of_sample_data"

    with session_scope() as db:
        assert db.get(ModelVersion, second_id).is_active is False
        assert db.get(ModelVersion, first_id).is_active is True
