from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, OddsIngestionRun, Season
from backend.app.db.models.matches import Match, MatchOdds, MatchOddsHistory
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
                    home_corners=int(r.home_corners),
                    away_corners=int(r.away_corners),
                    odds=[
                        RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.9),
                        # 1.85, no 1.95 (seccion "cambio de filosofia del
                        # ranking", v3): /predictions/top-signals ahora
                        # exige cuota dentro de [1.35, 1.90] -- 1.95
                        # quedaria excluida por el nuevo filtro, rompiendo
                        # todos los tests de este modulo que asumen que
                        # "las dos predicciones con mercado real del
                        # partido sembrado" son over_2_5 Y under_2_5.
                        RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.85),
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
        assert len(predictions) == TOTAL_MVP_MARKETS  # goles + corners

    # Seccion "cambio de filosofia del ranking" (v3, 2026-11):
    # /predictions/top-signals ahora exige EV estimado Y conservador
    # POSITIVOS y fiabilidad historica real -- ver `_force_positive_ev`,
    # llamado mas abajo en `test_attach_odds_matches_by_team_and_
    # refreshes_predictions` (justo despues de la UNICA vez que este
    # partido futuro recibe cuota real: antes de eso `market_odds` es
    # `None`, nada que ajustar todavia).
    #
    # Fiabilidad historica real para over_2_5/under_2_5 de esta
    # competicion: se liquida un lote de partidos FUTUROS-que-se-marcan-
    # jugados (mismo patron que test_settlement.py), suficiente para
    # superar MIN_SETTLED_FOR_TRACK_RECORD (30 en evaluation_service.py).
    _seed_market_track_record(competition_code)

    return competition_code


def _force_positive_ev(
    competition_code: str, fixture_date: dt.date, home_team_id: int, away_team_id: int
) -> None:
    """Fuerza `calibrated_probability`/`uncertainty_low`/`uncertainty_high`
    en las predicciones de goles de UN partido ya generado, para que su
    EV (estimado y conservador, ver prediction/ranking.py) sea
    POSITIVO con la cuota real que ya tiene -- ver docstring de la
    llamada en `seeded_competition_code`."""
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        match = (
            db.query(Match)
            .filter(
                Match.competition_id == comp.id,
                Match.home_team_id == home_team_id,
                Match.away_team_id == away_team_id,
                Match.kickoff_utc >= dt.datetime.combine(fixture_date, dt.time.min),
                Match.kickoff_utc < dt.datetime.combine(fixture_date, dt.time.min) + dt.timedelta(days=1),
            )
            .one()
        )
        for market, calibrated, uncertainty_low in (
            ("over_2_5", 0.65, 0.60),
            ("under_2_5", 0.60, 0.58),
        ):
            db.query(Prediction).filter(Prediction.match_id == match.id, Prediction.market == market).update(
                {
                    "calibrated_probability": calibrated,
                    "uncertainty_low": uncertainty_low,
                    "uncertainty_high": min(0.95, calibrated + 0.15),
                },
                synchronize_session=False,
            )


def _set_safe_calibration(prediction: Prediction) -> None:
    """Igual proposito que `_force_positive_ev`, pero mutando un objeto
    `Prediction` YA CARGADO (el llamador hace `db.flush()`) en vez de
    volver a buscar el partido por equipos/fecha -- para los tests que ya
    tienen la fila en mano (`valid_predictions[i]`) y solo necesitan
    garantizar que su EV, con la cuota real que YA tiene, sea positivo
    (banda de cuotas -- seccion "cambio de filosofia del ranking", v3).
    Calcula los valores a partir de `market_odds` de la propia fila, para
    funcionar igual sea cual sea el mercado/cuota concreto que le toque."""
    implied = 1.0 / prediction.market_odds
    calibrated = min(0.95, implied + 0.15)
    prediction.calibrated_probability = calibrated
    prediction.uncertainty_low = min(0.95, implied + 0.08)
    prediction.uncertainty_high = min(0.98, calibrated + 0.15)


def _two_valid_predictions_from_distinct_matches(db) -> list[Prediction]:
    """Las 2 primeras predicciones (por id) con mercado real, una por
    PARTIDO distinto. Nunca dos mercados (p.ej. over_2_5/under_2_5) del
    mismo partido: `/predictions/top-signals` aplica `select_one_per_match`
    por defecto (v3, "Mejores oportunidades"), asi que dos candidatas del
    mismo partido jamas podrian aparecer juntas en la respuesta -- un test
    que las fuerce a las dos estaria comprobando un escenario imposible en
    produccion, sea cual sea el filtro que de verdad quiera ejercitar."""
    candidates = (
        db.query(Prediction)
        .filter(Prediction.market_odds.isnot(None))
        .filter(Prediction.market_probability.isnot(None))
        .order_by(Prediction.id)
        .all()
    )
    picked: list[Prediction] = []
    seen_match_ids: set[int] = set()
    for prediction in candidates:
        if prediction.match_id in seen_match_ids:
            continue
        picked.append(prediction)
        seen_match_ids.add(prediction.match_id)
        if len(picked) == 2:
            break
    assert len(picked) == 2
    return picked


