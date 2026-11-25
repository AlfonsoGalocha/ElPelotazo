"""Fase 7 de la revision "motor profesional 2.0": comparar ROI/win-rate/
Brier real de v1 (`signal_score`, recalculada) vs v2 (`signal_score_v2`)
sobre predicciones YA LIQUIDADAS, restringido al TOP-N de cada estrategia
-- sin reentrenar nada, sin riesgo de leakage (ver docstring del modulo).
v2 ya es la formula activa en produccion (ver
docs/architecture_audit.md seccion 11); esta comparacion recalcula AMBAS
desde los campos congelados, nunca lee `prediction.signal_score`
directamente (esa columna ahora congela v2 para predicciones nuevas).
"""

from __future__ import annotations

import datetime as dt

from backend.app.backtesting.ranking_comparison import (
    V3_STRATEGY_NAME,
    WALK_FORWARD_STRATEGIES,
    compare_ranking_strategies,
    compare_ranking_strategies_walk_forward,
)
from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.normalization.competitions import COMPETITIONS


def _prediction(id: int, **overrides) -> Prediction:
    defaults = dict(
        id=id,
        match_id=id,
        market="over_2_5",
        line=2.5,
        selection="over",
        model_version_id=1,
        model_probability=0.6,
        market_probability=0.5,
        market_odds=1.9,
        fair_odds=1.67,
        edge=0.10,
        expected_value=0.14,
        risk_adjusted_expected_value=0.05,
        confidence=0.7,
        data_quality=0.7,
        uncertainty_low=0.55,
        uncertainty_high=0.65,
        explanation={"factors": []},
        features_used={"feature_names": []},
        created_at=dt.datetime.utcnow(),
        signal_score=0.1,
    )
    defaults.update(overrides)
    return Prediction(**defaults)


def _result(outcome: bool) -> PredictionResult:
    return PredictionResult(outcome=outcome)


def test_compare_ranking_strategies_reports_both_v1_and_v2_slices():
    pairs = [
        (_prediction(1, signal_score=0.30), _result(True)),
        (_prediction(2, signal_score=0.10), _result(False)),
        (_prediction(3, signal_score=0.05), _result(True)),
    ]
    report = compare_ranking_strategies(pairs, top_n_values=(2,))

    strategies = {s.strategy for s in report.slices}
    assert strategies == {"v1", "v2"}
    top_n_labels = {s.top_n for s in report.slices}
    assert top_n_labels == {"2", "all"}


def test_compare_ranking_strategies_excludes_predictions_without_market_odds():
    pairs = [
        (_prediction(1, market_odds=None), _result(True)),
        (_prediction(2), _result(True)),
    ]
    report = compare_ranking_strategies(pairs, top_n_values=(5,))
    assert report.n_settled_with_market == 1
    all_slice_v1 = next(s for s in report.slices if s.strategy == "v1" and s.top_n == "all")
    assert all_slice_v1.n_bets == 1


def test_compare_ranking_strategies_top_n_uses_the_highest_scored_first():
    """El TOP-1 de v1 (recalculado con la formula v1 real, nunca leyendo
    `prediction.signal_score` -- ver `_score_v1`) debe ser exactamente la
    prediccion con mayor score v1, acertada o no."""
    winner_high_score = _prediction(1, edge=0.35)
    loser_low_score = _prediction(2, edge=0.01)
    pairs = [(winner_high_score, _result(False)), (loser_low_score, _result(True))]

    report = compare_ranking_strategies(pairs, top_n_values=(1,))
    top1_v1 = next(s for s in report.slices if s.strategy == "v1" and s.top_n == "1")
    assert top1_v1.n_bets == 1
    assert top1_v1.win_rate == 0.0  # el de mayor score perdio -- se refleja tal cual, no se maquilla


