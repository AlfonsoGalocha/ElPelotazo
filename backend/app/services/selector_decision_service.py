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
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.app.config.settings import get_settings
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import (
    Prediction,
    PredictionResult,
    SelectorDecisionCandidate,
    SelectorDecisionSnapshot,
)
from backend.app.prediction.anomaly import classify_contradiction, compute_anomaly_flags
from backend.app.prediction.evidence import build_market_evidence
from backend.app.prediction.opportunity_category import opportunity_category
from backend.app.prediction.ranking import (
    conservative_probability,
    ev_with_probability,
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

        snapshot = SelectorDecisionSnapshot(
            match_id=match.id,
            captured_at=now,
            selector_version=SELECTOR_VERSION,
            min_opportunity_odds=settings.min_opportunity_odds,
            max_opportunity_odds=settings.max_opportunity_odds,
            selected_prediction_id=included[0].prediction.id if included else None,
        )
        db.add(snapshot)
        db.flush()

        for rank_position, signal in enumerate(included, start=1):
            _add_candidate(
                db, snapshot, settings, signal.prediction, provenance, anomaly_flags_by_id,
                rank_position=rank_position, exclusion_reason=None, is_selected=rank_position == 1,
            )
        for excluded_signal in excluded:
            _add_candidate(
                db, snapshot, settings, excluded_signal.prediction, provenance, anomaly_flags_by_id,
                rank_position=None, exclusion_reason=excluded_signal.reason.value, is_selected=False,
            )
        db.flush()
        created += 1

    return created


def _add_candidate(
    db: Session, snapshot, settings, prediction, provenance, anomaly_flags_by_id,
    *, rank_position: int | None, exclusion_reason: str | None, is_selected: bool,
) -> None:
    reliability = provenance.get((prediction.match.competition.code, prediction.market))
    evidence = build_market_evidence(db, prediction)
    contradiction_level = classify_contradiction(prediction, evidence)
    anomaly_flags = anomaly_flags_by_id.get(prediction.id, [])
    category = opportunity_category(prediction, contradiction_level, reliability, anomaly_flags, settings)

    uncertainty_width = None
    if prediction.uncertainty_low is not None and prediction.uncertainty_high is not None:
        uncertainty_width = prediction.uncertainty_high - prediction.uncertainty_low

    db.add(
        SelectorDecisionCandidate(
            snapshot_id=snapshot.id,
            prediction_id=prediction.id,
            market=prediction.market,
            calibrated_probability=prediction.calibrated_probability,
            ev_conservative=ev_with_probability(conservative_probability(prediction), prediction.market_odds),
            market_track_record_value=reliability.value if reliability is not None else None,
            market_track_record_source_level=reliability.source_level if reliability is not None else None,
            uncertainty_width=uncertainty_width,
            data_quality=prediction.data_quality,
            edge=prediction.edge,
            category=category.value,
            rank_position=rank_position,
            exclusion_reason=exclusion_reason,
            is_selected=is_selected,
        )
    )


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
