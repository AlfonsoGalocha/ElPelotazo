from __future__ import annotations

import datetime as dt

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match, MatchOddsHistory
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.ingestion.odds.provider import FixtureOddsSnapshot
from backend.app.market.clv import compute_clv
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import attach_odds_to_scheduled_matches, ingest_matches
from backend.app.services.evaluation_service import settle_finished_predictions


def test_compute_clv_positive_when_odds_taken_beat_the_closing_line():
    assert compute_clv(2.00, 1.80) > 0


def test_compute_clv_negative_when_the_market_moves_against_you():
    assert compute_clv(1.80, 2.00) < 0


def test_compute_clv_zero_with_no_movement():
    assert compute_clv(1.90, 1.90) == 0.0


def test_compute_clv_none_on_missing_or_invalid_odds():
    assert compute_clv(None, 1.90) is None
    assert compute_clv(1.90, None) is None
    assert compute_clv(1.0, 1.90) is None
    assert compute_clv(1.90, 0.5) is None


class _FakeOddsProvider:
    name = "the_odds_api"

    def __init__(self, snapshots: list[FixtureOddsSnapshot]):
        self._snapshots = snapshots

    def is_available(self) -> bool:
        return True

    def fetch_odds(self, competition_code: str) -> list[FixtureOddsSnapshot]:
        return self._snapshots


class _FixtureProvider(DataProvider):
    """Crea un unico partido programado via el pipeline real de ingesta
    (nunca un `Match` a mano con FKs de equipo inventadas): los nombres de
    equipo aqui deben ser EXACTAMENTE los mismos que usara la cuota
    despues, para que `resolve_team_id` les asigne el MISMO team_id real
    y `attach_odds_to_scheduled_matches` pueda casarlos -- hardcodear
    `home_team_id`/`away_team_id` a un entero arbitrario es fragil: en una
    base de datos compartida entre tests (ver conftest.py, un unico
    SQLite por sesion de pytest) esos ids pueden pertenecer a CUALQUIER
    otro equipo segun el orden de ejecucion."""

    name = "clv_history_test_provider"

    def __init__(self, kickoff: dt.datetime):
        self._kickoff = kickoff

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        return [
            RawMatchRecord(
                provider=self.name,
                provider_id="clv-history-fixture-1",
                competition_code=competition_code,
                season_label=season_label,
                date=self._kickoff.date().isoformat(),
                home_team_raw="ClvHistoryHome",
                away_team_raw="ClvHistoryAway",
                home_goals=None,
                away_goals=None,
            )
        ]


def test_attaching_odds_twice_appends_to_history_without_losing_the_earlier_snapshot():
    """MatchOdds sigue borrando-y-reemplazando (snapshot vigente); la tabla
    nueva MatchOddsHistory debe conservar AMBOS momentos, nunca borrar el
    anterior -- es la unica forma de poder calcular CLV despues."""
    competition_code = "clv-history-test-league"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    kickoff = dt.datetime.utcnow() + dt.timedelta(days=1)

    with session_scope() as db:
        ingest_matches(db, _FixtureProvider(kickoff), competition_code, "2025/26")
        match_id = (
            db.query(Match)
            .filter_by(provider="clv_history_test_provider", provider_id="clv-history-fixture-1")
            .one()
        ).id

    def _attach(price: float) -> None:
        with session_scope() as db:
            m = db.query(Match).filter_by(id=match_id).one()
            snapshot = FixtureOddsSnapshot(
                home_team_raw="ClvHistoryHome",
                away_team_raw="ClvHistoryAway",
                commence_time=m.kickoff_utc.replace(tzinfo=dt.timezone.utc),
                odds=[RawOddsRecord("bet365", "over_under_goals", 2.5, "over", price)],
            )
            attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), competition_code)

    _attach(1.80)
    _attach(2.10)  # el mercado se movio despues

    with session_scope() as db:
        history = (
            db.query(MatchOddsHistory)
            .filter_by(match_id=match_id, bookmaker="bet365", selection="over")
            .order_by(MatchOddsHistory.recorded_at.asc())
            .all()
        )
        assert [row.price for row in history] == [1.80, 2.10]


