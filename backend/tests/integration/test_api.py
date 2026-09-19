from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import Prediction
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.ingestion.odds.provider import FixtureOddsSnapshot
from backend.app.main import app
from backend.app.prediction.market_labels import MARKET_DEFINITIONS
from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS
from backend.app.services.data_service import (
    attach_odds_to_scheduled_matches,
    attach_secondary_odds_to_scheduled_matches,
    ingest_matches,
)
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.tests.fixtures.synthetic import generate_synthetic_matches

TOTAL_MVP_MARKETS = len(MARKET_DEFINITIONS) + len(SECONDARY_MARKET_DEFINITIONS)


class _FakeProvider(DataProvider):
    name = "integration_test_provider"

    def __init__(self, synthetic):
        self._synthetic = synthetic

    def is_available(self) -> bool:
        return True

    def fetch_matches(self, competition_code: str, season_label: str) -> list[RawMatchRecord]:
        rows = self._synthetic[self._synthetic["season_label"] == season_label]
        records = []
        for _, r in rows.iterrows():
            records.append(
                RawMatchRecord(
                    provider=self.name,
                    provider_id=f"itest-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"IntegrationTeam{int(r.home_team_id)}",
                    away_team_raw=f"IntegrationTeam{int(r.away_team_id)}",
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


@pytest.fixture(scope="module")
def seeded_competition_code():
    """Ingiere partidos sinteticos, entrena y genera predicciones para un
    partido futuro, end-to-end, usando el pipeline real (no mocks internos)."""
    competition_code = "laliga_integration_test"
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=3, seed=99)
    provider = _FakeProvider(synthetic)

    from backend.app.normalization.competitions import COMPETITIONS

    COMPETITIONS.setdefault(competition_code, ("La Liga (integration test)", "Spain"))

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"

    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        season = db.query(Season).filter_by(competition_id=comp.id).order_by(Season.id.desc()).first()
        db.add(
            Match(
                provider="integration_test_manual",
                provider_id="future-fixture-1",
                competition_id=comp.id,
                season_id=season.id,
                kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
                home_team_id=1,
                away_team_id=2,
                status="scheduled",
            )
        )

    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, competition_code, fixture_date)
        assert len(predictions) == TOTAL_MVP_MARKETS  # goles + tarjetas + corners

    return competition_code


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_competitions_endpoint_lists_seeded_competition(seeded_competition_code):
    client = TestClient(app)
    response = client.get("/competitions")
    assert response.status_code == 200
    codes = [c["code"] for c in response.json()]
    assert seeded_competition_code in codes


def test_predictions_today_returns_all_five_markets(seeded_competition_code):
    client = TestClient(app)
    response = client.get("/predictions/today")
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= TOTAL_MVP_MARKETS
    markets = {p["market"] for p in body}
    assert markets == set(MARKET_DEFINITIONS) | set(SECONDARY_MARKET_DEFINITIONS)

    for prediction in body:
        assert 0.0 <= prediction["model_probability"] <= 1.0
        assert 0.0 <= prediction["confidence"] <= 1.0
        assert 0.0 <= prediction["data_quality"] <= 1.0
        assert prediction["fair_odds"] > 1.0
        assert prediction["signal_tier"] in {"HIGH_DATA_SUPPORT", "MEDIUM_DATA_SUPPORT", "LOW_DATA_SUPPORT"}


def test_match_predictions_endpoint(seeded_competition_code):
    client = TestClient(app)
    today = client.get("/predictions/today").json()
    match_id = today[0]["match"]["id"]

    response = client.get(f"/matches/{match_id}/predictions")
    assert response.status_code == 200
    assert len(response.json()) == TOTAL_MVP_MARKETS


def test_models_endpoint_lists_trained_model(seeded_competition_code):
    client = TestClient(app)
    response = client.get("/models")
    assert response.status_code == 200
    assert any(m["dataset_version"].startswith(seeded_competition_code) for m in response.json())


def test_get_nonexistent_match_returns_404():
    client = TestClient(app)
    response = client.get("/matches/999999")
    assert response.status_code == 404