def _seed_market_track_record(competition_code: str) -> None:
    """Liquida 35 partidos FUTUROS-que-se-marcan-jugados para dejar
    fiabilidad historica real (`market_track_records`) en over_2_5/
    under_2_5 de `competition_code` -- sin esto, v3 ("Mejores
    oportunidades") excluiria CUALQUIER prediccion de esta competicion
    por `fiabilidad_historica_insuficiente`, sea cual sea su cuota/EV.

    Deliberadamente SIN cuota real (nunca se les llama
    `attach_odds_to_scheduled_matches`/no se les crea `MatchOdds`):
    `market_track_records()` solo necesita (outcome, model_probability),
    nunca cuota -- anhadir cuota aqui colaria estas 35*15=525
    predicciones (con ids mas bajos que las de cualquier test posterior)
    en cualquier query de este modulo que busque "las primeras N
    predicciones con mercado real" para el partido futuro sembrado,
    rompiendo esos tests por una razon ajena a lo que de verdad hace
    falta sembrar aqui."""
    from backend.app.services.evaluation_service import settle_finished_predictions

    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        season = db.query(Season).filter_by(competition_id=comp.id).order_by(Season.id.desc()).first()
        base_time = dt.datetime.utcnow() + dt.timedelta(days=300)  # bien lejos de cualquier ventana real de /top-signals
        match_ids: list[int] = []
        for i in range(35):
            match = Match(
                provider="integration_test_track_record",
                provider_id=f"track-record-fixture-{i}",
                competition_id=comp.id,
                season_id=season.id,
                kickoff_utc=base_time + dt.timedelta(days=i),
                home_team_id=1,
                away_team_id=2,
                status="scheduled",
            )
            db.add(match)
            db.flush()
            match_ids.append(match.id)

    for i, match_id in enumerate(match_ids):
        with session_scope() as db:
            kickoff_date = db.get(Match, match_id).kickoff_utc.date()
        with session_scope() as db:
            generate_predictions_for_competition(db, competition_code, kickoff_date)
        # Resultado alternante (nunca el mismo marcador 35 veces, para no
        # fingir una certeza perfecta): el VALOR exacto del track record
        # no importa para el filtro duro de v3 (solo exige que EXISTA,
        # `market_track_record is not None`), solo que haya liquidaciones
        # reales de las que calcularlo.
        home_goals, away_goals = (2, 1) if i % 2 == 0 else (0, 1)
        with session_scope() as db:
            db.query(Match).filter_by(id=match_id).update(
                {"home_goals": home_goals, "away_goals": away_goals, "status": "finished"},
                synchronize_session=False,
            )

    with session_scope() as db:
        settle_finished_predictions(db, competition_code=competition_code)


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_data_sources_status_never_leaks_a_real_key():
    """Pagina "Configuracion" del frontend: solo booleanos de que fuentes
    estan activas, nunca el valor de una API key real."""
    client = TestClient(app)
    response = client.get("/config/data-sources")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "football_data_co_uk_enabled",
        "api_football_enabled",
        "odds_api_enabled",
        "odds_api_fetch_additional_markets",
    }
    assert all(isinstance(v, bool) for v in body.values())


def test_competitions_endpoint_lists_seeded_competition(seeded_competition_code):
    client = TestClient(app)
    response = client.get("/competitions")
    assert response.status_code == 200
    codes = [c["code"] for c in response.json()]
    assert seeded_competition_code in codes


def test_predictions_summary_endpoint_returns_real_counts(seeded_competition_code):
    """Resumen de jornada del dashboard: nunca debe fallar aunque no haya
    predicciones con mercado real (opportunities_count=0 es una respuesta
    valida, no un error) -- y nunca debe contar una señal que no pase el
    mismo filtro de calidad que el resto de la app."""
    client = TestClient(app)
    response = client.get("/predictions/summary")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "date",
        "matches_pending",
        "competitions_count",
        "opportunities_count",
        "high_quality_count",
    }
    assert body["competitions_count"] >= 1  # al menos la liga sembrada por el fixture
    assert body["opportunities_count"] >= 0
    assert body["high_quality_count"] <= body["opportunities_count"]


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
        # Pedido de rediseño (comparativa modelo-vs-mercado en el detalle de
        # partido): calibrated_probability debe venir expuesto en la
        # respuesta (antes solo se usaba internamente para el filtro de
        # calidad), siempre como numero en [0,1] o None, nunca inventado.
        assert "calibrated_probability" in prediction
        if prediction["calibrated_probability"] is not None:
            assert 0.0 <= prediction["calibrated_probability"] <= 1.0


def test_match_predictions_endpoint(seeded_competition_code):
    client = TestClient(app)
    today = client.get("/predictions/today").json()
    match_id = today[0]["match"]["id"]

    response = client.get(f"/matches/{match_id}/predictions")
    assert response.status_code == 200
    assert len(response.json()) == TOTAL_MVP_MARKETS


def test_match_form_endpoint_returns_recent_form_and_h2h(seeded_competition_code):
    """Pedido real del usuario: poder ver, al pinchar un partido, como
    viene cada equipo (forma reciente, especificamente en casa/fuera) y el
    cara a cara historico -- solo con partidos REALMENTE 'finished'
    anteriores a este, nunca inventados ni posteriores (fuga temporal)."""
    client = TestClient(app)
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        # Cualquier partido finalizado con partidos previos de ambos
        # equipos (la temporada sintetica tiene 3 temporadas de 8 equipos,
        # asi que hay de sobra) sirve como caso representativo.
        candidate = (
            db.query(Match)
            .filter(Match.competition_id == comp.id, Match.status == "finished")
            .order_by(Match.kickoff_utc.desc())
            .first()
        )
        match_id = candidate.id
        home_team_id = candidate.home_team_id
        away_team_id = candidate.away_team_id
        kickoff = candidate.kickoff_utc

    response = client.get(f"/matches/{match_id}/form", params={"limit": 5})
    assert response.status_code == 200
    body = response.json()

    assert body["home_team"]["team"]["id"] == home_team_id
    assert body["away_team"]["team"]["id"] == away_team_id
    assert len(body["home_team"]["recent"]) <= 5
    assert len(body["away_team"]["recent"]) <= 5

    for entry in (
        body["home_team"]["recent"]
        + body["home_team"]["recent_at_venue"]
        + body["away_team"]["recent"]
        + body["away_team"]["recent_at_venue"]
        + body["head_to_head"]
    ):
        assert entry["match_id"] != match_id
        assert entry["kickoff_utc"] < kickoff.isoformat()
        assert entry["result"] in ("W", "D", "L")

    for entry in body["home_team"]["recent_at_venue"]:
        assert entry["venue"] == "home"
    for entry in body["away_team"]["recent_at_venue"]:
        assert entry["venue"] == "away"


