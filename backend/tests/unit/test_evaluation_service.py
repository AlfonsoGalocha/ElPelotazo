from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.db.database import Base, session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.ingestion.base import DataProvider, RawMatchRecord, RawOddsRecord
from backend.app.normalization.competitions import COMPETITIONS
from backend.app.services.data_service import ingest_matches
from backend.app.services.evaluation_service import (
    MIN_SETTLED_FOR_EDGE_RELIABILITY,
    MIN_SETTLED_FOR_LONGSHOT_CALIBRATION,
    MIN_SETTLED_FOR_ODDS_RELIABILITY,
    MIN_SETTLED_FOR_TRACK_RECORD,
    evaluate_settled_predictions,
    fit_edge_reliability_curve,
    fit_longshot_calibration_curve,
    fit_odds_reliability_curve,
    market_track_records,
    market_track_records_with_provenance,
    settle_finished_predictions,
)
from backend.app.services.model_service import train_competition_models
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.tests.fixtures.synthetic import generate_synthetic_matches


class _FakeProvider(DataProvider):
    name = "evaluation_test_provider"

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
                    provider_id=f"eval-test-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"EvalTeam{int(r.home_team_id)}",
                    away_team_raw=f"EvalTeam{int(r.away_team_id)}",
                    home_goals=int(r.home_goals),
                    away_goals=int(r.away_goals),
                    home_shots=int(r.home_shots),
                    away_shots=int(r.away_shots),
                    home_corners=int(r.home_corners),
                    away_corners=int(r.away_corners),
                    odds=[
                        RawOddsRecord("bet365", "over_under_goals", 2.5, "over", 1.9),
                        RawOddsRecord("bet365", "over_under_goals", 2.5, "under", 1.95),
                    ],
                )
            )
        return records


def test_evaluate_settled_predictions_grades_a_now_finished_match():
    """Flujo completo del pedido del usuario: predecir un partido futuro,
    simular que se juega de verdad (lo que en produccion trae
    `football-edge update` tras `refresh_cache()`), y comprobar que la
    prediccion YA GUARDADA -- nunca una nueva -- se compara contra el
    resultado real."""
    competition_code = "evaluation_test_league"
    COMPETITIONS.setdefault(competition_code, ("Evaluation Test League", "Testland"))
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=3, seed=123)
    provider = _FakeProvider(synthetic)

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"

    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        season = (
            db.query(Season).filter_by(competition_id=comp.id).order_by(Season.id.desc()).first()
        )
        match = Match(
            provider="evaluation_test_manual",
            provider_id="eval-future-fixture-1",
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

    # Sin resultado real todavia: no hay nada que evaluar para esta competicion.
    with session_scope() as db:
        report = evaluate_settled_predictions(db, competition_code=competition_code)
    assert competition_code not in report["competitions"]

    # Simula que el partido ya se jugo de verdad.
    with session_scope() as db:
        db.query(Match).filter_by(id=match_id).update(
            {"home_goals": 2, "away_goals": 1, "status": "finished"}, synchronize_session=False
        )

    with session_scope() as db:
        report = evaluate_settled_predictions(db, competition_code=competition_code)
    assert competition_code in report["competitions"]
    market_reports = report["competitions"][competition_code]
    assert "over_2_5" in market_reports  # 2+1=3 goles: True para over_2_5

    quality = market_reports["over_2_5"]["model_quality"]
    assert quality["n_predictions"] == 1
    assert 0.0 <= quality["brier_score"] <= 1.0

    # over_2_5 tenia cuota real (1.90/1.95) en el proveedor fake -> debe haber estrategia.
    assert market_reports["over_2_5"]["market_strategy"] is not None

    # Prediccion original preservada, nunca borrada por haberse jugado el partido.
    with session_scope() as db:
        remaining = db.query(Match).filter_by(id=match_id).one()
        assert remaining.status == "finished"


class _FakeProviderForMissingCornersTest(DataProvider):
    """Nombre de proveedor y de equipos PROPIOS (no reutiliza `_FakeProvider`
    de arriba): con el mismo `n_teams`, `generate_synthetic_matches` genera
    SIEMPRE las mismas fechas de calendario (solo cambian goles/stats segun
    el seed) -- reutilizar el mismo `provider.name`/mismos nombres de equipo
    ("EvalTeam1", etc.) haria que `resolve_team_id` devolviera los mismos
    `Team` ya creados por OTRO test de este fichero, y el partido se
    absorberia como "duplicado" de uno de OTRA competicion en vez de crear
    partidos nuevos para esta."""

    name = "missing_corners_test_provider"

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
                    provider_id=f"missing-corners-test-{r.match_id}",
                    competition_code=competition_code,
                    season_label=season_label,
                    date=r.date.date().isoformat(),
                    home_team_raw=f"MissingCornersTeam{int(r.home_team_id)}",
                    away_team_raw=f"MissingCornersTeam{int(r.away_team_id)}",
                    home_goals=int(r.home_goals),
                    away_goals=int(r.away_goals),
                    home_shots=int(r.home_shots),
                    away_shots=int(r.away_shots),
                    home_corners=int(r.home_corners),
                    away_corners=int(r.away_corners),
                )
            )
        return records