def test_compare_ranking_strategies_v2_can_rank_differently_from_v1():
    """Construye un caso donde v1 (score congelado alto) y v2 (recalculado
    con EV ajustado al riesgo negativo) discrepan sobre cual es la "mejor"
    -- exactamente el escenario que motivo signal_score_v2 (ver
    test_ranking.py::test_signal_score_v2_never_favors_the_getafe_style_extreme_case)."""
    extreme_but_shaky = _prediction(
        1,
        edge=0.40,  # v1: edge enorme, domina el ranking v1 (el sintoma real)
        model_probability=0.819,
        market_odds=2.39,
        risk_adjusted_expected_value=-0.04,  # v2: EV ajustado negativo -> score_v2 = 0
        uncertainty_low=0.40,
        uncertainty_high=0.95,
    )
    solid_and_supported = _prediction(
        2,
        edge=0.05,  # v1: edge modesto
        model_probability=0.65,
        market_odds=1.75,
        risk_adjusted_expected_value=0.05,  # v2: EV ajustado positivo -> score_v2 > 0
        uncertainty_low=0.60,
        uncertainty_high=0.70,
    )
    pairs = [(extreme_but_shaky, _result(False)), (solid_and_supported, _result(True))]

    report = compare_ranking_strategies(pairs, top_n_values=(1,))
    top1_v1 = next(s for s in report.slices if s.strategy == "v1" and s.top_n == "1")
    top1_v2 = next(s for s in report.slices if s.strategy == "v2" and s.top_n == "1")

    assert top1_v1.win_rate == 0.0  # v1 eligio la extrema (perdio)
    assert top1_v2.win_rate == 1.0  # v2 eligio la solida (gano)


def test_compare_ranking_strategies_computes_pnl_and_drawdown():
    pairs = [
        (_prediction(1, market_odds=2.0, signal_score=0.9), _result(True)),  # pnl +1.0
        (_prediction(2, market_odds=2.0, signal_score=0.8), _result(False)),  # pnl -1.0
    ]
    report = compare_ranking_strategies(pairs, top_n_values=(2,))
    slice_all_v1 = next(s for s in report.slices if s.strategy == "v1" and s.top_n == "2")
    assert slice_all_v1.total_pnl == 0.0
    assert slice_all_v1.roi == 0.0
    assert slice_all_v1.max_drawdown <= 0.0


def test_compare_ranking_strategies_reports_empty_when_nothing_settled_with_market():
    report = compare_ranking_strategies([], top_n_values=(5,))
    assert report.n_settled_with_market == 0
    assert report.slices == []


# compare_ranking_strategies_walk_forward (seccion 15 de
# docs/architecture_audit.md): a diferencia de compare_ranking_strategies
# (valido sin walk-forward para v1/v2, que no tienen ningun parametro
# ajustado), las estrategias con curva (`v2_edge_cap`, produccion actual;
# `v2_edge_cap_longshot`, candidata pendiente de validar) exigen ajustar
# cada curva SOLO con liquidaciones estrictamente anteriores a cada
# prediccion evaluada -- de lo contrario la comparacion tendria leakage
# de validacion (la curva "veria" el resultado que esta puntuando).


def test_compare_ranking_strategies_walk_forward_is_empty_below_minimum_sample():
    base = dt.datetime(2024, 1, 1)
    pairs = [
        (_prediction(i), _result(True), base + dt.timedelta(days=i)) for i in range(10)
    ]
    report = compare_ranking_strategies_walk_forward(pairs, min_train_n=30)
    assert report.slices == []
    assert report.n_settled_with_market == 10


def test_compare_ranking_strategies_walk_forward_reports_all_four_strategies():
    base = dt.datetime(2024, 1, 1)
    pairs = [
        (
            _prediction(i, edge=0.05, market_odds=2.0),
            _result(i % 2 == 0),
            base + dt.timedelta(days=i),
        )
        for i in range(40)
    ]
    report = compare_ranking_strategies_walk_forward(pairs, min_train_n=30, top_n_values=(5,))
    strategies = {s.strategy for s in report.slices}
    assert strategies == set(WALK_FORWARD_STRATEGIES)
    # Solo las liquidaciones DESPUES de min_train_n se evaluan (40 - 30 = 10).
    all_slice = next(s for s in report.slices if s.strategy == "v2" and s.top_n == "all")
    assert all_slice.n_bets == 10


