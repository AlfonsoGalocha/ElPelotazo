"""Verificacion end-to-end real (auditoria v2.8.2) de que, cuando
`corner_calibration_choice` dice `"corner_survival"` para un mercado de
corners, la PREDICCION REAL que `generate_predictions_for_competition`
guarda en la base de datos lleva esa probabilidad -- no la del calibrador
isotonico generico. Antes de este fix, `corner_survival` se entrenaba y se
guardaba en cada artefacto pero el pipeline real nunca lo usaba (solo la
vista previa de candidatos de Champions League); de 1.636 predicciones de
corners reales en produccion, solo 15 (0.9%) llegaban a tener
`calibrated_probability`.

Mismo patron de datos sinteticos e ingesta real que
`test_promotion_service.py` (nunca mocks del propio modelo: la
verificacion solo tiene sentido con artefactos entrenados de verdad).

La ELECCION de metodo (isotonic vs corner_survival) ya se prueba de forma
aislada y determinista en `test_model_service_calibration.py`
(`_corner_survival_beats_isotonic`) -- aqui se fuerza la eleccion a mano
sobre un artefacto YA entrenado de verdad, para probar el CABLEADO
(`_predict_fixture_markets` -> `generate_predictions_for_competition` ->
`Prediction` guardada), no la estadistica de cual gana."""

from __future__ import annotations

import datetime as dt

import joblib

from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.ingestion.base import DataProvider, RawMatchRecord
from backend.app.models.calibration.corners import correct_probability
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches
from backend.app.services.model_service import load_model_artifact, train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.tests.fixtures.synthetic import generate_synthetic_matches


class _FakeProviderWithCorners(DataProvider):
    def __init__(self, synthetic, competition_code: str):
        self._synthetic = synthetic
        self.name = f"corner_survival_pipeline_test_{competition_code}"
        self._competition_code = competition_code

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        rows = self._synthetic[self._synthetic["season_label"] == season_label]
        return [
            RawMatchRecord(
                provider=self.name,
                provider_id=f"corner-pipeline-{self._competition_code}-{r.match_id}",
                competition_code=competition_code,
                season_label=season_label,
                date=r.date.date().isoformat(),
                home_team_raw=f"CornerPipelineTeam{int(r.home_team_id)}",
                away_team_raw=f"CornerPipelineTeam{int(r.away_team_id)}",
                home_goals=int(r.home_goals),
                away_goals=int(r.away_goals),
                home_shots=int(r.home_shots),
                away_shots=int(r.away_shots),
                home_corners=int(r.home_corners),
                away_corners=int(r.away_corners),
            )
            for _, r in rows.iterrows()
        ]


def test_corner_survival_choice_reaches_the_real_stored_prediction():
    competition_code = "corner_survival_pipeline_league"
    COMPETITIONS.setdefault(competition_code, ("Corner Survival Pipeline League", "Testland"))
    synthetic = generate_synthetic_matches(n_teams=10, n_seasons=4, seed=909)
    provider = _FakeProviderWithCorners(synthetic, competition_code)

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"
        model_version_id = result["model_version_id"]

    with session_scope() as db:
        model_version = db.get(ModelVersion, model_version_id)
        # Se activa directamente (igual que test_promotion_service.py):
        # esta prueba verifica el cableado de corners, no el gate de
        # promocion (ya probado aparte).
        model_version.is_active = True
        artifact = load_model_artifact(model_version)
        assert "corners_model" in artifact, "la liga sintetica debe traer corners reales"

        # Fuerza la eleccion a mano (la comparacion real ya esta probada
        # en test_model_service_calibration.py): corner_survival SI se usa
        # para corners_over_9_5, con parametros validos conocidos.
        forced_params = {"slope": 0.8, "intercept": -0.15, "version": "corner-survival-logistic-v1"}
        artifact["corner_survival"] = forced_params
        artifact["corner_calibration_choice"] = {"corners_over_9_5": "corner_survival"}
        artifact["calibrators"] = {
            k: v for k, v in (artifact.get("calibrators") or {}).items() if k != "corners_over_9_5"
        }
        joblib.dump(artifact, model_version.artifact_path)

        # Un partido programado, sin resultado, en un futuro cercano --
        # el unico universo que `generate_predictions_for_competition`
        # considera.
        any_match = db.query(Match).filter(Match.provider == provider.name).first()
        scheduled = Match(
            provider=provider.name,
            provider_id=f"corner-pipeline-{competition_code}-scheduled",
            competition_id=any_match.competition_id,
            season_id=any_match.season_id,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
            home_team_id=any_match.home_team_id,
            away_team_id=any_match.away_team_id,
            status="scheduled",
        )
        db.add(scheduled)
        db.flush()
        scheduled_id = scheduled.id

    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, competition_code)
        assert predictions, "deberia generar predicciones para el partido programado"

    with session_scope() as db:
        stored = (
            db.query(Prediction)
            .filter(Prediction.match_id == scheduled_id, Prediction.market == "corners_over_9_5")
            .one()
        )
        expected = correct_probability(stored.model_probability, forced_params, under=False)
        assert stored.calibrated_probability == expected
        # `apply_coherent_calibration` (sin tocar, ya existente) reetiqueta
        # TODA probabilidad calibrada como "validated_marginal" al pasar
        # por el paso de coherencia -- mismo comportamiento de siempre
        # para los mercados de goles isotonicos, se confirma aqui que
        # corners no es la excepcion: el VALOR es el de corner_survival,
        # aunque la etiqueta de procedencia detallada no sobreviva ese
        # paso (limitacion preexistente, fuera de alcance de esta fase).
        assert (stored.explanation or {}).get("calibration_source") == "validated_marginal"

        # Coherencia over/under preservada (seccion "no rompas lo que ya
        # funcionaba"): corners_under_9_5 sigue siendo 1 - el over corregido,
        # via `coherent.py::COMPLEMENTS` (sin tocar).
        under = (
            db.query(Prediction)
            .filter(Prediction.match_id == scheduled_id, Prediction.market == "corners_under_9_5")
            .one()
        )
        assert abs(under.calibrated_probability - (1 - stored.calibrated_probability)) < 1e-9