def test_deleting_a_match_cascades_to_its_odds_history():
    """Bug real encontrado al escribir este modulo (seccion 'ficheros y
    errores'): `reset_scheduled` (cli.py) borra `Match` enteros, y SQLite
    reutiliza rowids de filas borradas -- sin cascade, un `MatchOddsHistory`
    huerfano de un partido borrado podia "adoptarse" por un partido NUEVO
    que reciclara el mismo id, corrompiendo closing_line_value de un
    partido completamente distinto. `odds`/`statistics` ya tenian este
    cascade; `odds_history` debe tenerlo igual."""
    competition_code = "clv-cascade-test-league"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))

    with session_scope() as db:
        comp = Competition(code=competition_code, name=competition_code, country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2025/26")
        db.add(season)
        db.flush()
        match = Match(
            provider="clv_cascade_test",
            provider_id="clv-cascade-fixture-1",
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
        db.add(
            MatchOddsHistory(
                match_id=match_id,
                bookmaker="bet365",
                market="over_under_goals",
                line=2.5,
                selection="over",
                price=1.90,
                snapshot_type="pre_match",
                recorded_at=dt.datetime.utcnow(),
            )
        )

    with session_scope() as db:
        match = db.query(Match).filter_by(id=match_id).one()
        db.delete(match)

    with session_scope() as db:
        assert db.query(MatchOddsHistory).filter_by(match_id=match_id).all() == []


def test_settlement_computes_closing_line_value_from_the_latest_recorded_odds():
    competition_code = "clv-settlement-test-league"
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))

    with session_scope() as db:
        comp = Competition(code=competition_code, name=competition_code, country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2025/26")
        db.add(season)
        db.flush()
        model_version = ModelVersion(
            name="clv-test",
            version="v1",
            market_family="goals",
            features=[],
            hyperparameters={},
            metrics={},
            dataset_version="test",
        )
        db.add(model_version)
        match = Match(
            provider="clv_settlement_test",
            provider_id="clv-settlement-fixture-1",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() - dt.timedelta(hours=2),
            home_team_id=1,
            away_team_id=2,
            status="finished",
            home_goals=2,
            away_goals=1,
        )
        db.add(match)
        db.flush()
        match_id = match.id

        # La prediccion se genero cuando la cuota de consenso era 1.80.
        prediction = Prediction(
            match_id=match_id,
            market="over_2_5",
            line=2.5,
            selection="over",
            model_version_id=model_version.id,
            model_probability=0.6,
            market_odds=1.80,
            fair_odds=1.0 / 0.6,
            confidence=0.8,
            data_quality=0.8,
            explanation={},
            features_used={},
            created_at=dt.datetime.utcnow() - dt.timedelta(hours=3),
        )
        db.add(prediction)
        db.flush()
        prediction_id = prediction.id

        # Dos snapshots registrados en MatchOddsHistory: el mercado se movio
        # de 1.80 (cuando se genero la prediccion) a 1.50 (cierre real).
        for price, hours_before in [(1.80, 3), (1.50, 1)]:
            db.add(
                MatchOddsHistory(
                    match_id=match_id,
                    bookmaker="bet365",
                    market="over_under_goals",
                    line=2.5,
                    selection="over",
                    price=price,
                    snapshot_type="pre_match",
                    recorded_at=dt.datetime.utcnow() - dt.timedelta(hours=hours_before),
                )
            )

    with session_scope() as db:
        created = settle_finished_predictions(db, competition_code=competition_code)
        assert created == 1

    with session_scope() as db:
        result = db.query(PredictionResult).filter_by(prediction_id=prediction_id).one()
        assert result.closing_line_value is not None
        # Tomada a 1.80, cierre a 1.50: el mercado se movio A FAVOR (la
        # cuota bajo despues, es decir la seleccion se volvio mas probable
        # para el mercado -- quien tomo 1.80 consiguio mejor valor que
        # quien apostó al cierre).
        assert result.closing_line_value > 0
        assert result.closing_line_value == compute_clv(1.80, 1.50)
