"""`backtesting/calibration_audit.py` (v2.8.2, seccion 2): auditoria real
de sobreconfianza que explico el ECE ponderado de 0,245 encontrado en
produccion sobre las 1.203 predicciones pre-partido genuinas liquidadas.

Mismo patron de siembra directa (Prediction+PredictionResult, sin pasar
por el pipeline completo) que `test_evaluation_service.py::_seed_settled_predictions`,
y mismas convenciones (`session_scope()` real, codigos de competicion/
provider_id unicos por test para no colisionar con el resto de la suite
compartida)."""

from __future__ import annotations

import datetime as dt

from backend.app.backtesting.calibration_audit import calibration_audit_report
from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction, PredictionResult
from backend.app.normalization.competitions import COMPETITIONS


def _seed(db, competition_code: str, rows: list[dict], tag: str) -> None:
    """`rows`: cada uno con market, model_probability, calibrated_probability
    (opcional), outcome, y opcionalmente match_id (para agrupar varios
    mercados en el MISMO partido -- necesario para el test de
    monotonicidad entre lineas), created_offset_minutes (negativo = antes
    del pitido, por defecto -15; positivo = reconstruida DESPUES)."""
    COMPETITIONS.setdefault(competition_code, (competition_code, "Testland"))
    comp = db.query(Competition).filter_by(code=competition_code).one_or_none()
    if comp is None:
        comp = Competition(code=competition_code, name=competition_code, country="Testland")
        db.add(comp)
        db.flush()
        db.add(Season(competition_id=comp.id, label="2024-2025"))
        db.flush()
    season = db.query(Season).filter_by(competition_id=comp.id).one()
    model_version = ModelVersion(
        name="calibration-audit-test", version=f"v-{tag}", market_family="goals",
        features=[], hyperparameters={}, metrics={}, dataset_version="test",
    )
    db.add(model_version)
    db.flush()

    matches_by_key: dict[str, Match] = {}
    for i, row in enumerate(rows):
        match_key = row.get("match_key", f"{tag}-{i}")
        if match_key not in matches_by_key:
            kickoff = dt.datetime.utcnow() - dt.timedelta(days=1)
            match = Match(
                provider="calibration_audit_test", provider_id=f"cal-audit-{tag}-{match_key}",
                competition_id=comp.id, season_id=season.id, kickoff_utc=kickoff,
                home_team_id=1, away_team_id=2, status="finished", home_goals=1, away_goals=0,
            )
            db.add(match)
            db.flush()
            matches_by_key[match_key] = match
        match = matches_by_key[match_key]
        created_offset = row.get("created_offset_minutes", -15)
        prediction = Prediction(
            match_id=match.id, market=row["market"], line=2.5, selection="over",
            model_version_id=model_version.id, model_probability=row["model_probability"],
            calibrated_probability=row.get("calibrated_probability"),
            fair_odds=1 / row["model_probability"], confidence=0.8, data_quality=0.8,
            explanation={}, features_used={},
            created_at=match.kickoff_utc + dt.timedelta(minutes=created_offset),
        )
        db.add(prediction)
        db.flush()
        db.add(PredictionResult(prediction_id=prediction.id, outcome=row["outcome"], settled_at=dt.datetime.utcnow()))
    db.flush()


def test_detects_injected_overconfidence_with_correct_sign():
    """Sobreconfianza inyectada deliberadamente: probabilidad SIEMPRE 0,20
    por encima de la frecuencia real (0,75 predicho vs 0,55 real, n=40,
    22 aciertos/18 fallos exactos) -- debe detectarse como
    "sobreconfianza", nunca "calibrado" ni "infraconfianza"."""
    competition_code = "cal-audit-overconfidence"
    rows = [
        {"market": "over_2_5", "model_probability": 0.75, "outcome": i < 22}
        for i in range(40)
    ]
    with session_scope() as db:
        _seed(db, competition_code, rows, tag="overconf")

    with session_scope() as db:
        report = calibration_audit_report(db, competition_code=competition_code)

    segment = next(s for s in report.segments if s.market == "over_2_5")
    bucket = next(
        b for b in segment.buckets
        if b.probability_column == "model_probability" and b.probability_range == "70-80%"
    )
    assert bucket.n == 40
    assert bucket.classification == "sobreconfianza"
    assert bucket.difference > 0.15