def test_compare_ranking_strategies_walk_forward_never_fits_curve_on_future_or_self_data(
    monkeypatch,
):
    """Verificacion directa (espia la funcion de ajuste real, no solo
    infiere de los resultados agregados) de que cada ajuste de la curva
    de edge recibe EXCLUSIVAMENTE observaciones estrictamente anteriores
    a la prediccion evaluada -- nunca la propia prediccion ni ninguna
    posterior cronologicamente."""
    import backend.app.backtesting.ranking_comparison as module

    real_fit = module.fit_edge_reliability_curve_from_observations
    seen_lengths: list[int] = []

    def _spy(edge_pp, outcome):
        seen_lengths.append(len(edge_pp))
        return real_fit(edge_pp, outcome)

    monkeypatch.setattr(module, "fit_edge_reliability_curve_from_observations", _spy)

    base = dt.datetime(2024, 1, 1)
    pairs = [
        (
            _prediction(i, edge=0.05, market_odds=2.0),
            _result(i % 2 == 0),
            base + dt.timedelta(days=i),
        )
        for i in range(70)
    ]
    compare_ranking_strategies_walk_forward(pairs, min_train_n=30, top_n_values=(5,))

    # Primer ajuste al llegar al indice 30 (0-indexado, i==min_train_n):
    # debe usar EXACTAMENTE las 30 liquidaciones anteriores -- ni 31 (que
    # incluiria la propia evaluada) ni mas (que incluiria el futuro).
    # Segundo ajuste (WALK_FORWARD_REFIT_EVERY=25) al llegar a i=50: debe
    # usar exactamente las 50 anteriores.
    assert seen_lengths == [30, 50]


def test_compare_ranking_strategies_walk_forward_never_fits_longshot_curve_on_future_or_self_data(
    monkeypatch,
):
    """Misma verificacion directa que la prueba de espia del techo de
    edge, aplicada a `fit_longshot_calibration_curve_from_observations`
    -- solo se alimenta con la cola (cuota >= `LONGSHOT_ODDS_THRESHOLD`)
    de liquidaciones estrictamente anteriores."""
    import backend.app.backtesting.ranking_comparison as module

    real_fit = module.fit_longshot_calibration_curve_from_observations
    seen_lengths: list[int] = []

    def _spy(model_probability, outcome):
        seen_lengths.append(len(model_probability))
        return real_fit(model_probability, outcome)

    monkeypatch.setattr(module, "fit_longshot_calibration_curve_from_observations", _spy)

    base = dt.datetime(2024, 1, 1)
    # Todas con cuota 5.0 (>= LONGSHOT_ODDS_THRESHOLD=4.0): cada fila
    # cuenta para el historico de la cola.
    pairs = [
        (
            _prediction(i, edge=0.05, market_odds=5.0),
            _result(i % 2 == 0),
            base + dt.timedelta(days=i),
        )
        for i in range(70)
    ]
    compare_ranking_strategies_walk_forward(pairs, min_train_n=30, top_n_values=(5,))

    assert seen_lengths == [30, 50]


def test_compare_ranking_strategies_walk_forward_longshot_curve_ignores_non_longshot_priors(
    monkeypatch,
):
    """El historico de la curva de la cola se acumula SOLO con cuota >=
    `LONGSHOT_ODDS_THRESHOLD` -- liquidaciones de favoritos (cuota baja)
    no deben contar para alcanzar el minimo de la cola, aunque si
    cuenten para el minimo general de filas evaluadas."""
    import backend.app.backtesting.ranking_comparison as module

    real_fit = module.fit_longshot_calibration_curve_from_observations
    seen_lengths: list[int] = []

    def _spy(model_probability, outcome):
        seen_lengths.append(len(model_probability))
        return real_fit(model_probability, outcome)

    monkeypatch.setattr(module, "fit_longshot_calibration_curve_from_observations", _spy)

    base = dt.datetime(2024, 1, 1)
    # Cuota 1.5 (favorito, por debajo del umbral de la cola): nunca debe
    # alimentar la curva de longshot, por muchas filas que haya.
    pairs = [
        (
            _prediction(i, edge=0.05, market_odds=1.5),
            _result(True),
            base + dt.timedelta(days=i),
        )
        for i in range(70)
    ]
    compare_ranking_strategies_walk_forward(pairs, min_train_n=30, top_n_values=(5,))

    assert seen_lengths == []  # nunca hay suficiente muestra de COLA para ajustarla


