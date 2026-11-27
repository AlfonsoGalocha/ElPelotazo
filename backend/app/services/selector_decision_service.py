"""Fase E de la revision v2.8 (Fase 10 del brief original): aprendizaje
del selector sobre sus propias decisiones.

Dos piezas: `capture_selector_decisions` (congela, para cada partido
proximo, TODO el universo de candidatas/excluidas que "Mejores
oportunidades" ve AHORA -- ver db/models/modeling.py::
SelectorDecisionSnapshot) y `evaluate_selector_decisions` (evaluacion
POST-HOC una vez que esos partidos liquidan, comparando contra el
resultado real).

Limite honesto, documentado tambien en la propia tabla: SIN BACKFILL. Las
decisiones de antes de que esta tabla existiera no se pueden reconstruir
fielmente (el `RankingConfig`/track record de aquel momento no se
preservo en ningun sitio) -- la evaluacion es puramente prospectiva desde
que esto se despliega, nunca retroactiva.

Aislamiento deliberado: este modulo NUNCA lee ni escribe `bot_decisions`,
`bot_trial_decisions`, `bot_trial_cuts`, `bot_manual_tickets`,
`strategy_captures`/`strategy_scans` (bot capital y las 8 estrategias del
Laboratorio son un sistema totalmente independiente, ver
prediction/ranking.py vs bot_capital.py/strategy_shadow.py) ni modifica
`Prediction`/`PredictionResult` existentes -- solo lee ambas y escribe en
sus propias tablas nuevas."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy.orm import Session

from backend.app.config.settings import get_settings
from backend.app.models.calibration.metrics import brier_score, log_loss_score
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import (
    Prediction,
    PredictionResult,
    SelectorDecisionCandidate,
    SelectorDecisionSnapshot,
)
from backend.app.prediction.anomaly import ContradictionLevel, classify_contradiction, compute_anomaly_flags
from backend.app.prediction.candidate_policy import rank_candidates
from backend.app.prediction.evidence import MarketEvidence, build_market_evidence
from backend.app.prediction.opportunity_category import opportunity_category
from backend.app.prediction.ranking import (
    conservative_probability,
    ev_with_probability,
    evaluate_all_gate_reasons_v3,
    rank_signals_v3,
)
from backend.app.services.evaluation_service import market_track_records_with_provenance
from backend.app.services.ranking_config_service import get_opportunity_odds_band

SELECTOR_VERSION = "v3_probability_first"
# Ventana razonable (seccion 10 del brief: "para cada corte prepartido"):
# partidos que todavia no han empezado pero ya estan lo bastante cerca
# para que cuotas/datos sean representativos de la decision final. Mismo
# orden de magnitud que `publish_upcoming` (publication_service.py).
CAPTURE_WINDOW_HOURS = 48
# Misma cadencia que la Loop A del scheduler (donde vive `capture-
# strategies`, cada 600s): si este comando se invoca dos veces seguidas
# sin que haya pasado al menos este intervalo (p.ej. una superposicion
# accidental del cron), el segundo intento NO crea un snapshot nuevo para
# el mismo partido -- evita inundar la tabla con "cortes" casi identicos
# sin informacion nueva real. Mas alla de este intervalo, SI se inserta
# un snapshot nuevo cada vez (comportamiento deseado: ver sucesivos
# cortes a medida que cambian cuotas/datos).
MIN_RECAPTURE_INTERVAL_MINUTES = 10


def capture_selector_decisions(db: Session, now: dt.datetime | None = None) -> int:
    """Congela el universo completo de candidatas/excluidas de "Mejores
    oportunidades" (v3) para cada partido programado dentro de las
    proximas `CAPTURE_WINDOW_HOURS` horas. Devuelve cuantos snapshots
    NUEVOS se crearon (0 para un partido recapturado antes de
    `MIN_RECAPTURE_INTERVAL_MINUTES`, ver arriba)."""
    now = now or dt.datetime.utcnow()
    horizon = now + dt.timedelta(hours=CAPTURE_WINDOW_HOURS)
    settings = get_settings()
    min_odds, max_odds = get_opportunity_odds_band(db, settings)
    settings = settings.model_copy(
        update={"min_opportunity_odds": min_odds, "max_opportunity_odds": max_odds}
    )

    provenance = market_track_records_with_provenance(db)
    track_records = {key: estimate.value for key, estimate in provenance.items()}

    matches = (
        db.query(Match)
        .filter(Match.status == "scheduled", Match.kickoff_utc >= now, Match.kickoff_utc <= horizon)
        .all()
    )

    created = 0
    for match in matches:
        last_snapshot = (
            db.query(SelectorDecisionSnapshot)
            .filter(SelectorDecisionSnapshot.match_id == match.id)
            .order_by(SelectorDecisionSnapshot.captured_at.desc())
            .first()
        )
        if last_snapshot is not None and (now - last_snapshot.captured_at) < dt.timedelta(
            minutes=MIN_RECAPTURE_INTERVAL_MINUTES
        ):
            continue

        predictions = (
            db.query(Prediction)
            .filter(Prediction.match_id == match.id, ~Prediction.market.startswith("cards_"))
            .all()
        )
        if not predictions:
            continue

        included, excluded = rank_signals_v3(predictions, settings, track_records, one_per_match=False)
        anomaly_flags_by_id = {
            prediction.id: compute_anomaly_flags(prediction, predictions, settings) for prediction in predictions
        }

        # Evidencia + contradiccion: UNA sola vez por prediccion (v2.8.2),
        # reusada tanto para `category`/`contradiction_level` (ya existia,
        # antes se recalculaba dentro de `_add_candidate` por candidata)
        # como para la politica CANDIDATA nueva -- nunca se duplica el
        # calculo de `build_market_evidence` (coste medido en Fase D de
        # v2.8: ~13ms/prediccion, nada gratis).
        evidence_by_id: dict[int, MarketEvidence] = {}
        contradiction_by_id: dict[int, ContradictionLevel] = {}
        for prediction in predictions:
            evidence = build_market_evidence(db, prediction, match=match)
            evidence_by_id[prediction.id] = evidence
            contradiction_by_id[prediction.id] = classify_contradiction(prediction, evidence)

        # Politica candidata (seccion 4/8 del pedido v2.8.2): MISMO
        # universo, MISMO instante -- nunca un segundo corte en otro
        # momento, para que la comparacion v3-vs-candidata sea sobre
        # exactamente las mismas oportunidades.
        included_candidate, excluded_candidate = rank_candidates(
            predictions, contradiction_by_id, settings, track_records, one_per_match=False, now=now,
        )
        candidate_rank_by_id = {s.prediction.id: i for i, s in enumerate(included_candidate, start=1)}
        candidate_exclusion_by_id = {e.prediction.id: e.reason.value for e in excluded_candidate}
        selected_candidate_id = included_candidate[0].prediction.id if included_candidate else None

        snapshot = SelectorDecisionSnapshot(
            match_id=match.id,
            captured_at=now,
            selector_version=SELECTOR_VERSION,
            min_opportunity_odds=settings.min_opportunity_odds,
            max_opportunity_odds=settings.max_opportunity_odds,
            selected_prediction_id=included[0].prediction.id if included else None,
            selected_prediction_id_candidate=selected_candidate_id,
        )
        db.add(snapshot)
        db.flush()

        for rank_position, signal in enumerate(included, start=1):
            _add_candidate(
                db, snapshot, settings, signal.prediction, provenance, anomaly_flags_by_id,
                evidence_by_id, contradiction_by_id, candidate_rank_by_id, candidate_exclusion_by_id,
                rank_position=rank_position, exclusion_reason=None, is_selected=rank_position == 1,
            )
        for excluded_signal in excluded:
            _add_candidate(
                db, snapshot, settings, excluded_signal.prediction, provenance, anomaly_flags_by_id,
                evidence_by_id, contradiction_by_id, candidate_rank_by_id, candidate_exclusion_by_id,
                rank_position=None, exclusion_reason=excluded_signal.reason.value, is_selected=False,
            )
        db.flush()
        created += 1

    return created


def _add_candidate(
    db: Session, snapshot, settings, prediction, provenance, anomaly_flags_by_id,
    evidence_by_id, contradiction_by_id, candidate_rank_by_id, candidate_exclusion_by_id,
    *, rank_position: int | None, exclusion_reason: str | None, is_selected: bool,
) -> None:
    reliability = provenance.get((prediction.match.competition.code, prediction.market))
    evidence = evidence_by_id[prediction.id]
    contradiction_level = contradiction_by_id[prediction.id]
    anomaly_flags = anomaly_flags_by_id.get(prediction.id, [])
    category = opportunity_category(prediction, contradiction_level, reliability, anomaly_flags, settings)

    uncertainty_width = None
    if prediction.uncertainty_low is not None and prediction.uncertainty_high is not None:
        uncertainty_width = prediction.uncertainty_high - prediction.uncertainty_low

    rank_position_candidate = candidate_rank_by_id.get(prediction.id)

    db.add(
        SelectorDecisionCandidate(
            snapshot_id=snapshot.id,
            prediction_id=prediction.id,
            market=prediction.market,
            model_probability=prediction.model_probability,
            calibrated_probability=prediction.calibrated_probability,
            market_odds=prediction.market_odds,
            market_probability=prediction.market_probability,
            ev_conservative=ev_with_probability(conservative_probability(prediction), prediction.market_odds),
            market_track_record_value=reliability.value if reliability is not None else None,
            market_track_record_source_level=reliability.source_level if reliability is not None else None,
            uncertainty_width=uncertainty_width,
            data_quality=prediction.data_quality,
            edge=prediction.edge,
            category=category.value,
            contradiction_level=contradiction_level.value,
            rank_position=rank_position,
            exclusion_reason=exclusion_reason,
            is_selected=is_selected,
            rank_position_candidate=rank_position_candidate,
            exclusion_reason_candidate=candidate_exclusion_by_id.get(prediction.id),
            is_selected_candidate=rank_position_candidate == 1,
        )
    )


# v2.8.2 (seccion 8 del pedido): nunca se declara una comparacion
# decidida con menos liquidaciones que esto para NINGUNA de las dos
# politicas -- mismo orden de magnitud que `MIN_SETTLED_FOR_TRACK_RECORD`
# (evaluation_service.py) y `MIN_CALIBRATION_MATCHES` (model_service.py):
# el criterio ya establecido en todo el proyecto para "muestra minima
# antes de confiar en una metrica", reusado aqui, no inventado de nuevo.
MIN_SETTLED_FOR_POLICY_COMPARISON = 30


@dataclass(frozen=True)
class SelectorLearningBucket:
    key: str
    n: int
    win_rate: float | None


@dataclass(frozen=True)
class SelectorLearningReport:
    n_snapshots_total: int
    n_snapshots_settled: int
    n_snapshots_pending: int
    selected_n: int
    selected_win_rate: float | None
    # Snapshots donde la seleccionada perdio pero OTRA candidata (peor
    # posicion o excluida) SI ganaba -- el caso real que esta fase quiere
    # medir: ¿el selector esta sistematicamente dejando fuera opciones
    # mejores?
    missed_opportunities_n: int
    missed_opportunities_rate: float | None
    by_rank_position: list[SelectorLearningBucket] = field(default_factory=list)
    by_category: list[SelectorLearningBucket] = field(default_factory=list)


def evaluate_selector_decisions(db: Session) -> SelectorLearningReport:
    """Evaluacion POST-HOC (seccion 10 del brief): para cada
    `SelectorDecisionSnapshot`, compara contra el resultado real YA
    LIQUIDADO (si existe) -- nunca reconstruye nada con informacion que
    no existia en `captured_at` (eso ya lo congelo la propia tabla al
    capturar). Puramente prospectivo, sin backfill (ver docstring de
    `SelectorDecisionSnapshot`): un partido sin snapshot capturado nunca
    entra aqui, por muy liquidado que este."""
    snapshots = db.query(SelectorDecisionSnapshot).all()
    n_total = len(snapshots)

    rank_bucket_hits: dict[str, list[bool]] = defaultdict(list)
    category_bucket_hits: dict[str, list[bool]] = defaultdict(list)
    selected_outcomes: list[bool] = []
    missed = 0
    n_settled = 0
    n_with_selected_settled = 0

    for snapshot in snapshots:
        candidates = snapshot.candidates
        if not candidates:
            continue
        prediction_ids = [candidate.prediction_id for candidate in candidates]
        results = {
            result.prediction_id: result
            for result in db.query(PredictionResult).filter(PredictionResult.prediction_id.in_(prediction_ids)).all()
        }

        any_settled = False
        selected_won: bool | None = None
        other_won = False
        for candidate in candidates:
            result = results.get(candidate.prediction_id)
            if result is None or result.settlement_status == "void":
                continue
            any_settled = True
            won = bool(result.outcome)
            rank_key = str(candidate.rank_position) if candidate.rank_position is not None else "excluded"
            rank_bucket_hits[rank_key].append(won)
            category_bucket_hits[candidate.category].append(won)
            if candidate.is_selected:
                selected_won = won
                selected_outcomes.append(won)
            elif won:
                other_won = True

        if any_settled:
            n_settled += 1
        if selected_won is not None:
            n_with_selected_settled += 1
            if not selected_won and other_won:
                missed += 1

    def _finalize(buckets: dict[str, list[bool]]) -> list[SelectorLearningBucket]:
        return [
            SelectorLearningBucket(key=key, n=len(hits), win_rate=(sum(hits) / len(hits)) if hits else None)
            for key, hits in sorted(buckets.items())
        ]

    return SelectorLearningReport(
        n_snapshots_total=n_total,
        n_snapshots_settled=n_settled,
        n_snapshots_pending=n_total - n_settled,
        selected_n=len(selected_outcomes),
        selected_win_rate=(sum(selected_outcomes) / len(selected_outcomes)) if selected_outcomes else None,
        missed_opportunities_n=missed,
        missed_opportunities_rate=(missed / n_with_selected_settled) if n_with_selected_settled else None,
        by_rank_position=_finalize(rank_bucket_hits),
        by_category=_finalize(category_bucket_hits),
    )


@dataclass(frozen=True)
class PolicyMetrics:
    """Metricas de UNA politica (v3 o la candidata) sobre sus propias
    decisiones ya liquidadas -- seccion 8 del pedido v2.8.2."""

    policy: str  # "v3_probability_first" | "v4_contradiction_aware"
    n_snapshots_with_selection: int
    coverage: float | None  # fraccion de snapshots liquidados con ALGUNA seleccion
    top1_win_rate: float | None
    top3_hit_rate: float | None  # acerto alguna de las 3 mejor posicionadas, no solo la elegida
    brier_score: float | None
    log_loss: float | None
    roi_flat_stake: float | None  # 1 unidad por seleccion, a la cuota real de esa prediccion
    market_distribution: dict[str, int] = field(default_factory=dict)
    contradiction_distribution: dict[str, int] = field(default_factory=dict)
    insufficient_sample: bool = True


@dataclass(frozen=True)
class WeeklyStabilityPoint:
    week_start: str  # fecha ISO (lunes) de la semana de `captured_at`
    policy: str
    n: int
    win_rate: float | None


@dataclass(frozen=True)
class PolicyComparisonReport:
    n_snapshots_total: int
    n_snapshots_settled: int
    v3: PolicyMetrics
    candidate: PolicyMetrics
    weekly_stability: list[WeeklyStabilityPoint] = field(default_factory=list)
    # Mensaje honesto y explicito (pedido real: "no declares una mejora
    # demostrada con muestras pequeñas") -- nunca se omite ni se suaviza.
    note: str = ""


def _policy_metrics(
    policy_name: str,
    selected_rows: list[dict],
    top3_hits: list[bool],
    n_settled_snapshots: int,
) -> PolicyMetrics:
    """`selected_rows`: un dict por snapshot liquidado DONDE la politica
    selecciono algo, con `won` (bool), `model_probability`,
    `calibrated_probability`, `market_odds`, `market`,
    `contradiction_level`. `top3_hits`: un bool por snapshot liquidado
    (independientemente de si la politica selecciono algo), True si
    alguna de sus 3 mejor posicionadas gano."""
    n = len(selected_rows)
    coverage = (n / n_settled_snapshots) if n_settled_snapshots else None

    market_distribution: dict[str, int] = defaultdict(int)
    contradiction_distribution: dict[str, int] = defaultdict(int)
    for row in selected_rows:
        market_distribution[row["market"]] += 1
        if row["contradiction_level"] is not None:
            contradiction_distribution[row["contradiction_level"]] += 1

    wins = [row["won"] for row in selected_rows]
    top1_win_rate = (sum(wins) / n) if n else None
    top3_hit_rate = (sum(top3_hits) / len(top3_hits)) if top3_hits else None

    probabilities = [
        row["calibrated_probability"] if row["calibrated_probability"] is not None else row["model_probability"]
        for row in selected_rows
        if row["model_probability"] is not None
        or row["calibrated_probability"] is not None
    ]
    outcomes_for_prob = [
        row["won"] for row in selected_rows
        if row["model_probability"] is not None or row["calibrated_probability"] is not None
    ]
    brier = log_loss = None
    if probabilities:
        y_true = np.array(outcomes_for_prob, dtype=bool)
        y_prob = np.array(probabilities, dtype=float)
        brier = brier_score(y_true, y_prob)
        log_loss = log_loss_score(y_true, y_prob)

    odds_rows = [row for row in selected_rows if row["market_odds"] is not None]
    roi = None
    if odds_rows:
        pnl = sum((row["market_odds"] - 1.0) if row["won"] else -1.0 for row in odds_rows)
        roi = pnl / len(odds_rows)

    return PolicyMetrics(
        policy=policy_name,
        n_snapshots_with_selection=n,
        coverage=coverage,
        top1_win_rate=top1_win_rate,
        top3_hit_rate=top3_hit_rate,
        brier_score=brier,
        log_loss=log_loss,
        roi_flat_stake=roi,
        market_distribution=dict(market_distribution),
        contradiction_distribution=dict(contradiction_distribution),
        insufficient_sample=n < MIN_SETTLED_FOR_POLICY_COMPARISON,
    )


def evaluate_policy_comparison(db: Session) -> PolicyComparisonReport:
    """Compara v3 ("Mejores oportunidades") contra la politica candidata
    consciente de contradicciones (`prediction/candidate_policy.py`)
    SOBRE LAS MISMAS oportunidades -- ambas se registran en el mismo
    `capture_selector_decisions` (seccion 4/7/8 del pedido v2.8.2).
    Puramente prospectivo (mismo limite que `evaluate_selector_decisions`,
    sin backfill): solo cuenta snapshots capturados DESPUES de que las
    columnas `*_candidate` existieran. Nunca declara una politica mejor
    con menos de `MIN_SETTLED_FOR_POLICY_COMPARISON` selecciones
    liquidadas -- `insufficient_sample` queda explicito en el resultado,
    nunca se omite."""
    snapshots = db.query(SelectorDecisionSnapshot).all()
    n_total = len(snapshots)

    v3_rows: list[dict] = []
    candidate_rows: list[dict] = []
    v3_top3_hits: list[bool] = []
    candidate_top3_hits: list[bool] = []
    weekly: dict[tuple[str, str], list[bool]] = defaultdict(list)
    n_settled_snapshots = 0

    for snapshot in snapshots:
        candidates = snapshot.candidates
        if not candidates:
            continue
        prediction_ids = [c.prediction_id for c in candidates]
        results = {
            r.prediction_id: r
            for r in db.query(PredictionResult).filter(PredictionResult.prediction_id.in_(prediction_ids)).all()
        }
        settled_candidates = [
            c for c in candidates
            if (r := results.get(c.prediction_id)) is not None and r.settlement_status != "void"
        ]
        if not settled_candidates:
            continue
        n_settled_snapshots += 1
        week_start = (snapshot.captured_at - dt.timedelta(days=snapshot.captured_at.weekday())).date().isoformat()

        for policy, rank_field, selected_field, rows, top3_hits in (
            ("v3_probability_first", "rank_position", "is_selected", v3_rows, v3_top3_hits),
            ("v4_contradiction_aware", "rank_position_candidate", "is_selected_candidate", candidate_rows, candidate_top3_hits),
        ):
            top3 = [c for c in settled_candidates if (getattr(c, rank_field) or 99) <= 3]
            top3_hits.append(any(bool(results[c.prediction_id].outcome) for c in top3))
            selected = next((c for c in settled_candidates if getattr(c, selected_field)), None)
            if selected is not None:
                won = bool(results[selected.prediction_id].outcome)
                rows.append(
                    {
                        "won": won, "market": selected.market,
                        "model_probability": selected.model_probability,
                        "calibrated_probability": selected.calibrated_probability,
                        "market_odds": selected.market_odds,
                        "contradiction_level": selected.contradiction_level,
                    }
                )
                weekly[(week_start, policy)].append(won)

    v3_metrics = _policy_metrics("v3_probability_first", v3_rows, v3_top3_hits, n_settled_snapshots)
    candidate_metrics = _policy_metrics("v4_contradiction_aware", candidate_rows, candidate_top3_hits, n_settled_snapshots)

    weekly_points = [
        WeeklyStabilityPoint(
            week_start=week, policy=policy, n=len(hits), win_rate=(sum(hits) / len(hits)) if hits else None
        )
        for (week, policy), hits in sorted(weekly.items())
    ]

    if v3_metrics.insufficient_sample or candidate_metrics.insufficient_sample:
        note = (
            f"Muestra insuficiente para comparar politicas con confianza: "
            f"v3 tiene {v3_metrics.n_snapshots_with_selection} selecciones liquidadas, "
            f"la candidata {candidate_metrics.n_snapshots_with_selection} "
            f"(minimo exigido: {MIN_SETTLED_FOR_POLICY_COMPARISON}). "
            "No se declara ninguna politica mejor todavia."
        )
    else:
        note = "Muestra suficiente para comparar -- ver metricas; esto NO promueve la politica candidata automaticamente."

    return PolicyComparisonReport(
        n_snapshots_total=n_total,
        n_snapshots_settled=n_settled_snapshots,
        v3=v3_metrics,
        candidate=candidate_metrics,
        weekly_stability=weekly_points,
        note=note,
    )


def funnel_report(db: Session, now: dt.datetime | None = None) -> dict:
    """Embudo real de seleccion v3 (v2.8.2, seccion 6 del pedido): usa el
    SNAPSHOT MAS RECIENTE de cada partido ya capturado (nunca inventa
    candidatas nuevas, nunca recalcula el ranking entero). Dos conteos
    por motivo:

    - `accumulated_reasons`: lo que YA guarda cada
      `SelectorDecisionCandidate.exclusion_reason` (la cascada real que
      vio el selector -- el PRIMER motivo que bloqueo cada candidata).
    - `individual_reasons`: recalculado aqui con
      `evaluate_all_gate_reasons_v3` sobre la `Prediction` real -- TODOS
      los motivos que aplicarian a cada candidata a la vez, para poder
      responder "cuanto pesa cada filtro de verdad" (p.ej. si
      'calibracion_no_disponible' domina los corners incluso para
      candidatas que ya se excluyeron antes por otro motivo).

    Nunca relaja ningun umbral -- esto es puramente de lectura/diagnostico."""
    now = now or dt.datetime.utcnow()
    settings = get_settings()
    track_records = {
        key: estimate.value for key, estimate in market_track_records_with_provenance(db).items()
    }

    snapshots = db.query(SelectorDecisionSnapshot).all()
    latest_by_match: dict[int, SelectorDecisionSnapshot] = {}
    for snapshot in snapshots:
        current = latest_by_match.get(snapshot.match_id)
        if current is None or snapshot.captured_at > current.captured_at:
            latest_by_match[snapshot.match_id] = snapshot

    accumulated_reasons: Counter[str] = Counter()
    individual_reasons: Counter[str] = Counter()
    n_candidates_total = 0
    n_passed_all_checks = 0

    for snapshot in latest_by_match.values():
        for candidate in snapshot.candidates:
            n_candidates_total += 1
            if candidate.exclusion_reason is not None:
                accumulated_reasons[candidate.exclusion_reason] += 1

            prediction = db.get(Prediction, candidate.prediction_id)
            if prediction is None or prediction.match is None:
                continue
            track_record = track_records.get((prediction.match.competition.code, prediction.market))
            reasons = evaluate_all_gate_reasons_v3(prediction, settings, track_record, now=now)
            if not reasons:
                n_passed_all_checks += 1
            for reason in reasons:
                individual_reasons[reason.value] += 1

    return {
        "n_matches": len(latest_by_match),
        "n_candidates_total": n_candidates_total,
        "n_passed_all_checks": n_passed_all_checks,
        "accumulated_reasons": dict(accumulated_reasons),
        "individual_reasons": dict(individual_reasons),
    }
