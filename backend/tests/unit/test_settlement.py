"""Fase 4/7: settlement (`PredictionResult`), Historico, y varios de los
casos de la seccion 27 del brief no cubiertos por
`test_market_consistency.py`/`test_ranking.py`/`test_evaluation_service.py`.

Reutiliza el mismo patron que `test_evaluation_service.py` (predecir un
partido futuro, simular que se juega de verdad, comprobar que la
prediccion YA GUARDADA se liquida sin regenerarse) para no duplicar
infraestructura de fixtures.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.main import app
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.prediction.anomaly import (
    HIGH_PROBABILITY_LOW_VALUE,
    OUTLIER,
    best_prediction_per_match,
    compute_anomaly_flags,
)
from backend.app.services.data_service import ingest_matches
from backend.app.services.evaluation_service import settle_finished_predictions
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.tests.fixtures.synthetic import generate_synthetic_matches

client = TestClient(app)


class _FakeProvider(DataProvider):
    def __init__(self, synthetic, competition_code: str):
        self._synthetic = synthetic
        self.name = f"settlement_test_provider_{competition_code}"
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
                    provider_id=f"settle-test-{self._competition_code}-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"SettleTeam{int(r.home_team_id)}",
                    away_team_raw=f"SettleTeam{int(r.away_team_id)}",
                    home_goals=int(r.home_goals),
                    away_goals=int(r.away_goals),
                    home_shots=int(r.home_shots),
                    away_shots=int(r.away_shots),
                    odds=[
                        RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.9),
                        RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
                    ],
                )
            )
        return records


def _setup_finished_match(competition_code: str, home_goals: int, away_goals: int, seed: int = 321) -> int:
    """Entrena un modelo real, genera predicciones para un partido futuro
    con cuotas de mercado reales, y luego simula que el partido ya se jugo
    con el marcador dado. Devuelve el `match_id`."""
    COMPETITIONS.setdefault(competition_code, ("Settlement Test League", "Testland"))
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
            provider="settlement_test_manual",
            provider_id=f"settle-future-fixture-{seed}",
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

    with session_scope() as db:
        db.query(Match).filter_by(id=match_id).update(
            {"home_goals": home_goals, "away_goals": away_goals, "status": "finished"},
            synchronize_session=False,
        )

    return match_id


def test_settlement_creates_prediction_result_with_real_score():
    match_id = _setup_finished_match("settlement_test_league_a", home_goals=2, away_goals=1, seed=1)
    with session_scope() as db:
        created = settle_finished_predictions(db, competition_code="settlement_test_league_a")
        assert created > 0

    with session_scope() as db:
        predictions = db.query(Prediction).filter(Prediction.match_id == match_id).all()
        assert predictions
        for p in predictions:
            assert p.result is not None
            assert p.result.actual_result == "2-1"


def test_settlement_is_idempotent_never_duplicates_result():
    match_id = _setup_finished_match("settlement_test_league_b", home_goals=1, away_goals=0, seed=2)
    with session_scope() as db:
        settle_finished_predictions(db, competition_code="settlement_test_league_b")
    with session_scope() as db:
        second_run_created = settle_finished_predictions(db, competition_code="settlement_test_league_b")
        assert second_run_created == 0
    with session_scope() as db:
        n_results = (
            db.query(PredictionResult)
            .join(Prediction, Prediction.id == PredictionResult.prediction_id)
            .filter(Prediction.match_id == match_id)
            .count()
        )
        n_predictions = db.query(Prediction).filter(Prediction.match_id == match_id).count()
        assert n_results == n_predictions  # exactamente un resultado por prediccion, nunca duplicado


def test_settlement_never_mutates_predictive_fields_of_prediction():
    """Inmutabilidad absoluta (seccion 8): liquidar solo ANHADE
    `PredictionResult`, nunca toca `model_probability`/`edge`/`signal_score`
    de la `Prediction` original."""
    match_id = _setup_finished_match("settlement_test_league_c", home_goals=3, away_goals=0, seed=3)
    with session_scope() as db:
        before = {
            p.id: (p.model_probability, p.edge, p.signal_score, p.market_odds, p.model_version_id)
            for p in db.query(Prediction).filter(Prediction.match_id == match_id).all()
        }

    with session_scope() as db:
        settle_finished_predictions(db, competition_code="settlement_test_league_c")

    with session_scope() as db:
        after = db.query(Prediction).filter(Prediction.match_id == match_id).all()
        for p in after:
            assert (p.model_probability, p.edge, p.signal_score, p.market_odds, p.model_version_id) == before[p.id]
            # model_version se conserva intacto al liquidar (nunca se pierde ni se reescribe).
            assert p.model_version_id is not None


def test_correct_and_incorrect_predictions_are_determined_correctly():
    """3 goles totales (over_2_5=True, under_2_5=False): comprueba que
    `outcome` de cada mercado se deriva del resultado real, no al reves."""
    match_id = _setup_finished_match("settlement_test_league_d", home_goals=2, away_goals=1, seed=4)
    with session_scope() as db:
        settle_finished_predictions(db, competition_code="settlement_test_league_d")

    with session_scope() as db:
        predictions = db.query(Prediction).filter(Prediction.match_id == match_id).all()
        by_market = {p.market: p for p in predictions}
        if "over_2_5" in by_market:
            assert by_market["over_2_5"].result.outcome is True  # 2+1=3 > 2.5
        if "under_2_5" in by_market:
            assert by_market["under_2_5"].result.outcome is False


def test_finished_match_disappears_from_home_and_appears_in_history():
    match_id = _setup_finished_match("settlement_test_league_e", home_goals=1, away_goals=1, seed=5)
    with session_scope() as db:
        settle_finished_predictions(db, competition_code="settlement_test_league_e")

    today_resp = client.get(
        "/predictions/today", params={"competition_code": "settlement_test_league_e", "days": 30}
    )
    assert today_resp.status_code == 200
    assert all(p["match"]["id"] != match_id for p in today_resp.json())

    finished_resp = client.get(
        "/matches/fixtures/finished", params={"competition_code": "settlement_test_league_e"}
    )
    assert finished_resp.status_code == 200
    assert any(m["id"] == match_id for m in finished_resp.json())

    history_resp = client.get("/history", params={"competition_code": "settlement_test_league_e"})
    assert history_resp.status_code == 200
    matches_in_history = [row["match"]["id"] for row in history_resp.json()]
    assert match_id in matches_in_history

    detail_resp = client.get(f"/history/{match_id}")
    assert detail_resp.status_code == 200
    body = detail_resp.json()
    assert body["match"]["id"] == match_id
    assert len(body["predictions"]) > 0
    for p in body["predictions"]:
        assert "actual_result" in p and p["actual_result"] == "1-1"
        assert "is_correct" in p


def test_best_prediction_per_match_selected_by_signal_score_not_raw_probability():
    """Seccion 1: "mejor prediccion" es la de mayor `signal_score`, nunca
    simplemente la de mayor `model_probability`/edge/cuota en bruto."""
    match_id = _setup_finished_match("settlement_test_league_f", home_goals=2, away_goals=0, seed=6)
    with session_scope() as db:
        predictions = db.query(Prediction).filter(Prediction.match_id == match_id).all()
        with_market = [p for p in predictions if p.market_odds is not None and p.signal_score is not None]
        if not with_market:
            return  # sin cuotas de mercado en este seed, nada que comprobar
        best = best_prediction_per_match({match_id: predictions}).get(match_id)
        assert best is not None
        max_score = max(p.signal_score or 0.0 for p in with_market)
        assert best.signal_score == max_score


def test_high_probability_low_value_flag_distinguishes_from_best_value():
    """Seccion 3/18: una senhal de probabilidad muy alta pero edge minimo se
    etiqueta HIGH_PROBABILITY_LOW_VALUE, distinta de una senhal de "mejor
    valor" real (edge alto)."""

    class _Stub:
        def __init__(self, id, market, model_probability, edge, confidence=0.8, data_quality=0.8,
                     bookmakers_used=5, market_odds_min=1.9, market_odds_max=2.0, market_odds_median=1.95):
            self.id = id
            self.market = market
            self.model_probability = model_probability
            self.edge = edge
            self.confidence = confidence
            self.data_quality = data_quality
            self.bookmakers_used = bookmakers_used
            self.market_odds_min = market_odds_min
            self.market_odds_max = market_odds_max
            self.market_odds_median = market_odds_median

    high_prob_low_value = _Stub(1, "home_win", model_probability=0.90, edge=0.01)
    best_value = _Stub(2, "away_win", model_probability=0.55, edge=0.15)

    flags_a = compute_anomaly_flags(high_prob_low_value, [high_prob_low_value])
    flags_b = compute_anomaly_flags(best_value, [best_value])

    assert HIGH_PROBABILITY_LOW_VALUE in flags_a
    assert HIGH_PROBABILITY_LOW_VALUE not in flags_b