def test_regenerating_predictions_does_not_duplicate_rows(seeded_competition_code):
    """Regresion: `generate_predictions_for_competition` insertaba SIEMPRE
    filas nuevas sin borrar las anteriores. Como el flujo pensado (`refresh`,
    `predict-upcoming`) se ejecuta repetidamente sobre los MISMOS partidos
    programados para refrescar el dashboard, esto acumulaba duplicados
    (un mismo partido apareciendo 2-3 veces en "Las 5 mejores predicciones").
    Regenerar sobre los mismos partidos debe SUSTITUIR, no acumular.
    """
    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        first_run = generate_predictions_for_competition(db, seeded_competition_code, fixture_date)
        assert len(first_run) == TOTAL_MVP_MARKETS

    with session_scope() as db:
        second_run = generate_predictions_for_competition(db, seeded_competition_code, fixture_date)
        assert len(second_run) == TOTAL_MVP_MARKETS
        match_id = second_run[0].match_id

    client = TestClient(app)
    response = client.get(f"/matches/{match_id}/predictions")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == TOTAL_MVP_MARKETS
    markets = [p["market"] for p in body]
    assert len(markets) == len(set(markets))


class _FakeOddsProvider:
    """Nunca se probo `OddsApiProvider` real contra ninguna cuota simulada
    (ni siquiera con un mock): este test cubre el flujo completo
    attach -> regenerar predicciones sin depender de la red real."""

    name = "the_odds_api"

    def __init__(self, snapshots: list[FixtureOddsSnapshot]):
        self._snapshots = snapshots

    def is_available(self) -> bool:
        return True

    def fetch_odds(self, competition_code: str) -> list[FixtureOddsSnapshot]:
        return self._snapshots


def test_attach_odds_matches_by_team_and_refreshes_predictions(seeded_competition_code):
    """El equipo 1 vs equipo 2 del partido futuro sembrado por
    `seeded_competition_code` se llama, por normalizacion sin alias,
    "IntegrationTeam1"/"IntegrationTeam2" (ver `_FakeProvider` arriba). Si
    una fuente de cuotas devuelve exactamente esos mismos nombres, debe
    casar y las predicciones de ese partido deben pasar a tener
    market_probability/market_odds/edge no nulos."""
    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        match = (
            db.query(Match)
            .filter(Match.competition_id == comp.id, Match.status == "scheduled")
            .one()
        )
        snapshot = FixtureOddsSnapshot(
            home_team_raw="IntegrationTeam1",
            away_team_raw="IntegrationTeam2",
            commence_time=match.kickoff_utc.replace(tzinfo=dt.timezone.utc),
            odds=[
                RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90),
                RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
            ],
        )
        provider = _FakeOddsProvider([snapshot])
        result = attach_odds_to_scheduled_matches(db, provider, seeded_competition_code)
        assert result.fixtures_fetched == 1
        assert result.matched == 1
        assert result.matched_match_ids == [match.id]
        assert result.unmatched_examples == []

    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, seeded_competition_code, fixture_date)
        by_market = {p.market: p for p in predictions}
        over_2_5 = by_market["over_2_5"]
        assert over_2_5.market_odds is not None
        assert over_2_5.market_probability is not None
        assert over_2_5.edge is not None
        assert over_2_5.expected_value is not None


def test_attach_odds_reports_unmatched_examples_for_unknown_teams(seeded_competition_code):
    """Si la fuente de cuotas devuelve nombres de equipo que no casan con
    ningun partido programado (nombre distinto, fecha demasiado lejana...),
    no debe fallar en silencio: `unmatched_examples` debe exponer el par
    (local, visitante) exacto que no caso, para poder diagnosticar."""
    with session_scope() as db:
        snapshot = FixtureOddsSnapshot(
            home_team_raw="Equipo Que No Existe",
            away_team_raw="Otro Equipo Fantasma",
            commence_time=dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1),
            odds=[RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90)],
        )
        provider = _FakeOddsProvider([snapshot])
        result = attach_odds_to_scheduled_matches(db, provider, seeded_competition_code)
        assert result.fixtures_fetched == 1
        assert result.matched == 0
        assert result.unmatched_examples == [("Equipo Que No Existe", "Otro Equipo Fantasma")]