def test_settle_never_fabricates_a_corners_outcome_for_a_match_without_real_corners_data():
    """Bug real reportado por el usuario (casi todos los partidos salian
    con 85%+ en "menos de 9.5 corners", sin sentido): el partido en si
    puede tener resultado real (goles) pero carecer de corners reales
    (fuente que no los trae para el) -- liquidar su prediccion de corners
    como "se cumplio menos de 9.5" seria inventar un resultado que nunca
    se supo. Debe quedar SIN liquidar (pendiente), mientras que la
    prediccion de goles del MISMO partido si se liquida con normalidad."""
    competition_code = "settle_missing_corners_test_league"
    COMPETITIONS.setdefault(competition_code, ("Settle Missing Corners Test League", "Testland"))
    synthetic = generate_synthetic_matches(n_teams=8, n_seasons=3, seed=321)
    provider = _FakeProviderForMissingCornersTest(synthetic)

    with session_scope() as db:
        for season in synthetic["season_label"].unique():
            ingest_matches(db, provider, competition_code, season)

    with session_scope() as db:
        result = train_competition_models(db, competition_code)
        assert result["status"] == "trained"

    with session_scope() as db:
        comp = db.query(Competition).filter_by(code=competition_code).one()
        season = (
            db.query(Season).filter_by(competition_id=comp.id).order_by(Season.id.desc()).first()
        )
        # IDs de equipo REALES de esta competicion (nunca hardcoded 1/2:
        # los equipos son globales en la BD, y para cuando corre este test
        # ya existen muchos de OTROS tests -- se reusa el par de un
        # partido YA ingerido de esta misma competicion).
        existing_match = db.query(Match).filter_by(competition_id=comp.id).first()
        home_team_id, away_team_id = existing_match.home_team_id, existing_match.away_team_id
        # Partido FUTURO (todavia sin jugar): nunca tiene MatchStatistics
        # todavia, igual que cualquier partido programado de verdad.
        match = Match(
            provider="settle_missing_corners_test_manual",
            provider_id="settle-missing-corners-future-1",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            status="scheduled",
        )
        db.add(match)
        db.flush()
        match_id = match.id

    fixture_date = (dt.datetime.utcnow() + dt.timedelta(days=1)).date()
    with session_scope() as db:
        predictions = generate_predictions_for_competition(db, competition_code, fixture_date)
        corners_prediction_ids = [p.id for p in predictions if p.market.startswith("corners_")]
        non_corners_prediction_ids = [p.id for p in predictions if not p.market.startswith("corners_")]
        assert corners_prediction_ids  # el modelo de corners SI entreno (datos reales en el fake provider)
        assert non_corners_prediction_ids

    # El partido se juega de verdad -- CON goles reales, pero SIN corners
    # (ninguna MatchStatistics se crea: misma situacion que una fuente que
    # trae el resultado pero no las estadisticas del partido).
    with session_scope() as db:
        db.query(Match).filter_by(id=match_id).update(
            {"home_goals": 2, "away_goals": 1, "status": "finished"}, synchronize_session=False
        )

    with session_scope() as db:
        created = settle_finished_predictions(db, competition_code=competition_code)
        # Los 4 mercados de corners quedan SIN liquidar (pendientes); el
        # resto (goles/1X2/BTTS) se liquida con normalidad.
        assert created == len(non_corners_prediction_ids)

    with session_scope() as db:
        corners_results = (
            db.query(PredictionResult)
            .filter(PredictionResult.prediction_id.in_(corners_prediction_ids))
            .all()
        )
        assert corners_results == []  # nunca un resultado inventado

        non_corners_results = (
            db.query(PredictionResult)
            .filter(PredictionResult.prediction_id.in_(non_corners_prediction_ids))
            .all()
        )
        assert len(non_corners_results) == len(non_corners_prediction_ids)