def _seed_backup_settlements(
    db, competition_code: str, market: str, kickoffs: list[dt.datetime]
) -> None:
    """Liquidaciones mínimas (sin cuota/calibración/incertidumbre: solo lo
    que `market_track_records_with_provenance` necesita -- `model_probability`
    + `outcome` + `Match.kickoff_utc`) para alimentar el nivel GLOBAL/tier
    de la jerarquía de fiabilidad histórica, en una competición/mercado
    DISTINTO del que se evalúa en el test."""
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    comp = Competition(code=competition_code, name=competition_code, country="Testland")
    db.add(comp)
    db.flush()
    season = Season(competition_id=comp.id, label="2024-2025")
    db.add(season)
    model_version = ModelVersion(
        name="wf-v3-backup", version="v1", market_family="goals",
        features=[], hyperparameters={}, metrics={}, dataset_version="test",
    )
    db.add(model_version)
    db.flush()
    for i, kickoff in enumerate(kickoffs):
        match = Match(
            provider="wf_v3_backup_test", provider_id=f"wf-v3-backup-{competition_code}-{i}",
            competition_id=comp.id, season_id=season.id, kickoff_utc=kickoff,
            home_team_id=1, away_team_id=2, status="finished", home_goals=1, away_goals=0,
        )
        db.add(match)
        db.flush()
        prediction = Prediction(
            match_id=match.id, market=market, line=None, selection="away",
            model_version_id=model_version.id, model_probability=0.6, fair_odds=1 / 0.6,
            confidence=0.8, data_quality=0.8, explanation={}, features_used={},
            created_at=kickoff - dt.timedelta(minutes=15),
        )
        db.add(prediction)
        db.flush()
        db.add(
            PredictionResult(
                prediction_id=prediction.id, outcome=(i % 3 != 0), settled_at=kickoff + dt.timedelta(hours=2)
            )
        )
    db.flush()


def _seed_v3_eligible_target(db, competition_code: str, market: str, kickoff: dt.datetime) -> Prediction:
    """Una única predicción liquidada que pasa TODOS los requisitos de
    `evaluate_quality_gate_v3` salvo, potencialmente, la fiabilidad
    histórica (que depende de lo que exista en la BD en el momento de la
    evaluación) -- cuota real en banda, calibración, intervalo de
    incertidumbre estrecho con EV conservador positivo."""
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    comp = Competition(code=competition_code, name=competition_code, country="Testland")
    db.add(comp)
    db.flush()
    season = Season(competition_id=comp.id, label="2024-2025")
    db.add(season)
    model_version = ModelVersion(
        name="wf-v3-target", version="v1", market_family="goals",
        features=[], hyperparameters={}, metrics={}, dataset_version="test",
    )
    db.add(model_version)
    db.flush()
    match = Match(
        provider="wf_v3_target_test", provider_id=f"wf-v3-target-{competition_code}",
        competition_id=comp.id, season_id=season.id, kickoff_utc=kickoff,
        home_team_id=1, away_team_id=2, status="finished", home_goals=0, away_goals=1,
    )
    db.add(match)
    db.flush()
    # La cuota es "reciente" respecto al propio `kickoff` (nunca respecto
    # al reloj real) -- `compare_ranking_strategies_walk_forward` pasa
    # `now=kickoff` a `evaluate_quality_gate_v3` precisamente para que
    # esto se mida asi (ver fix real en prediction/ranking.py).
    prediction = Prediction(
        match_id=match.id, market=market, line=None, selection="away",
        model_version_id=model_version.id, model_probability=0.7, calibrated_probability=0.7,
        uncertainty_low=0.7, uncertainty_high=0.7, fair_odds=1 / 0.7,
        market_probability=0.55, market_odds=1.5, bookmakers_used=3,
        confidence=0.8, data_quality=0.8, explanation={}, features_used={},
        odds_origin_at=kickoff - dt.timedelta(minutes=20),
        created_at=kickoff - dt.timedelta(minutes=15),
    )
    db.add(prediction)
    db.flush()
    db.add(
        PredictionResult(prediction_id=prediction.id, outcome=True, settled_at=kickoff + dt.timedelta(hours=2))
    )
    db.flush()
    return prediction