def test_match_kickoff_utc_is_serialized_with_an_explicit_utc_offset():
    """Bug real reportado por el usuario (todas las horas 1h antes de la
    real): `kickoff_utc` se guarda naive pero SIEMPRE en UTC por
    convencion (ver services/data_service.py) -- Pydantic serializaba un
    datetime naive sin ningun sufijo de zona ("...T19:30:00"), y
    `new Date(...)` en JavaScript interpreta eso como hora LOCAL del
    navegador, no UTC. Alguien en UTC+1 veia cada partido 1 hora antes de
    su hora real. El JSON debe llevar SIEMPRE un offset explicito."""
    client = TestClient(app)
    competition_code = "kickoff_utc_offset_test"
    with session_scope() as db:
        comp = Competition(code=competition_code, name="Offset Test League", country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2026/27")
        db.add(season)
        db.flush()
        from backend.app.db.models.core import Team

        home = Team(canonical_name="Offset Test Home")
        away = Team(canonical_name="Offset Test Away")
        db.add_all([home, away])
        db.flush()
        match = Match(
            provider="offset_test_provider",
            provider_id="offset-test-1",
            competition_id=comp.id,
            season_id=season.id,
            # Naive a proposito, igual que lo que de verdad guarda la
            # ingesta real -- el bug esta precisamente en que nunca lleva
            # tzinfo en la BD.
            kickoff_utc=dt.datetime(2026, 11, 20, 19, 30, 0),
            home_team_id=home.id,
            away_team_id=away.id,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        match_id = match.id

    response = client.get("/matches", params={"competition_code": competition_code})
    assert response.status_code == 200
    body = response.json()
    serialized = next(m["kickoff_utc"] for m in body if m["id"] == match_id)
    assert serialized.endswith("+00:00") or serialized.endswith("Z")
    assert serialized.startswith("2026-11-20T19:30:00")


def test_list_matches_finished_filter_returns_most_recent_when_truncated_by_limit():
    """Bug real reportado por el usuario (pagina "Partidos", filtro
    "Jugados"): con mas partidos 'finished' desde el inicio de temporada
    que `limit`, ordenar SIEMPRE ascendente antes de recortar devolvia
    los partidos MAS ANTIGUOS (agosto/septiembre) en vez de los mas
    recientes -- "Jugados" parecia atascado meses atras aunque ya se
    hubieran jugado muchas mas jornadas. Para 'scheduled' (o sin filtro
    de estado) el orden sigue siendo ascendente (lo mas proximo primero),
    que es lo correcto ahi."""
    client = TestClient(app)
    competition_code = "list_matches_order_test"
    with session_scope() as db:
        comp = Competition(code=competition_code, name="Order Test League", country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2026/27")
        db.add(season)
        db.flush()
        from backend.app.db.models.core import Team

        home = Team(canonical_name="Order Test Home")
        away = Team(canonical_name="Order Test Away")
        db.add_all([home, away])
        db.flush()

        now = dt.datetime.utcnow()
        oldest_id = None
        newest_id = None
        for days_ago in (90, 45, 1):
            match = Match(
                provider="order_test_provider",
                provider_id=f"order-test-{days_ago}",
                competition_id=comp.id,
                season_id=season.id,
                kickoff_utc=now - dt.timedelta(days=days_ago),
                home_team_id=home.id,
                away_team_id=away.id,
                status="finished",
                home_goals=1,
                away_goals=0,
            )
            db.add(match)
            db.flush()
            if days_ago == 90:
                oldest_id = match.id
            if days_ago == 1:
                newest_id = match.id

    response = client.get(
        "/matches",
        params={"competition_code": competition_code, "status": "finished", "limit": 2},
    )
    assert response.status_code == 200
    body = response.json()
    returned_ids = [m["id"] for m in body]
    assert len(returned_ids) == 2
    assert newest_id in returned_ids
    assert oldest_id not in returned_ids


def test_match_form_endpoint_404_for_unknown_match():
    client = TestClient(app)
    response = client.get("/matches/999999999/form")
    assert response.status_code == 404


def test_h2h_endpoint_returns_recent_form_and_history_between_two_teams(seeded_competition_code):
    """Pedido real del usuario (pagina principal): poder elegir dos
    equipos CUALQUIERA (no un partido concreto) y ver su cara a cara +
    forma reciente de cada uno."""
    client = TestClient(app)
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        # La liga sintetica tiene 8 equipos en 3 temporadas round-robin:
        # cualquier pareja de equipos con un partido finalizado real sirve.
        any_finished = (
            db.query(Match).filter(Match.competition_id == comp.id, Match.status == "finished").first()
        )
        team_a_id, team_b_id = any_finished.home_team_id, any_finished.away_team_id

    response = client.get("/matches/h2h", params={"team_a_id": team_a_id, "team_b_id": team_b_id})
    assert response.status_code == 200
    body = response.json()
    assert body["team_a"]["team"]["id"] == team_a_id
    assert body["team_b"]["team"]["id"] == team_b_id
    assert len(body["head_to_head"]) >= 1
    for entry in body["head_to_head"]:
        assert entry["result"] in ("W", "D", "L")


def test_h2h_endpoint_404_for_unknown_team():
    client = TestClient(app)
    response = client.get("/matches/h2h", params={"team_a_id": 999999999, "team_b_id": 1})
    assert response.status_code == 404


def test_h2h_endpoint_surfaces_real_prediction_for_a_genuinely_scheduled_match(seeded_competition_code):
    """La parte critica de "nunca inventar": `scheduled_match` solo trae
    una prediccion cuando existe un partido REAL ya programado entre esos
    dos equipos Y ya se generaron predicciones reales para el."""
    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        match = db.query(Match).filter_by(competition_id=comp.id, provider_id="future-fixture-1").one()
        match_id = match.id
        team_a_id, team_b_id = match.home_team_id, match.away_team_id
        home_name, away_name = match.home_team.canonical_name, match.away_team.canonical_name
        snapshot = FixtureOddsSnapshot(
            home_team_raw=home_name,
            away_team_raw=away_name,
            commence_time=match.kickoff_utc.replace(tzinfo=dt.timezone.utc),
            odds=[
                RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90),
                RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
            ],
        )
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), seeded_competition_code)

    with session_scope() as db:
        generate_predictions_for_competition(db, seeded_competition_code, fixture_date)

    client = TestClient(app)
    response = client.get("/matches/h2h", params={"team_a_id": team_a_id, "team_b_id": team_b_id})
    assert response.status_code == 200
    body = response.json()
    assert body["scheduled_match"] is not None
    assert body["scheduled_match"]["match_id"] == match_id
    assert body["scheduled_match"]["best_prediction"] is not None


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
                # 1.85, no 1.95 (seccion "cambio de filosofia del
                # ranking", v3): fuera de [1.35, 1.90].
                RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.85),
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

    # v3 ("Mejores oportunidades") exige EV estimado/conservador
    # positivos -- esta regeneracion BORRA y recrea las predicciones del
    # partido (ver generate_predictions_for_competition), asi que
    # cualquier ajuste anterior de `seeded_competition_code` quedaria
    # descartado; se fuerza aqui, justo despues de la regeneracion real,
    # para que el resto de tests de este modulo (que dependen de que
    # este partido SIGA pasando el filtro duro) vean un EV garantizado.
    _force_positive_ev(seeded_competition_code, fixture_date, home_team_id=1, away_team_id=2)