def test_top_signals_only_includes_predictions_with_valid_market(seeded_competition_code):
    """Se apoya en `test_attach_odds_matches_by_team_and_refreshes_predictions`
    (mismo modulo, misma competicion de prueba): tras esa prueba, el
    partido futuro tiene cuota SOLO para over_2_5 (goles), nunca para
    tarjetas/corners. `/predictions/top-signals` ("Mejores señales") NUNCA
    debe devolver una prediccion sin mercado, sea cual sea su probabilidad."""
    client = TestClient(app)
    response = client.get("/predictions/top-signals", params={"limit": 100})
    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    for prediction in body:
        assert prediction["has_market"] is True
        assert prediction["market_odds"] is not None
        assert prediction["market_probability"] is not None
        assert prediction["edge"] is not None and prediction["edge"] >= 0
        assert not prediction["market"].startswith("cards_")
        assert not prediction["market"].startswith("corners_")


def test_top_signals_default_window_excludes_far_future_matches(seeded_competition_code):
    """Sin limite de dias, "las mejores predicciones" podia mezclar un
    partido de esta semana con otro de dentro de 3, sin fecha visible en
    el widget -- parece un error de datos (el mismo equipo "dos veces en
    la misma jornada") aunque no lo sea. Por defecto solo debe mirar los
    proximos dias; con una ventana mas amplia (`days`), el partido lejano
    aparece."""
    far_kickoff = dt.datetime.utcnow() + dt.timedelta(days=10)
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        far_match = Match(
            provider="integration_test_manual",
            provider_id="future-fixture-far",
            competition_id=comp.id,
            season_id=db.query(Match).filter_by(competition_id=comp.id).first().season_id,
            kickoff_utc=far_kickoff,
            home_team_id=3,
            away_team_id=4,
            status="scheduled",
        )
        db.add(far_match)
        db.flush()
        far_match_id = far_match.id

        snapshot = FixtureOddsSnapshot(
            home_team_raw="IntegrationTeam3",
            away_team_raw="IntegrationTeam4",
            commence_time=far_kickoff.replace(tzinfo=dt.timezone.utc),
            odds=[
                RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90),
                RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
            ],
        )
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), seeded_competition_code)

    with session_scope() as db:
        generate_predictions_for_competition(db, seeded_competition_code, far_kickoff.date())

    client = TestClient(app)
    default_response = client.get("/predictions/top-signals", params={"limit": 100})
    default_match_ids = {p["match"]["id"] for p in default_response.json()}
    assert far_match_id not in default_match_ids

    wide_response = client.get(
        "/predictions/top-signals", params={"limit": 100, "days": 15, "scope": "all_upcoming"}
    )
    wide_match_ids = {p["match"]["id"] for p in wide_response.json()}
    assert far_match_id in wide_match_ids

    # `date` filtra a un dia CONCRETO en vez de la ventana relativa: el
    # partido lejano (fuera de la ventana por defecto de `days`) debe
    # aparecer si se pide exactamente su dia, e ignorar `days`.
    exact_day_response = client.get(
        "/predictions/top-signals", params={"limit": 100, "date": far_kickoff.date().isoformat()}
    )
    exact_day_match_ids = {p["match"]["id"] for p in exact_day_response.json()}
    assert far_match_id in exact_day_match_ids

    other_day_response = client.get(
        "/predictions/top-signals",
        params={"limit": 100, "date": (far_kickoff.date() + dt.timedelta(days=1)).isoformat()},
    )
    other_day_match_ids = {p["match"]["id"] for p in other_day_response.json()}
    assert far_match_id not in other_day_match_ids