def _seed_settled_predictions(
    db, competition_code: str, market: str, pairs: list[tuple[float, bool]], tier: str | None = None
) -> None:
    """Inserta directamente N (Prediction, PredictionResult) ya liquidados
    para (competition_code, market), sin pasar por el pipeline completo de
    entrenamiento/prediccion -- aqui solo interesa la consulta de
    `market_track_records`, no reprobar el settlement end-to-end (ya
    cubierto en test_settlement.py). `tier` opcional (nunca se pasa `None`
    explicito al modelo, para no pisar su default real "CORE") -- usado
    por los tests de fiabilidad jerarquica (seccion 2.2) para simular
    competiciones de un tier concreto."""
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    comp = Competition(code=competition_code, name=competition_code, country="Testland")
    if tier is not None:
        comp.tier = tier
    db.add(comp)
    db.flush()
    season = Season(competition_id=comp.id, label="2024-2025")
    db.add(season)
    model_version = ModelVersion(
        name="track-record-test",
        version="v1",
        market_family="goals",
        features=[],
        hyperparameters={},
        metrics={},
        dataset_version="test",
    )
    db.add(model_version)
    db.flush()

    for i, (probability, outcome) in enumerate(pairs):
        match = Match(
            provider="track_record_test",
            provider_id=f"track-record-{competition_code}-{market}-{i}",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() - dt.timedelta(days=1),
            home_team_id=1,
            away_team_id=2,
            status="finished",
            home_goals=1,
            away_goals=0,
        )
        db.add(match)
        db.flush()
        prediction = Prediction(
            match_id=match.id,
            market=market,
            line=2.5,
            selection="over",
            model_version_id=model_version.id,
            model_probability=probability,
            fair_odds=1 / probability,
            confidence=0.8,
            data_quality=0.8,
            explanation={},
            features_used={},
            created_at=dt.datetime.utcnow(),
        )
        db.add(prediction)
        db.flush()
        db.add(
            PredictionResult(
                prediction_id=prediction.id, outcome=outcome, settled_at=dt.datetime.utcnow()
            )
        )
    db.flush()