def test_1x2_markets_are_never_pooled_into_one_calibration_array():
    """home_win/draw/away_win son 3 mercados BINARIOS independientes
    (market_labels.py) -- cada uno debe llegar como su PROPIO segmento,
    nunca mezclados en un unico array de calibracion. Se siembran con
    comportamientos opuestos (home_win bien calibrado, draw muy
    sobreconfiado) en el MISMO rango de probabilidad: si se mezclaran,
    el sesgo de uno contaminaria la metrica del otro."""
    competition_code = "cal-audit-1x2-no-pooling"
    rows = (
        # home_win: predicho 0.75, real 0.75 (18/24) -- bien calibrado.
        [{"market": "home_win", "model_probability": 0.75, "outcome": i < 18} for i in range(24)]
        # draw: predicho 0.75, real 0.10 (3/30) -- muy sobreconfiado.
        + [{"market": "draw", "model_probability": 0.75, "outcome": i < 3} for i in range(30)]
    )
    with session_scope() as db:
        _seed(db, competition_code, rows, tag="1x2")

    with session_scope() as db:
        report = calibration_audit_report(db, competition_code=competition_code)

    home_bucket = next(
        b for s in report.segments if s.market == "home_win" for b in s.buckets
        if b.probability_column == "model_probability" and b.probability_range == "70-80%"
    )
    draw_bucket = next(
        b for s in report.segments if s.market == "draw" for b in s.buckets
        if b.probability_column == "model_probability" and b.probability_range == "70-80%"
    )
    assert home_bucket.classification == "calibrado"
    assert draw_bucket.classification == "sobreconfianza"
    # Si se hubieran mezclado, la frecuencia real combinada seria una sola
    # cifra para ambos -- aqui deben ser claramente distintas.
    assert abs(home_bucket.empirical_frequency - draw_bucket.empirical_frequency) > 0.3


def test_reconstructed_post_kickoff_predictions_are_excluded():
    """Mismo filtro que `/models/performance` (`stored_pre_kickoff`):
    predicciones creadas EN O DESPUES del pitido (reconstruidas) nunca
    deben contar, aunque las haya de sobra -- si entraran, un bloque de
    30 "reconstruidas" con 100% de acierto se mezclaria con las 25
    genuinas y ocultaria la sobreconfianza real."""
    competition_code = "cal-audit-post-kickoff-excluded"
    genuine = [
        {"market": "over_2_5", "model_probability": 0.75, "outcome": i < 11, "created_offset_minutes": -15}
        for i in range(25)
    ]
    reconstructed = [
        {"market": "over_2_5", "model_probability": 0.75, "outcome": True, "created_offset_minutes": 120}
        for _ in range(30)
    ]
    with session_scope() as db:
        _seed(db, competition_code, genuine + reconstructed, tag="postkickoff")

    with session_scope() as db:
        report = calibration_audit_report(db, competition_code=competition_code)

    segment = next(s for s in report.segments if s.market == "over_2_5")
    bucket = next(
        b for b in segment.buckets
        if b.probability_column == "model_probability" and b.probability_range == "70-80%"
    )
    # Solo las 25 genuinas -- nunca 55.
    assert bucket.n == 25
    assert bucket.classification == "sobreconfianza"


def test_original_vs_calibrated_are_audited_separately():
    """Comparacion pedida explicitamente: la MISMA prediccion puede estar
    sobreconfiada en `model_probability` pero bien calibrada en
    `calibrated_probability` (el calibrador hizo su trabajo) -- ambas
    columnas deben auditarse por separado, nunca una sola mezcla."""
    competition_code = "cal-audit-original-vs-calibrated"
    rows = [
        {"market": "over_2_5", "model_probability": 0.75, "calibrated_probability": 0.55, "outcome": i < 22}
        for i in range(40)
    ]
    with session_scope() as db:
        _seed(db, competition_code, rows, tag="origvscal")

    with session_scope() as db:
        report = calibration_audit_report(db, competition_code=competition_code)

    segment = next(s for s in report.segments if s.market == "over_2_5")
    original_bucket = next(
        b for b in segment.buckets if b.probability_column == "model_probability" and b.probability_range == "70-80%"
    )
    calibrated_bucket = next(
        b for b in segment.buckets if b.probability_column == "calibrated_probability" and b.probability_range == "50-60%"
    )
    assert original_bucket.classification == "sobreconfianza"
    assert calibrated_bucket.classification == "calibrado"


def test_nested_line_violation_is_counted_on_both_siblings():
    """Caso real (Stuttgart-Frankfurt): `over_3_5` calibrada mayor que
    `over_2_5` calibrada del MISMO partido -- debe contarse como
    violacion en el bucket de AMBAS predicciones implicadas, nunca solo
    en una."""
    competition_code = "cal-audit-nested-violation"
    rows = [
        {"market": "over_1_5", "model_probability": 0.70, "calibrated_probability": 0.84, "outcome": True, "match_key": "m1"},
        {"market": "over_2_5", "model_probability": 0.60, "calibrated_probability": 0.64, "outcome": True, "match_key": "m1"},
        {"market": "over_3_5", "model_probability": 0.72, "calibrated_probability": 0.85, "outcome": True, "match_key": "m1"},
    ]
    with session_scope() as db:
        _seed(db, competition_code, rows, tag="nested")

    with session_scope() as db:
        report = calibration_audit_report(db, competition_code=competition_code)

    over_2_5_bucket = next(
        b for s in report.segments if s.market == "over_2_5" for b in s.buckets
        if b.probability_column == "calibrated_probability"
    )
    over_3_5_bucket = next(
        b for s in report.segments if s.market == "over_3_5" for b in s.buckets
        if b.probability_column == "calibrated_probability"
    )
    assert over_2_5_bucket.n_with_monotonicity_violation == 1
    assert over_3_5_bucket.n_with_monotonicity_violation == 1