def test_top_signals_default_scope_excludes_next_round_even_with_higher_edge(seeded_competition_code):
    """Acceptance criteria 1-2 del pedido de revision integral: un partido
    de la jornada SIGUIENTE no debe aparecer en 'Mejores señales' por
    defecto ni aunque tenga mas edge que uno de la jornada actual -- el
    filtro es por JORNADA (matchday real), no por ventana de dias ni por
    ranking. `scope=all_upcoming` si se quiere ver de todos modos."""
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        season = db.query(Match).filter_by(competition_id=comp.id).first().season_id

        current_round_match = Match(
            provider="integration_test_manual",
            provider_id="current-round-fixture",
            competition_id=comp.id,
            season_id=season,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
            home_team_id=5,
            away_team_id=6,
            status="scheduled",
            matchday=10,
        )
        next_round_match = Match(
            provider="integration_test_manual",
            provider_id="next-round-fixture",
            competition_id=comp.id,
            season_id=season,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=3),
            home_team_id=7,
            away_team_id=8,
            status="scheduled",
            matchday=11,
        )
        db.add_all([current_round_match, next_round_match])
        db.flush()
        current_round_match_id = current_round_match.id
        next_round_match_id = next_round_match.id
        current_round_kickoff = current_round_match.kickoff_utc
        next_round_kickoff = next_round_match.kickoff_utc

        snapshots = [
            FixtureOddsSnapshot(
                home_team_raw="IntegrationTeam5",
                away_team_raw="IntegrationTeam6",
                commence_time=current_round_kickoff.replace(tzinfo=dt.timezone.utc),
                odds=[
                    RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90),
                    RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
                ],
            ),
            FixtureOddsSnapshot(
                home_team_raw="IntegrationTeam7",
                away_team_raw="IntegrationTeam8",
                commence_time=next_round_kickoff.replace(tzinfo=dt.timezone.utc),
                odds=[
                    RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90),
                    RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
                ],
            ),
        ]
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider(snapshots), seeded_competition_code)

    with session_scope() as db:
        generate_predictions_for_competition(db, seeded_competition_code, current_round_kickoff.date())
        generate_predictions_for_competition(db, seeded_competition_code, next_round_kickoff.date())

    # El partido de la jornada SIGUIENTE se fuerza a tener mucho mas edge
    # que el de la jornada actual, para probar que aun asi no aparece por
    # defecto: el filtro es por jornada, nunca "el que tenga mas edge gana".
    with session_scope() as db:
        db.query(Prediction).filter(Prediction.match_id == next_round_match_id).update(
            {"edge": 0.40}, synchronize_session=False
        )
        db.query(Prediction).filter(Prediction.match_id == current_round_match_id).update(
            {"edge": 0.05}, synchronize_session=False
        )

    client = TestClient(app)
    default_response = client.get(
        "/predictions/top-signals", params={"limit": 100, "competition_code": seeded_competition_code}
    )
    assert default_response.status_code == 200
    default_match_ids = {p["match"]["id"] for p in default_response.json()}
    assert current_round_match_id in default_match_ids
    assert next_round_match_id not in default_match_ids

    all_upcoming_response = client.get(
        "/predictions/top-signals",
        params={
            "limit": 100,
            "competition_code": seeded_competition_code,
            "scope": "all_upcoming",
            "days": 10,
        },
    )
    all_upcoming_match_ids = {p["match"]["id"] for p in all_upcoming_response.json()}
    assert next_round_match_id in all_upcoming_match_ids

    next_round_response = client.get(
        "/predictions/top-signals",
        params={"limit": 100, "competition_code": seeded_competition_code, "scope": "next_round"},
    )
    next_round_match_ids = {p["match"]["id"] for p in next_round_response.json()}
    assert next_round_match_id in next_round_match_ids
    assert current_round_match_id not in next_round_match_ids

    # Limpieza: `seeded_competition_code` es una fixture module-scoped
    # compartida por muchos otros tests que dependen del comportamiento
    # SIN jornadas asignadas (fallback por fecha). Anhadir aqui partidos
    # con `matchday` real cambia la "jornada actual" de la competicion
    # para el resto del modulo si no se revierte.
    with session_scope() as db:
        db.query(Prediction).filter(
            Prediction.match_id.in_([current_round_match_id, next_round_match_id])
        ).delete(synchronize_session=False)
        db.query(MatchOdds).filter(
            MatchOdds.match_id.in_([current_round_match_id, next_round_match_id])
        ).delete(synchronize_session=False)
        db.query(Match).filter(Match.id.in_([current_round_match_id, next_round_match_id])).delete(
            synchronize_session=False
        )