def test_market_track_records_empty_below_minimum_settled_sample():
    """Menos liquidaciones EXACTAS que MIN_SETTLED_FOR_TRACK_RECORD: nunca
    se inventa un numero de nivel "competition_market" sobre una muestra
    insuficiente -- nunca en 0.0 ni en 1.0.

    Bug real de diseño confirmado: antes esto significaba que la clave
    desaparecia del TODO el dict; ahora la jerarquia (seccion 2.2,
    "fiabilidad jerarquica") puede rellenarla con un nivel mas general
    (tier/global) SI otras competiciones/tiers tienen suficiente historico
    real para ese mismo mercado -- eso es el comportamiento CORRECTO, no
    un bug (ver market_track_records_with_provenance). Lo que nunca debe
    pasar es que esta muestra insuficiente, por si sola, produzca un nivel
    "competition_market" -- se verifica la PROCEDENCIA, no la ausencia
    total (que ya no se puede garantizar: otros tests de este mismo
    fichero siembran el mismo mercado real "over_2_5" en otras
    competiciones, y es correcto que ese historico SI alimente el nivel
    global)."""
    competition_code = "track-record-too-few"
    pairs = [(0.8, True)] * (MIN_SETTLED_FOR_TRACK_RECORD - 1)
    with session_scope() as db:
        _seed_settled_predictions(db, competition_code, "over_2_5", pairs)

    with session_scope() as db:
        records = market_track_records_with_provenance(db, competition_code=competition_code)
    estimate = records.get((competition_code, "over_2_5"))
    assert estimate is None or estimate.source_level != "competition_market"


def test_market_track_records_well_calibrated_market_scores_near_one():
    competition_code = "track-record-well-calibrated"
    # 80% de probabilidad predicha, ~80% de aciertos reales: calibracion casi perfecta.
    pairs = [(0.8, True)] * 32 + [(0.8, False)] * 8
    with session_scope() as db:
        _seed_settled_predictions(db, competition_code, "over_2_5", pairs)

    with session_scope() as db:
        records = market_track_records(db, competition_code=competition_code)
    assert records[(competition_code, "over_2_5")] > 0.9


def test_market_track_records_poorly_calibrated_market_scores_lower():
    competition_code = "track-record-poorly-calibrated"
    # 90% de probabilidad predicha pero solo 30% de aciertos reales: mala calibracion.
    pairs = [(0.9, True)] * 12 + [(0.9, False)] * 28
    with session_scope() as db:
        _seed_settled_predictions(db, competition_code, "over_2_5", pairs)

    with session_scope() as db:
        records = market_track_records(db, competition_code=competition_code)
    assert records[(competition_code, "over_2_5")] < 0.5


def test_market_track_records_is_per_competition_and_market():
    with session_scope() as db:
        _seed_settled_predictions(
            db, "track-record-scope-a", "over_2_5", [(0.8, True)] * 32 + [(0.8, False)] * 8
        )
        _seed_settled_predictions(
            db, "track-record-scope-b", "btts", [(0.9, True)] * 12 + [(0.9, False)] * 28
        )

    with session_scope() as db:
        scoped = market_track_records(db, competition_code="track-record-scope-a")
    assert ("track-record-scope-a", "over_2_5") in scoped
    assert ("track-record-scope-b", "btts") not in scoped


def test_market_track_records_falls_back_to_tier_when_exact_competition_is_insufficient():
    """Fiabilidad jerarquica (seccion 2.2, "mision oportunidades"): una
    competicion con pocas liquidaciones EXACTAS para un mercado real
    "corners_over_10_5" (mercado poco usado en el resto de este fichero,
    para no mezclarse con el historico global de otros tests) hereda la
    fiabilidad de OTRA competicion del MISMO tier con historico real
    suficiente -- nunca queda vacia solo por falta de recorrido propio."""
    rich_pairs = [(0.75, True)] * 24 + [(0.75, False)] * 8  # 32 >= minimo, bien calibrado
    thin_pairs = [(0.75, True)] * 5  # muy por debajo del minimo
    with session_scope() as db:
        _seed_settled_predictions(
            db, "track-record-tier-rich", "corners_over_10_5", rich_pairs, tier="EXPERIMENTAL"
        )
        _seed_settled_predictions(
            db, "track-record-tier-thin", "corners_over_10_5", thin_pairs, tier="EXPERIMENTAL"
        )

    with session_scope() as db:
        records = market_track_records_with_provenance(db, competition_code="track-record-tier-thin")

    estimate = records[("track-record-tier-thin", "corners_over_10_5")]
    assert estimate.source_level == "market_tier"
    assert estimate.n_observations == len(rich_pairs) + len(thin_pairs)
    assert estimate.value > 0.9  # heredado del historico bien calibrado