def test_model_performance_endpoint_uses_only_settled_predictions():
    """Fase 5: `/models/performance` se calcula SOLO sobre predicciones ya
    liquidadas -- antes de liquidar no debe aparecer ningun segmento para
    esta competicion; despues, si."""
    match_id = _setup_finished_match("settlement_test_league_g", home_goals=2, away_goals=2, seed=7)

    before = client.get(
        "/models/performance", params={"competition_code": "settlement_test_league_g"}
    ).json()
    assert before["segments"] == []

    with session_scope() as db:
        settle_finished_predictions(db, competition_code="settlement_test_league_g")

    after = client.get(
        "/models/performance", params={"competition_code": "settlement_test_league_g"}
    ).json()
    assert len(after["segments"]) > 0
    for segment in after["segments"]:
        assert segment["competition_code"] == "settlement_test_league_g"
        assert segment["n_settled"] > 0
        assert "reliability_curve" in segment["model_quality"]
        assert isinstance(segment["performance_by_probability_bucket"], list)
    assert match_id  # sanity: el partido existe y se uso para liquidar


def test_outlier_market_quality_does_not_dominate_best_selection():
    """Seccion 18: una cuota respaldada por 1 sola casa (LOW quality) se
    etiqueta OUTLIER y su `signal_score` se penaliza vs una cuota HIGH con
    mas casas, aunque el edge nominal sea parecido."""

    class _Stub:
        def __init__(self, id, bookmakers_used, market_odds_min, market_odds_max, market_odds_median):
            self.id = id
            self.market = "over_2_5"
            self.bookmakers_used = bookmakers_used
            self.market_odds_min = market_odds_min
            self.market_odds_max = market_odds_max
            self.market_odds_median = market_odds_median
            self.model_probability = 0.6
            self.edge = 0.08
            self.confidence = 0.8
            self.data_quality = 0.8

    outlier = _Stub(1, bookmakers_used=1, market_odds_min=2.5, market_odds_max=2.5, market_odds_median=2.5)
    solid = _Stub(2, bookmakers_used=6, market_odds_min=1.85, market_odds_max=1.95, market_odds_median=1.90)

    from backend.app.prediction.ranking import signal_score

    flags = compute_anomaly_flags(outlier, [outlier])
    assert OUTLIER in flags
    assert signal_score(solid) > signal_score(outlier)