def test_prediction_detail_endpoint_shows_bookmaker_breakdown_and_explanation(seeded_competition_code):
    """Seccion 16/17 del pedido de revision integral: detalle completo de
    una senhal con desglose bookmaker-por-bookmaker y una explicacion en
    lenguaje llano, sin lenguaje de certeza ("apuesta segura", "100%",
    "ganadora")."""
    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        match = db.query(Match).filter(Match.competition_id == comp.id, Match.status == "scheduled").one()
        snapshot = FixtureOddsSnapshot(
            home_team_raw="IntegrationTeam1",
            away_team_raw="IntegrationTeam2",
            commence_time=match.kickoff_utc.replace(tzinfo=dt.timezone.utc),
            odds=[
                RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90),
                RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
                RawOddsRecord("pinnacle", "over_under_goals", 2.5, "over", 1.85),
                RawOddsRecord("pinnacle", "over_under_goals", 2.5, "under", 2.00),
            ],
        )
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), seeded_competition_code)

    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, seeded_competition_code, fixture_date)
        over_2_5_id = next(p.id for p in predictions if p.market == "over_2_5")

    client = TestClient(app)
    response = client.get(f"/predictions/{over_2_5_id}/detail")
    assert response.status_code == 200
    body = response.json()

    bookmakers = {row["bookmaker"] for row in body["bookmaker_odds"]}
    # Nombre canonico (normalization/bookmakers.py), no el raw "bet365"/
    # "pinnacle" tal cual vino de la fuente de cuotas.
    assert bookmakers == {"Bet365", "Pinnacle"}
    for row in body["bookmaker_odds"]:
        assert row["diff_from_consensus"] is not None

    assert len(body["explanation_summary"]) > 0
    forbidden = ["apuesta segura", "bet segura", "ganadora", "100%", "seguro"]
    full_text = " ".join(body["explanation_summary"]).lower()
    for word in forbidden:
        assert word not in full_text


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
    debe devolver una prediccion sin mercado, sea cual sea su probabilidad.

    `edge >= 0` ya NO es una garantia de v3 (cambio de filosofia del
    ranking): v3 no exige edge minimo a proposito ("informacion
    secundaria", nunca sustituye a EV/probabilidad/fiabilidad) -- las
    garantias reales ahora son EV estimado Y conservador POSITIVOS."""
    # Varios tests de este modulo regeneran las predicciones del partido
    # futuro sembrado (cada uno con su propia cuota) -- se fuerza aqui de
    # nuevo, justo antes de la llamada real, para no depender de que la
    # ultima regeneracion anterior en el orden del archivo haya sido la
    # de `test_attach_odds_matches_by_team_and_refreshes_predictions`.
    _force_positive_ev(
        seeded_competition_code,
        (dt.datetime.utcnow() + dt.timedelta(days=1)).date(),
        home_team_id=1,
        away_team_id=2,
    )
    client = TestClient(app)
    response = client.get("/predictions/top-signals", params={"limit": 100})
    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    for prediction in body:
        assert prediction["has_market"] is True
        assert prediction["market_odds"] is not None
        assert prediction["market_probability"] is not None
        assert prediction["ev_calibrated"] is not None and prediction["ev_calibrated"] > 0
        assert prediction["ev_conservative"] is not None and prediction["ev_conservative"] > 0
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
                RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.85),
            ],
        )
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), seeded_competition_code)

    with session_scope() as db:
        generate_predictions_for_competition(db, seeded_competition_code, far_kickoff.date())
    # v3 ("Mejores oportunidades") exige EV positivo -- ver docstring de
    # `_force_positive_ev` en `seeded_competition_code`.
    _force_positive_ev(seeded_competition_code, far_kickoff.date(), home_team_id=3, away_team_id=4)

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
                    RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.85),
                ],
            ),
            FixtureOddsSnapshot(
                home_team_raw="IntegrationTeam7",
                away_team_raw="IntegrationTeam8",
                commence_time=next_round_kickoff.replace(tzinfo=dt.timezone.utc),
                odds=[
                    RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90),
                    RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.85),
                ],
            ),
        ]
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider(snapshots), seeded_competition_code)

    with session_scope() as db:
        generate_predictions_for_competition(db, seeded_competition_code, current_round_kickoff.date())
        generate_predictions_for_competition(db, seeded_competition_code, next_round_kickoff.date())
    # v3 ("Mejores oportunidades") exige EV positivo -- ver docstring de
    # `_force_positive_ev` en `seeded_competition_code`.
    _force_positive_ev(seeded_competition_code, current_round_kickoff.date(), home_team_id=5, away_team_id=6)
    _force_positive_ev(seeded_competition_code, next_round_kickoff.date(), home_team_id=7, away_team_id=8)

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
        # El borrado masivo (`Query.delete()`) nunca dispara cascades de
        # ORM (`cascade="all, delete-orphan"` solo aplica a
        # `session.delete(objeto)`) -- MatchOddsHistory (append-only,
        # ver market/clv.py) hay que limpiarla aqui tambien a mano, o
        # quedaria huerfana con un match_id que SQLite puede reciclar para
        # un partido nuevo y no relacionado (bug real, ver test_clv.py).
        db.query(MatchOddsHistory).filter(
            MatchOddsHistory.match_id.in_([current_round_match_id, next_round_match_id])
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
        # Distintos PARTIDOS a proposito (ver `_two_valid_predictions_from_distinct_matches`):
        # v3 aplica `select_one_per_match` por defecto en este endpoint, asi
        # que dos mercados (over/under) del MISMO partido nunca podrian
        # aparecer juntos en la respuesta, sea cual sea su edge.
        valid_predictions = _two_valid_predictions_from_distinct_matches(db)
        low_edge_id, high_edge_id = valid_predictions[0].id, valid_predictions[1].id
        valid_predictions[0].edge = 0.02
        valid_predictions[1].edge = 0.35
        # v3 ("Mejores oportunidades") exige EV positivo para entrar --
        # ver `_set_safe_calibration`.
        _set_safe_calibration(valid_predictions[0])
        _set_safe_calibration(valid_predictions[1])
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
        valid_predictions = _two_valid_predictions_from_distinct_matches(db)
        low_fair_id, high_fair_id = valid_predictions[0].id, valid_predictions[1].id
        valid_predictions[0].fair_odds = 1.50
        valid_predictions[1].fair_odds = 4.00
        # v3 ("Mejores oportunidades") exige EV positivo para entrar --
        # ver `_set_safe_calibration`.
        _set_safe_calibration(valid_predictions[0])
        _set_safe_calibration(valid_predictions[1])
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


def test_top_signals_filters_by_min_edge_min_bookmakers_and_quality(seeded_competition_code):
    """Filtros adicionales pedidos explicitamente (seccion 14): edge
    minimo (en PUNTOS PORCENTUALES, para un slider), numero minimo de
    casas, y calidad de mercado -- todos AJUSTES FINOS sobre lo que ya
    paso el filtro duro, nunca lo sustituyen."""
    with session_scope() as db:
        valid_predictions = _two_valid_predictions_from_distinct_matches(db)
        low_edge_id, high_edge_id = valid_predictions[0].id, valid_predictions[1].id
        valid_predictions[0].edge = 0.02  # 2pp
        valid_predictions[0].bookmakers_used = 1
        valid_predictions[0].expected_value = 0.01  # EV +1%
        valid_predictions[1].edge = 0.12  # 12pp
        valid_predictions[1].bookmakers_used = 12
        valid_predictions[1].expected_value = 0.20  # EV +20%
        # v3 ("Mejores oportunidades") exige EV (calibrado/conservador,
        # distinto del `expected_value` legacy de arriba) positivo para
        # entrar -- ver `_set_safe_calibration`.
        _set_safe_calibration(valid_predictions[0])
        _set_safe_calibration(valid_predictions[1])
        db.flush()

    client = TestClient(app)

    min_edge_response = client.get(
        "/predictions/top-signals",
        params={"limit": 100, "days": 30, "scope": "all_upcoming", "min_edge": 5},
    )
    min_edge_ids = {p["id"] for p in min_edge_response.json()}
    assert low_edge_id not in min_edge_ids
    assert high_edge_id in min_edge_ids

    min_bookmakers_response = client.get(
        "/predictions/top-signals",
        params={"limit": 100, "days": 30, "scope": "all_upcoming", "min_bookmakers": 5},
    )
    min_bookmakers_ids = {p["id"] for p in min_bookmakers_response.json()}
    assert low_edge_id not in min_bookmakers_ids
    assert high_edge_id in min_bookmakers_ids

    quality_response = client.get(
        "/predictions/top-signals",
        params={"limit": 100, "days": 30, "scope": "all_upcoming", "quality": "LOW"},
    )
    quality_ids = {p["id"] for p in quality_response.json()}
    assert low_edge_id in quality_ids
    assert high_edge_id not in quality_ids

    min_ev_response = client.get(
        "/predictions/top-signals",
        params={"limit": 100, "days": 30, "scope": "all_upcoming", "min_ev": 0.10},
    )
    min_ev_ids = {p["id"] for p in min_ev_response.json()}
    assert low_edge_id not in min_ev_ids
    assert high_edge_id in min_ev_ids


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


def test_attach_secondary_odds_gives_corners_a_real_market(seeded_competition_code):
    """Corners no tiene mercado en The Odds API (ver docs/data_sources.md),
    pero SI puede tenerlo via API-Football. Tras casar cuotas reales para
    'corners_total', las predicciones de ese mercado deben dejar de ser
    "sin mercado". Tarjetas se quito del todo (pedido real del usuario,
    ver secondary_markets.py): aunque llegara una cuota de 'cards_total'
    (p.ej. de una fuente vieja), no existe ningun mercado 'cards_over_*'
    al que asociarla -- se guarda en MatchOdds pero nunca genera una
    Prediction."""
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
        assert not any(m.startswith("cards_") for m in by_market)

        corners_over = by_market["corners_over_8_5"]
        assert corners_over.market_odds is not None
        assert corners_over.market_probability is not None


def test_model_only_endpoint_returns_predictions_without_market(seeded_competition_code):
    """Las predicciones de corners (sin mercado en las fuentes de datos
    usadas para el resto de partidos) deben poder consultarse por
    separado, etiquetadas como sin mercado, en vez de simplemente
    desaparecer. Tarjetas se quito del todo, nunca aparece aqui."""
    client = TestClient(app)
    response = client.get("/predictions/model-only", params={"limit": 100})
    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    for prediction in body:
        assert prediction["has_market"] is False
        assert prediction["market_odds"] is None
    markets = {p["market"] for p in body}
    assert any(m.startswith("corners_") for m in markets)
    assert not any(m.startswith("cards_") for m in markets)


def test_best_predictions_debug_explains_exclusions(seeded_competition_code):
    """Observabilidad (seccion 13): tiene que poder saberse POR QUE una
    prediccion no entro al ranking, no solo que no esta. Desde el cambio
    de filosofia del ranking (v3, "Mejores oportunidades"), este debug
    usa el vocabulario de motivos de `evaluate_quality_gate_v3` -- banda
    de cuotas, EV estimado/conservador, fiabilidad historica -- no el de
    v1/v2 (que ya no decide que entra aqui)."""
    client = TestClient(app)
    response = client.get("/predictions/best/debug")
    assert response.status_code == 200
    body = response.json()
    assert "included" in body and "excluded" in body
    assert len(body["excluded"]) > 0
    for excluded in body["excluded"]:
        assert excluded["reason"] in {
            "sin_mercado",
            "cuota_fuera_de_banda",
            "pocas_casas",
            "calidad_datos_baja",
            "cuota_desactualizada",
            "ev_estimado_no_positivo",
            "ev_conservador_no_positivo",
            "fiabilidad_historica_insuficiente",
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


def test_predictions_current_round_marks_the_best_prediction_per_match(seeded_competition_code):
    """Bug real reportado por el usuario (la portada mostraba "Sin señal
    clara todavía" en TODOS los partidos, incluso con cuota real): esta
    ruta nunca marcaba `is_best_prediction` (quedaba `False` por defecto
    para todas, via el default del schema) aunque el partido tuviera una
    señal fuerte de verdad con mercado real -- a diferencia de
    `/matches/{id}/predictions`, que si usa `best_prediction_per_match`
    (prediction/anomaly.py) para marcarla."""
    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        # provider_id exacto (no solo "scheduled"): el fixture comparte
        # modulo con otros tests que pueden anadir sus propios partidos
        # "scheduled" a esta misma competicion -- un filtro generico seria
        # ambiguo (MultipleResultsFound) segun el orden de ejecucion.
        match = db.query(Match).filter_by(competition_id=comp.id, provider_id="future-fixture-1").one()
        # Lee los nombres REALES del partido en vez de asumir
        # "IntegrationTeam1"/"IntegrationTeam2" por posicion: los ids de
        # equipo son globales (no por competicion), asi que si otro test
        # ya reclamo los ids 1/2 para OTRA competicion antes de que corra
        # este fixture, el partido de seeded_competition_code puede acabar
        # apuntando a equipos con otro nombre -- un nombre hardcodeado que
        # no case no engancharia la cuota a este partido en absoluto.
        home_name = match.home_team.canonical_name
        away_name = match.away_team.canonical_name
        snapshot = FixtureOddsSnapshot(
            home_team_raw=home_name,
            away_team_raw=away_name,
            commence_time=match.kickoff_utc.replace(tzinfo=dt.timezone.utc),
            odds=[
                RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.90),
                RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
            ],
        )
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), seeded_competition_code)

    with session_scope() as db:
        generate_predictions_for_competition(db, seeded_competition_code, fixture_date)

    client = TestClient(app)
    response = client.get(f"/predictions/current-round?competition_code={seeded_competition_code}")
    assert response.status_code == 200
    predictions = response.json()["predictions"]
    assert any(p["is_best_prediction"] for p in predictions)


def test_predictions_current_round_falls_back_to_next_round_when_current_round_is_empty(
    seeded_competition_code,
):
    """Pedido real del usuario: la portada se quedaba vacia cuando la
    jornada "actual" (el matchday mas bajo con algun partido 'scheduled',
    ver round_service.py) ya no tiene NINGUN partido con kickoff futuro --
    p.ej. un partido cuyo pitido ya sono pero el scheduler todavia no lo
    marco 'finished'. En ese caso el endpoint debe caer a la jornada
    SIGUIENTE (que si tiene partidos futuros) en vez de devolver vacio, y
    marcarlo explicitamente via `is_next_round` (igual que `is_fallback`
    para el caso sin matchday)."""
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        season = db.query(Match).filter_by(competition_id=comp.id).first().season_id

        # Jornada "actual" (matchday mas bajo): su unico partido ya paso de
        # hora pero sigue 'scheduled' -- round_matches (kickoff >= now)
        # queda vacio para esta jornada.
        stale_current_match = Match(
            provider="integration_test_manual",
            provider_id="stale-current-round-fixture",
            competition_id=comp.id,
            season_id=season,
            kickoff_utc=dt.datetime.utcnow() - dt.timedelta(hours=3),
            home_team_id=5,
            away_team_id=6,
            status="scheduled",
            matchday=20,
        )
        next_round_match = Match(
            provider="integration_test_manual",
            provider_id="real-next-round-fixture",
            competition_id=comp.id,
            season_id=season,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=2),
            home_team_id=7,
            away_team_id=8,
            status="scheduled",
            matchday=21,
        )
        db.add_all([stale_current_match, next_round_match])
        db.flush()
        next_round_kickoff = next_round_match.kickoff_utc
        next_round_match_id = next_round_match.id

        snapshots = [
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
        generate_predictions_for_competition(db, seeded_competition_code, next_round_kickoff.date())

    client = TestClient(app)
    response = client.get(f"/predictions/current-round?competition_code={seeded_competition_code}")
    assert response.status_code == 200
    body = response.json()
    assert body["round"]["round"] == 21
    assert body["round"]["is_next_round"] is True
    assert len(body["predictions"]) > 0
    assert all(p["match"]["id"] == next_round_match_id for p in body["predictions"])


def test_ranking_config_band_is_actually_applied_to_top_signals(seeded_competition_code):
    """Seccion 7 del pedido ("cambio de filosofia del ranking"): el filtro
    de cuotas de 'Mejores oportunidades' debe ser configurable en
    runtime via `/config/ranking`, y ese valor guardado debe ser el que
    de verdad usa `/predictions/top-signals` -- guardar una preferencia
    que el endpoint real ignora (y sigue usando el default de `Settings`)
    seria la misma preferencia "fantasma" que esta funcionalidad existe
    para evitar.

    Partido PROPIO (equipos 9/10, fecha +20 dias) en vez de reutilizar
    "future-fixture-1": ese partido comparte modulo con ~30 tests que lo
    regeneran repetidamente con sus propias cuotas (ver `_force_positive_ev`
    y sus docstrings) -- depender de su estado al final del archivo seria
    tan fragil como los bugs ya corregidos arriba en este mismo modulo."""
    client = TestClient(app)

    default_config = client.get("/config/ranking")
    assert default_config.status_code == 200
    default_body = default_config.json()
    assert default_body["min_opportunity_odds"] == pytest.approx(1.35)
    assert default_body["max_opportunity_odds"] == pytest.approx(1.90)

    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=20)).date()
    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=seeded_competition_code).one()
        # Reutiliza un par de equipos de un partido YA FINALIZADO de esta
        # misma competicion (en vez de un id de equipo hardcodeado): esos
        # son los unicos equipos con historico real suficiente para que
        # el modelo genere features (y por tanto mercado) -- un id
        # arbitrario (p.ej. 9/10) apunta a equipos distintos segun que
        # otros modulos de test hayan corrido antes en la misma sesion de
        # pytest (los ids de equipo son globales, no por competicion).
        finished_match = (
            db.query(Match).filter_by(competition_id=comp.id, status="finished").first()
        )
        home_team_id = finished_match.home_team_id
        away_team_id = finished_match.away_team_id
        home_name = finished_match.home_team.canonical_name
        away_name = finished_match.away_team.canonical_name
        season = finished_match.season_id
        match = Match(
            provider="integration_test_manual",
            provider_id="ranking-config-band-fixture",
            competition_id=comp.id,
            season_id=season,
            kickoff_utc=dt.datetime.combine(fixture_date, dt.time(hour=20)),
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        snapshot = FixtureOddsSnapshot(
            home_team_raw=home_name,
            away_team_raw=away_name,
            commence_time=match.kickoff_utc.replace(tzinfo=dt.timezone.utc),
            odds=[
                RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.70),
                RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.75),
            ],
        )
        attach_odds_to_scheduled_matches(db, _FakeOddsProvider([snapshot]), seeded_competition_code)

    with session_scope() as db:
        generate_predictions_for_competition(db, seeded_competition_code, fixture_date)

    _force_positive_ev(seeded_competition_code, fixture_date, home_team_id=home_team_id, away_team_id=away_team_id)

    with session_scope() as db:
        over_2_5 = (
            db.query(Prediction)
            .join(Match)
            .filter(Match.provider_id == "ranking-config-band-fixture", Prediction.market == "over_2_5")
            .one()
        )
        over_2_5_id = over_2_5.id
        market_odds = over_2_5.market_odds
        assert market_odds == pytest.approx(1.70)

    window_params = {"limit": 100, "days": 30, "scope": "all_upcoming"}
    try:
        # Con el default (1.35-1.90), la cuota real del partido (1.70)
        # esta DENTRO de la banda: debe aparecer.
        with_default_band = client.get("/predictions/top-signals", params=window_params)
        assert with_default_band.status_code == 200
        assert over_2_5_id in {p["id"] for p in with_default_band.json()}

        # Se guarda una banda mas ESTRECHA que excluye esa misma cuota real
        # (nunca una banda inventada: se calcula justo por debajo de la
        # cuota real del partido, para que el cambio sea deterministico).
        narrow_max = round(market_odds - 0.05, 2)
        assert narrow_max > 1.35
        put_response = client.put(
            "/config/ranking", json={"min_opportunity_odds": 1.35, "max_opportunity_odds": narrow_max}
        )
        assert put_response.status_code == 200
        assert put_response.json()["max_opportunity_odds"] == pytest.approx(narrow_max)

        with_narrow_band = client.get("/predictions/top-signals", params=window_params)
        assert with_narrow_band.status_code == 200
        assert over_2_5_id not in {p["id"] for p in with_narrow_band.json()}
        for prediction in with_narrow_band.json():
            assert prediction["market_odds"] <= narrow_max
    finally:
        # Nunca dejar un estado global (RankingConfig es una fila UNICA)
        # que contamine el resto de tests de este modulo, que asumen la
        # banda por defecto (1.35-1.90).
        reset_response = client.put(
            "/config/ranking", json={"min_opportunity_odds": 1.35, "max_opportunity_odds": 1.90}
        )
        assert reset_response.status_code == 200


def test_ranking_config_put_rejects_an_invalid_band(seeded_competition_code):
    client = TestClient(app)
    response = client.put("/config/ranking", json={"min_opportunity_odds": 1.0, "max_opportunity_odds": 1.90})
    assert response.status_code == 422

    response = client.put("/config/ranking", json={"min_opportunity_odds": 1.90, "max_opportunity_odds": 1.35})
    assert response.status_code == 422

    # El rechazo no debe haber dejado nada a medias guardado.
    unchanged = client.get("/config/ranking")
    assert unchanged.json()["min_opportunity_odds"] == pytest.approx(1.35)
    assert unchanged.json()["max_opportunity_odds"] == pytest.approx(1.90)


def test_odds_diagnostics_endpoint_reports_the_latest_real_ingestion_attempts():
    """Panel tecnico de "Configuracion" (pedido real del usuario tras
    investigar cuotas desactualizadas): `/config/odds-diagnostics` debe
    devolver el ULTIMO intento real de cada (provider, operation,
    competicion) -- exito o fallo -- y la frescura real ya guardada en
    `MatchOdds`, nunca nada inventado."""
    older = dt.datetime(2026, 11, 18, 6, 0, 0)
    newer = dt.datetime(2026, 11, 18, 12, 0, 0)
    with session_scope() as db:
        # Partido + cuota PROPIOS de este test (nunca depender de que
        # otro test anterior del modulo haya dejado algo en MatchOdds --
        # este test debe poder correr solo, en cualquier orden).
        comp = Competition(code="odds_diag_test_comp", name="Odds Diag Test Comp", country="Testland")
        db.add(comp)
        db.flush()
        season = Season(competition_id=comp.id, label="2026/27")
        db.add(season)
        db.flush()
        match = Match(
            provider="odds_diag_test",
            provider_id="odds-diag-fixture-1",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
            home_team_id=1,
            away_team_id=2,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        db.add(
            MatchOdds(
                match_id=match.id,
                bookmaker="Bet365",
                market="over_under_goals",
                line=2.5,
                selection="over",
                price=1.73,
                snapshot_type="pre_match",
                recorded_at=newer,
            )
        )
        db.add_all(
            [
                OddsIngestionRun(
                    provider="api_football",
                    operation="update_odds",
                    competition_code="laliga_integration_test",
                    started_at=older,
                    finished_at=older,
                    fixtures_fetched=5,
                    matched=5,
                ),
                OddsIngestionRun(
                    provider="api_football",
                    operation="update_odds",
                    competition_code="laliga_integration_test",
                    started_at=newer,
                    finished_at=newer,
                    fixtures_fetched=20,
                    matched=18,
                ),
                OddsIngestionRun(
                    provider="api_football",
                    operation="update_secondary_odds",
                    competition_code="premier_league_integration_test",
                    started_at=newer,
                    finished_at=newer,
                    error="API-Football rate limit: Too many requests.",
                ),
            ]
        )

    client = TestClient(app)
    response = client.get("/config/odds-diagnostics")
    assert response.status_code == 200
    body = response.json()

    runs_by_key = {(r["operation"], r["competition_code"]): r for r in body["latest_runs"]}
    laliga_run = runs_by_key[("update_odds", "laliga_integration_test")]
    # Solo el intento MAS RECIENTE de esa (provider, operation, competicion)
    # -- nunca los dos, aunque ambos siguen en la tabla (append-only).
    assert laliga_run["fixtures_fetched"] == 20
    assert laliga_run["matched"] == 18
    assert laliga_run["error"] is None

    failed_run = runs_by_key[("update_secondary_odds", "premier_league_integration_test")]
    assert failed_run["error"] == "API-Football rate limit: Too many requests."
    # Nunca 0 inventado para un intento que fallo antes de contar nada.
    assert failed_run["fixtures_fetched"] is None
    assert failed_run["matched"] is None

    # `>=`, no `==`: comparte BD con el resto de tests del modulo, que
    # pueden haber guardado cuotas aun mas recientes/otras casas -- esta
    # prueba solo comprueba que SU PROPIA cuota real quedo reflejada.
    # El backend expone esto con zona UTC explicita (ver UtcDatetime en
    # health.py) -- comparar contra `newer` (naive) requiere darle la
    # misma zona, nunca comparar aware con naive directamente.
    assert dt.datetime.fromisoformat(body["most_recent_odds_recorded_at"]) >= newer.replace(
        tzinfo=dt.timezone.utc
    )
    assert "Bet365" in body["bookmakers_seen"]
    assert body["scheduled_matches_with_odds"] >= 1