def test_market_track_records_falls_back_to_global_when_tier_is_also_insufficient():
    """Ultimo nivel de la jerarquia: si ni la competicion exacta ni su tier
    tienen historico suficiente, se usa el mercado GLOBAL (todas las
    competiciones/tiers). Mercado real poco comun ("corners_under_9_5")
    para que el historico global de este test sea el UNICO contribuyente
    real en la suite."""
    rich_pairs = [(0.75, True)] * 24 + [(0.75, False)] * 8
    thin_pairs = [(0.75, True)] * 3
    with session_scope() as db:
        _seed_settled_predictions(
            db, "track-record-global-rich", "corners_under_9_5", rich_pairs, tier="CORE"
        )
        _seed_settled_predictions(
            db, "track-record-global-thin", "corners_under_9_5", thin_pairs, tier="EXPERIMENTAL"
        )

    with session_scope() as db:
        records = market_track_records_with_provenance(
            db, competition_code="track-record-global-thin"
        )

    estimate = records[("track-record-global-thin", "corners_under_9_5")]
    assert estimate.source_level == "market_global"
    assert estimate.n_observations == len(rich_pairs) + len(thin_pairs)


def test_evaluate_settled_predictions_empty_db_returns_no_competitions():
    with session_scope() as db:
        report = evaluate_settled_predictions(
            db, competition_code="a_competition_that_does_not_exist"
        )
    assert report == {"competitions": {}}


# fit_edge_reliability_curve (fix real tras activar signal_score_v2: ver
# prediction/ranking.py::reality_capped_expected_value para el contexto
# completo -- un edge grande en produccion acierta MENOS, no mas, evidencia real en
# scripts/debug_edge_vs_hitrate.py).


def _seed_settled_predictions_with_edge(
    db, competition_code: str, rows: list[tuple[float, float, bool]]
) -> None:
    """Igual que `_seed_settled_predictions`, pero con `market_odds`/`edge`
    reales (requeridos por `fit_edge_reliability_curve`, que los exige con
    `.isnot(None)`) -- cada fila es (model_probability, edge, outcome)."""
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    comp = Competition(code=competition_code, name=competition_code, country="Testland")
    db.add(comp)
    db.flush()
    season = Season(competition_id=comp.id, label="2024-2025")
    db.add(season)
    model_version = ModelVersion(
        name="edge-reliability-test",
        version="v1",
        market_family="goals",
        features=[],
        hyperparameters={},
        metrics={},
        dataset_version="test",
    )
    db.add(model_version)
    db.flush()

    for i, (probability, edge, outcome) in enumerate(rows):
        market_probability = probability - edge
        market_odds = 1.0 / market_probability
        match = Match(
            provider="edge_reliability_test",
            provider_id=f"edge-reliability-{competition_code}-{i}",
            competition_id=comp.id,
            season_id=season.id,
            kickoff_utc=dt.datetime.utcnow() - dt.timedelta(days=1),
            home_team_id=1,
            away_team_id=2,
            status="finished",
            home_goals=1,
            away_goals=0,
        )
        db.add(match)
        db.flush()
        prediction = Prediction(
            match_id=match.id,
            market="over_2_5",
            line=2.5,
            selection="over",
            model_version_id=model_version.id,
            model_probability=probability,
            market_probability=market_probability,
            market_odds=market_odds,
            fair_odds=1 / probability,
            edge=edge,
            confidence=0.8,
            data_quality=0.8,
            explanation={},
            features_used={},
            created_at=dt.datetime.utcnow(),
        )
        db.add(prediction)
        db.flush()
        db.add(
            PredictionResult(
                prediction_id=prediction.id, outcome=outcome, settled_at=dt.datetime.utcnow()
            )
        )
    db.flush()