def test_top_signals_sort_by_edge_orders_by_edge_not_score(seeded_competition_code):
    """`sort_by=edge` cambia el ORDEN dentro de lo que ya paso el filtro de
    calidad, sin cambiar QUE entra (eso lo sigue decidiendo
    evaluate_quality_gate). Se fuerzan dos edges muy distintos en
    predicciones ya existentes y validas para comprobar que "edge" las
    ordena de mayor a menor, al margen de como las ordenaria el score
    compuesto (probabilidad^2 x edge x confianza x calidad)."""
    with session_scope() as db:
        valid_predictions = (
            db.query(Prediction)
            .filter(Prediction.market_odds.isnot(None))
            .filter(Prediction.market_probability.isnot(None))
            .order_by(Prediction.id)
            .limit(2)
            .all()
        )
        assert len(valid_predictions) == 2
        low_edge_id, high_edge_id = valid_predictions[0].id, valid_predictions[1].id
        valid_predictions[0].edge = 0.02
        valid_predictions[1].edge = 0.35
        db.flush()

    client = TestClient(app)
    response = client.get(
        "/predictions/top-signals",
        params={"limit": 100, "days": 30, "scope": "all_upcoming", "sort_by": "edge"},
    )
    assert response.status_code == 200
    ordered_ids = [p["id"] for p in response.json() if p["id"] in (low_edge_id, high_edge_id)]
    assert ordered_ids == [high_edge_id, low_edge_id]


def test_top_signals_rejects_unknown_sort_by(seeded_competition_code):
    client = TestClient(app)
    response = client.get("/predictions/top-signals", params={"sort_by": "not_a_real_option"})
    assert response.status_code == 422


def test_top_signals_filters_by_fair_odds_range(seeded_competition_code):
    """Pedido explicito de usuario: poder acotar "Mejores señales" por
    rango de CUOTA JUSTA del modelo (p.ej. "solo entre 1 y 2", favoritos
    claros segun el modelo), independiente del filtro MAX_SIGNAL_ODDS
    (que limita la cuota de MERCADO, pensado para evitar tiros muy
    largos)."""
    with session_scope() as db:
        valid_predictions = (
            db.query(Prediction)
            .filter(Prediction.market_odds.isnot(None))
            .filter(Prediction.market_probability.isnot(None))
            .order_by(Prediction.id)
            .limit(2)
            .all()
        )
        assert len(valid_predictions) == 2
        low_fair_id, high_fair_id = valid_predictions[0].id, valid_predictions[1].id
        valid_predictions[0].fair_odds = 1.50
        valid_predictions[1].fair_odds = 4.00
        db.flush()

    client = TestClient(app)
    response = client.get(
        "/predictions/top-signals",
        params={"limit": 100, "days": 30, "scope": "all_upcoming", "min_fair_odds": 1.0, "max_fair_odds": 2.0},
    )
    assert response.status_code == 200
    returned_ids = {p["id"] for p in response.json()}
    assert low_fair_id in returned_ids
    assert high_fair_id not in returned_ids


class _FakeApiFootballProvider:
    """Nunca se probo `ApiFootballOddsProvider` real contra la API (red
    restringida): este test cubre el flujo completo attach -> regenerar
    predicciones para tarjetas/corners con un proveedor simulado."""

    name = "api_football"

    def __init__(self, snapshots: list[FixtureOddsSnapshot]):
        self._snapshots = snapshots

    def is_available(self) -> bool:
        return True

    def fetch_secondary_odds(self, competition_code, season_year, date_from, date_to):
        return self._snapshots


def test_attach_secondary_odds_gives_cards_and_corners_a_real_market(seeded_competition_code):
    """Tarjetas/corners no tienen mercado en The Odds API (ver
    docs/data_sources.md), pero SI pueden tenerlo via API-Football. Tras
    casar cuotas reales para 'cards_total'/'corners_total', las
    predicciones de esos mercados deben dejar de ser "sin mercado"."""
    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        match = (
            db.query(Match)
            .filter(Match.competition_id == comp.id, Match.provider_id == "future-fixture-1")
            .one()
        )
        snapshot = FixtureOddsSnapshot(
            home_team_raw="IntegrationTeam1",
            away_team_raw="IntegrationTeam2",
            commence_time=match.kickoff_utc.replace(tzinfo=dt.timezone.utc),
            odds=[
                RawOddsRecord("bet365", "cards_total", 3.5, "over", 1.90),
                RawOddsRecord("bet365", "cards_total", 3.5, "under", 1.95),
                RawOddsRecord("bet365", "corners_total", 8.5, "over", 1.85),
                RawOddsRecord("bet365", "corners_total", 8.5, "under", 2.00),
            ],
        )
        provider = _FakeApiFootballProvider([snapshot])
        result = attach_secondary_odds_to_scheduled_matches(
            db, provider, seeded_competition_code, 2025, fixture_date, fixture_date
        )
        assert result.matched == 1

    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, seeded_competition_code, fixture_date)
        by_market = {p.market: p for p in predictions}
        cards_over = by_market["cards_over_3_5"]
        assert cards_over.market_odds is not None
        assert cards_over.market_probability is not None
        assert cards_over.edge is not None

        corners_over = by_market["corners_over_8_5"]
        assert corners_over.market_odds is not None
        assert corners_over.market_probability is not None