def _filler_pairs(n: int, base: dt.datetime) -> list[tuple[Prediction, PredictionResult, dt.datetime]]:
    """Predicciones sinteticas SIN BD (igual que el resto de este
    fichero) solo para superar `min_train_n` -- v3 las excluye enseguida
    (sin `calibrated_probability`), asi que nunca contaminan su pool ni
    su track record (que viene exclusivamente de `db` en estos tests)."""
    return [
        (_prediction(-(i + 1), edge=0.05, market_odds=1.5), _result(i % 2 == 0), base + dt.timedelta(days=i))
        for i in range(n)
    ]


def test_compare_ranking_strategies_walk_forward_with_db_uses_production_hierarchy_for_v3():
    """Bug real corregido (auditoria v2.8): sin `db`, el track record de
    v3 solo se reconstruye EXACTO (sin jerarquia tier/global), asi que un
    (competicion, mercado) sin 30 liquidaciones PROPIAS nunca entra en
    "Mejores oportunidades" en este comparador, aunque SI tenga respaldo
    real de otras competiciones -- exactamente lo que le pasaba a v3
    contra la BD de produccion real (0 filas). Con `db`, usa la misma
    jerarquia real que produccion (`market_track_records_with_provenance`)
    y el candidato SI entra."""
    target_kickoff = dt.datetime(2024, 6, 1)
    backup_kickoffs = [target_kickoff - dt.timedelta(days=d) for d in range(5, 5 + 35)]

    with session_scope() as db:
        _seed_backup_settlements(db, "wf-v3-hierarchy-backup", "away_win", backup_kickoffs)
        target = _seed_v3_eligible_target(db, "wf-v3-hierarchy-target", "away_win", target_kickoff)
        target_id = target.id

    with session_scope() as db:
        target = db.get(Prediction, target_id)
        settled = _filler_pairs(30, target_kickoff - dt.timedelta(days=60)) + [
            (target, target.result, target_kickoff)
        ]

        report_without_db = compare_ranking_strategies_walk_forward(settled, min_train_n=30)
        report_with_db = compare_ranking_strategies_walk_forward(settled, min_train_n=30, db=db)

    assert V3_STRATEGY_NAME not in {s.strategy for s in report_without_db.slices}
    v3_slices_with_db = [s for s in report_with_db.slices if s.strategy == V3_STRATEGY_NAME]
    assert v3_slices_with_db
    assert any(s.n_bets == 1 for s in v3_slices_with_db)


def test_compare_ranking_strategies_walk_forward_track_record_never_sees_future_backup():
    """Misma fijacion, verificada al reves: si el respaldo GLOBAL solo
    tiene liquidaciones suficientes CONTANDO partidos posteriores al
    punto evaluado, `as_of` debe seguir excluyendolas -- nunca se filtra
    con el historico completo, solo con lo anterior en el tiempo (mismo
    principio anti-leakage que `edge_curve`/`longshot_curve`)."""
    target_kickoff = dt.datetime(2024, 6, 1)
    # Solo 10 liquidaciones ANTES del partido evaluado (insuficiente,
    # <30) + 25 DESPUES (que sumarian 35, suficiente, SI se contaran mal).
    backup_kickoffs = (
        [target_kickoff - dt.timedelta(days=d) for d in range(5, 15)]
        + [target_kickoff + dt.timedelta(days=d) for d in range(5, 30)]
    )

    with session_scope() as db:
        _seed_backup_settlements(db, "wf-v3-leakage-backup", "draw", backup_kickoffs)
        target = _seed_v3_eligible_target(db, "wf-v3-leakage-target", "draw", target_kickoff)
        target_id = target.id

    with session_scope() as db:
        target = db.get(Prediction, target_id)
        settled = _filler_pairs(30, target_kickoff - dt.timedelta(days=60)) + [
            (target, target.result, target_kickoff)
        ]
        report = compare_ranking_strategies_walk_forward(settled, min_train_n=30, db=db)

    v3_slices = [s for s in report.slices if s.strategy == V3_STRATEGY_NAME]
    assert not v3_slices or all(s.n_bets == 0 for s in v3_slices)