@contextmanager
def _isolated_db_session():
    """`fit_edge_reliability_curve` pool TODAS las predicciones liquidadas
    con cuota SIN filtrar por competicion (a proposito, ver su docstring:
    la evidencia real se mantiene igual o mas fuerte agregada). Eso hace
    que probar su umbral minimo contra el fichero SQLite COMPARTIDO de
    toda la sesion de pytest (`session_scope`, ver conftest.py) sea
    fragil -- otros tests de este mismo fichero YA generan predicciones
    reales con cuota/edge via `generate_predictions_for_competition`, que
    se suman al conteo global. Un motor en memoria, aislado y descartado
    al salir del bloque, evita esa contaminacion cruzada sin tocar el
    codigo de produccion (que deliberadamente no filtra por competicion)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    IsolatedSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = IsolatedSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_fit_edge_reliability_curve_is_none_below_minimum_sample():
    with _isolated_db_session() as db:
        rows = [(0.55, 0.02, True)] * (MIN_SETTLED_FOR_EDGE_RELIABILITY - 1)
        _seed_settled_predictions_with_edge(db, "edge-reliability-too-few", rows)
        db.commit()
        curve = fit_edge_reliability_curve(db)
    assert curve is None


def test_fit_edge_reliability_curve_decreases_hit_rate_prediction_with_edge_size():
    """Mismo patron que la evidencia real (`debug_edge_vs_hitrate.py`):
    edge pequenho acierta mucho, edge grande acierta poco -- la curva
    ajustada debe reflejarlo (nunca al reves, por construccion
    `increasing=False`)."""
    with _isolated_db_session() as db:
        rows = (
            [(0.55, 0.02, True)] * 25
            + [(0.55, 0.02, False)] * 15  # edge pequenho: ~63% acierto
            + [(0.90, 0.35, True)] * 6
            + [(0.90, 0.35, False)] * 24  # edge enorme: ~20% acierto
        )
        _seed_settled_predictions_with_edge(db, "edge-reliability-decreasing", rows)
        db.commit()
        curve = fit_edge_reliability_curve(db)
    assert curve is not None
    small_edge_hit_rate = curve.predict([2.0])[0]
    large_edge_hit_rate = curve.predict([35.0])[0]
    assert small_edge_hit_rate > large_edge_hit_rate


# fit_odds_reliability_curve (favorite-longshot bias, seccion 13
# de docs/architecture_audit.md -- ver prediction/ranking.py::reality_capped_expected_value
# para el contexto completo: tras el fix de edge, el usuario reporto
# "victoria local" a cuota 5.50/6.00 seguia destacada pese a
# model_probability bajo. `scripts/debug_odds_vs_hitrate.py` confirmo que
# el modelo sobrestima su propia probabilidad en esa cola.
#
# IMPORTANTE (bug real corregido antes de desplegar): la curva se ajusta
# sobre `market_odds`, NUNCA sobre `model_probability` -- un
# `model_probability` identico aparece tanto en cuotas bajas bien
# calibradas como en cuotas altas mal calibradas; agrupar por
# probabilidad diluiria la correccion justo donde mas hace falta (visto
# EN VIVO: el caso real, model_probability=0.335/cuota 5.50, seguia casi
# sin penalizar con la primera version que si agrupaba por
# probabilidad).)


def test_fit_odds_reliability_curve_is_none_below_minimum_sample():
    with _isolated_db_session() as db:
        rows = [(0.55, 0.02, True)] * (MIN_SETTLED_FOR_ODDS_RELIABILITY - 1)
        _seed_settled_predictions_with_edge(db, "odds-reliability-too-few", rows)
        db.commit()
        curve = fit_odds_reliability_curve(db)
    assert curve is None


def test_fit_odds_reliability_curve_decreases_hit_rate_prediction_with_odds():
    """Mismo patron que la evidencia real (`debug_odds_vs_hitrate.py`):
    cuota alta ("longshot") acierta mucho menos que cuota baja
    (favorito) -- la curva ajustada debe reflejarlo (nunca al reves, por
    construccion `increasing=False`). `_seed_settled_predictions_with_edge`
    deriva `market_odds` de `(model_probability, edge)`
    (`market_odds = 1/(model_probability - edge)`): favorito ->
    `market_odds` ~1.67; longshot -> `market_odds` ~7.69."""
    with _isolated_db_session() as db:
        rows = (
            [(0.65, 0.05, True)] * 22
            + [(0.65, 0.05, False)] * 8  # favorito (cuota ~1.67): ~73% acierto
            + [(0.18, 0.05, True)] * 2
            + [(0.18, 0.05, False)] * 28  # longshot (cuota ~7.69): ~7% acierto
        )
        _seed_settled_predictions_with_edge(db, "odds-reliability-decreasing", rows)
        db.commit()
        curve = fit_odds_reliability_curve(db)
    assert curve is not None
    favorite_odds = 1.0 / (0.65 - 0.05)
    longshot_odds = 1.0 / (0.18 - 0.05)
    favorite_hit_rate = curve.predict([favorite_odds])[0]
    longshot_hit_rate = curve.predict([longshot_odds])[0]
    assert longshot_hit_rate < favorite_hit_rate


# fit_longshot_calibration_curve (seccion 15 de docs/architecture_audit.md
# -- pedido explicito del usuario tras el rollback de emergencia del techo
# de cuota: "quiero que el modelo sea realista tambien con las
# predicciones". A diferencia de `fit_odds_reliability_curve` (que usaba
# ROI promedio, negativo en TODAS las cuotas por el vig, y colapsaba el
# ranking), esta recalibra la PROBABILIDAD misma -- acotada en [0,1],
# nunca un ROI -- y SOLO en la cola de cuota alta.)


def test_fit_longshot_calibration_curve_is_none_below_minimum_sample():
    with _isolated_db_session() as db:
        rows = [(0.30, 0.05, True)] * (MIN_SETTLED_FOR_LONGSHOT_CALIBRATION - 1)
        _seed_settled_predictions_with_edge(db, "longshot-calibration-too-few", rows)
        db.commit()
        curve = fit_longshot_calibration_curve(db)
    assert curve is None


def test_fit_longshot_calibration_curve_ignores_rows_below_the_odds_threshold():
    """Solo pool liquidaciones con cuota >= `LONGSHOT_ODDS_THRESHOLD` --
    una muestra enorme de favoritos (cuota baja) nunca debe bastar por
    si sola para ajustar esta curva."""
    with _isolated_db_session() as db:
        # model_probability=0.65, edge=0.05 -> market_probability=0.60 ->
        # cuota ~1.67, muy por debajo del umbral: TODAS excluidas.
        rows = [(0.65, 0.05, True)] * (MIN_SETTLED_FOR_LONGSHOT_CALIBRATION * 3)
        _seed_settled_predictions_with_edge(db, "longshot-calibration-wrong-odds", rows)
        db.commit()
        curve = fit_longshot_calibration_curve(db)
    assert curve is None


def test_fit_longshot_calibration_curve_increases_hit_rate_prediction_with_probability():
    """Mismo patron que la evidencia real de la seccion 13.2, restringido
    a la cola: probabilidad dicha mas baja acierta menos que probabilidad
    dicha mas alta -- la curva debe reflejarlo (nunca al reves, por
    construccion `increasing=True`)."""
    with _isolated_db_session() as db:
        rows = (
            [(0.15, 0.05, True)] * 3
            + [(0.15, 0.05, False)] * 27  # cuota 10.0: ~10% acierto
            + [(0.30, 0.05, True)] * 9
            + [(0.30, 0.05, False)] * 21  # cuota 4.0: ~30% acierto
        )
        _seed_settled_predictions_with_edge(db, "longshot-calibration-increasing", rows)
        db.commit()
        curve = fit_longshot_calibration_curve(db)
    assert curve is not None
    low_probability_hit_rate = curve.predict([0.15])[0]
    high_probability_hit_rate = curve.predict([0.30])[0]
    assert low_probability_hit_rate < high_probability_hit_rate
