"""`void_cancelled_predictions` (seccion "Rendimiento del modelo", pedido
real del usuario: estados de liquidacion completos -- pending/won/lost/
void/half_won/half_lost). Un partido CANCELADO nunca produce un
resultado real: su(s) Prediction(es) se liquidan como "void" (stake
devuelto), nunca como "lost" -- y se EXCLUYEN de Brier/log loss/ROI en
vez de penalizar al modelo por algo ajeno a su prediccion.

Mismo patron que test_settlement.py (entrenar un modelo real, generar una
prediccion real para un partido futuro con cuotas de mercado reales, y
simular despues el desenlace) para no duplicar infraestructura."""

from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches
from backend.app.services.evaluation_service import (
    market_track_records,
    market_track_records_with_provenance,
    real_performance_report,
    void_cancelled_predictions,
)
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.tests.fixtures.synthetic import generate_synthetic_matches


class _FakeProvider(DataProvider):
    def __init__(self, synthetic, competition_code: str):
        self._synthetic = synthetic
        self.name = f"void_test_provider_{competition_code}"
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
                    provider_id=f"void-test-{self._competition_code}-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"VoidTeam{int(r.home_team_id)}",
                    away_team_raw=f"VoidTeam{int(r.away_team_id)}",
                    home_goals=int(r.home_goals),
                    away_goals=int(r.away_goals),
                    home_shots=int(r.home_shots),
                    away_shots=int(r.away_shots),
                )
            )
        return records


def _setup_cancelled_match(competition_code: str, seed: int) -> int:
    """Igual que `test_settlement.py::_setup_finished_match`, pero el
    partido futuro se marca CANCELADO en vez de jugado con marcador."""
    COMPETITIONS.setdefault(competition_code, ("Void Test League", "Testland"))
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=3, seed=seed)
    provider = _FakeProvider(synthetic, competition_code)

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"

    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        season = db.query(Season).filter_by(competition_id=comp.id).order_by(Season.id.desc()).first()
        match = Match(
            provider="void_test_manual",
            provider_id=f"void-future-fixture-{seed}",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
            home_team_id=1,
            away_team_id=2,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        match_id = match.id
        db.add_all(
            [
                MatchOdds(
                    match_id=match_id,
                    bookmaker="bet365",
                    market="over_under_goals",
                    line=2.5,
                    selection="over",
                    price=1.90,
                    snapshot_type="pre_match",
                    recorded_at=match.kickoff_utc,
                ),
                MatchOdds(
                    match_id=match_id,
                    bookmaker="bet365",
                    market="over_under_goals",
                    line=2.5,
                    selection="under",
                    price=1.95,
                    snapshot_type="pre_match",
                    recorded_at=match.kickoff_utc,
                ),
            ]
        )

    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, competition_code, fixture_date)
        assert len(predictions) > 0

    # El partido se CANCELA antes de jugarse -- nunca va a tener marcador real.
    with session_scope() as db:
        db.query(Match).filter_by(id=match_id).update(
            {"status": "cancelled"}, synchronize_session=False
        )

    return match_id


def test_void_cancelled_predictions_creates_void_results_not_lost():
    match_id = _setup_cancelled_match("void_test_league_a", seed=11)
    with session_scope() as db:
        created = void_cancelled_predictions(db, competition_code="void_test_league_a")
        assert created > 0

    with session_scope() as db:
        predictions = db.query(Prediction).filter(Prediction.match_id == match_id).all()
        assert predictions
        for p in predictions:
            assert p.result is not None
            assert p.result.settlement_status == "void"
            assert p.result.actual_result is None


def test_void_cancelled_predictions_is_idempotent():
    match_id = _setup_cancelled_match("void_test_league_b", seed=12)
    with session_scope() as db:
        void_cancelled_predictions(db, competition_code="void_test_league_b")
    with session_scope() as db:
        second_run = void_cancelled_predictions(db, competition_code="void_test_league_b")
        assert second_run == 0
    with session_scope() as db:
        n_results = (
            db.query(PredictionResult)
            .join(Prediction, Prediction.id == PredictionResult.prediction_id)
            .filter(Prediction.match_id == match_id)
            .count()
        )
        n_predictions = db.query(Prediction).filter(Prediction.match_id == match_id).count()
        assert n_results == n_predictions


def test_void_predictions_are_excluded_from_real_performance_report():
    """Una predicción "void" no debe contarse como "lost" en Brier/ROI --
    debe quedar fuera de las métricas por completo (se reporta aparte,
    `n_void`, nunca mezclada con liquidaciones reales)."""
    competition_code = "void_test_league_c"
    _setup_cancelled_match(competition_code, seed=13)
    with session_scope() as db:
        void_cancelled_predictions(db, competition_code=competition_code)
        report = real_performance_report(db, competition_code=competition_code)

    # Ningun segmento de esta competicion cuenta la prediccion void como
    # liquidacion real (n_settled nunca incluye "void").
    for segment in report["segments"]:
        assert segment["competition_code"] != competition_code or segment["n_settled"] == 0


def test_void_predictions_are_excluded_from_market_track_records():
    """Con una sola liquidacion (void, excluida) para esta competicion, su
    propio nivel EXACTO ("competition_market") nunca puede aparecer -- si
    "void" se contase como real, podria aparecer con n=1.

    No se afirma que el dict quede vacio del todo (fiabilidad jerarquica,
    seccion 2.2): un nivel mas general (tier/mercado global) SI puede
    rellenar la clave con historico real de OTRAS competiciones -- eso es
    correcto, lo unico que esta liga void nunca debe aportar es su propia
    evidencia al nivel exacto."""
    competition_code = "void_test_league_d"
    _setup_cancelled_match(competition_code, seed=14)
    with session_scope() as db:
        void_cancelled_predictions(db, competition_code=competition_code)
        records = market_track_records_with_provenance(db, competition_code=competition_code)
        assert all(estimate.source_level != "competition_market" for estimate in records.values())