def test_model_only_endpoint_returns_predictions_without_market(seeded_competition_code):
    """Las predicciones de tarjetas/corners (sin mercado en las fuentes de
    datos usadas) deben poder consultarse por separado, etiquetadas como
    sin mercado, en vez de simplemente desaparecer."""
    client = TestClient(app)
    response = client.get("/predictions/model-only", params={"limit": 100})
    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    for prediction in body:
        assert prediction["has_market"] is False
        assert prediction["market_odds"] is None
    markets = {p["market"] for p in body}
    assert any(m.startswith("cards_") or m.startswith("corners_") for m in markets)


def test_best_predictions_debug_explains_exclusions(seeded_competition_code):
    """Observabilidad (seccion 13): tiene que poder saberse POR QUE una
    prediccion no entro al ranking, no solo que no esta."""
    client = TestClient(app)
    response = client.get("/predictions/best/debug")
    assert response.status_code == 200
    body = response.json()
    assert "included" in body and "excluded" in body
    assert len(body["excluded"]) > 0
    for excluded in body["excluded"]:
        assert excluded["reason"] in {
            "sin_mercado",
            "cuota_invalida",
            "edge_invalido",
            "pocas_casas",
            "calidad_datos_baja",
        }


def test_current_round_endpoint_reports_round_metadata():
    competition_code = "current_round_api_test"
    with session_scope() as db:
        from backend.app.normalization.competitions import COMPETITIONS
        from backend.app.normalization.competitions import resolve_competition_id, resolve_season_id
        from backend.app.db.models.core import Team

        COMPETITIONS.setdefault(competition_code, ("Current Round API Test", "Testland"))
        competition_id = resolve_competition_id(db, competition_code)
        season_id = resolve_season_id(db, competition_id, "2025/26")
        home = Team(canonical_name="RoundApiHome")
        away = Team(canonical_name="RoundApiAway")
        db.add_all([home, away])
        db.flush()
        db.add(
            Match(
                provider="test",
                provider_id="round-api-1",
                competition_id=competition_id,
                season_id=season_id,
                kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=2),
                home_team_id=home.id,
                away_team_id=away.id,
                status="scheduled",
                matchday=7,
            )
        )

    client = TestClient(app)
    response = client.get(f"/competitions/{competition_code}/current-round")
    assert response.status_code == 200
    body = response.json()
    assert body["round"] == 7
    assert body["is_fallback"] is False
    assert len(body["match_ids"]) == 1


def test_current_round_endpoint_404_for_unknown_competition():
    client = TestClient(app)
    response = client.get("/competitions/does_not_exist_at_all/current-round")
    assert response.status_code == 404


def test_predictions_current_round_endpoint_serializes_correctly(seeded_competition_code):
    """Regresion: la primera version de este endpoint no declaraba
    `response_model`, asi que FastAPI intentaba serializar el objeto ORM
    `Match` anidado dentro de cada prediccion tal cual (no via Pydantic),
    lo que rompia con `PydanticSerializationError: Unable to serialize
    unknown type` en cuanto habia al menos un partido. Sin este test,
    ninguno de los tests anteriores lo detectaba porque llaman a
    `get_current_round`/`generate_predictions_for_competition` directamente
    en Python, nunca a traves de la capa HTTP real."""
    client = TestClient(app)
    response = client.get(f"/predictions/current-round?competition_code={seeded_competition_code}")
    assert response.status_code == 200
    body = response.json()
    assert "round" in body and "predictions" in body
    assert len(body["predictions"]) > 0
    for prediction in body["predictions"]:
        assert isinstance(prediction["match"], dict)
        assert "home_team" in prediction["match"]
