from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, aliased

from backend.app.api.routes.predictions import serialize_prediction
from backend.app.db.database import get_db
from backend.app.db.models.core import Competition, Team
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import best_prediction_per_match, compute_anomaly_flags
from backend.app.schemas.match import MatchFormOut, MatchOut
from backend.app.schemas.prediction import PredictionOut

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=list[MatchOut])
def list_matches(
    competition_code: str | None = None,
    date_from: dt.date | None = Query(None),
    date_to: dt.date | None = Query(None),
    search: str | None = Query(None, description="Busca por nombre de equipo (local o visitante)"),
    status: str | None = Query(None, description="'scheduled' o 'finished'"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[Match]:
    query = db.query(Match)
    if competition_code:
        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        if competition is None:
            return []
        query = query.filter(Match.competition_id == competition.id)
    if date_from:
        query = query.filter(Match.kickoff_utc >= dt.datetime.combine(date_from, dt.time.min))
    if date_to:
        query = query.filter(Match.kickoff_utc <= dt.datetime.combine(date_to, dt.time.max))
    if status:
        query = query.filter(Match.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        HomeTeam, AwayTeam = aliased(Team), aliased(Team)
        query = (
            query.join(HomeTeam, Match.home_team_id == HomeTeam.id)
            .join(AwayTeam, Match.away_team_id == AwayTeam.id)
            .filter(or_(HomeTeam.canonical_name.ilike(pattern), AwayTeam.canonical_name.ilike(pattern)))
        )
        # Sin filtro de fecha explicito, lo mas util al buscar es lo mas
        # PROXIMO a hoy (pasado o futuro), no el partido mas antiguo del
        # historico (que es lo que daria un simple ORDER BY kickoff_utc ASC).
        # Se ordena en Python (no en SQL) para no depender de funciones de
        # fecha especificas de un motor de BD concreto (SQLite vs Postgres).
        if not date_from and not date_to:
            now = dt.datetime.utcnow()
            candidates = query.all()
            candidates.sort(key=lambda m: abs((m.kickoff_utc - now).total_seconds()))
            return candidates[:limit]
    return query.order_by(Match.kickoff_utc).limit(limit).all()


@router.get("/fixtures/finished", response_model=list[MatchOut])
def finished_fixtures(
    competition_code: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[Match]:
    """Fase 4: partidos FINALIZADOS (`Match.status == "finished"`, nunca un
    filtro por fecha -- un partido programado para ayer que no se jugo
    todavia por aplazamiento sigue sin ser "finalizado"). Orden: mas
    reciente primero, para que el Historico muestre lo ultimo jugado
    arriba."""
    query = db.query(Match).filter(Match.status == "finished")
    if competition_code:
        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        if competition is None:
            return []
        query = query.filter(Match.competition_id == competition.id)
    return query.order_by(Match.kickoff_utc.desc()).limit(limit).all()


@router.get("/{match_id}", response_model=MatchOut)
def get_match(match_id: int, db: Session = Depends(get_db)) -> Match:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    return match


@router.get("/{match_id}/predictions", response_model=list[PredictionOut])
def get_match_predictions(match_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """Seccion 1/18: cada prediccion del partido lleva sus `anomaly_flags`
    (calculadas contra las DEMAS predicciones de este mismo partido) y la
    que tenga mayor `signal_score` entre las que pasan el filtro duro y no
    tienen CONTRADICCION viene marcada `is_best_prediction=True` -- nunca
    la de mayor probabilidad/edge/cuota en crudo (ver prediction/anomaly.py
    y prediction/ranking.py)."""
    predictions = db.query(Prediction).filter(Prediction.match_id == match_id).all()
    best = best_prediction_per_match({match_id: predictions}).get(match_id)
    return [
        serialize_prediction(
            p,
            compute_anomaly_flags(p, predictions),
            is_best_prediction=(best is not None and p.id == best.id),
        )
        for p in predictions
    ]


def _recent_finished_matches(
    db: Session,
    team_id: int,
    before: dt.datetime,
    exclude_match_id: int,
    limit: int,
    venue: str | None = None,
) -> list[Match]:
    query = db.query(Match).filter(
        or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        Match.status == "finished",
        Match.id != exclude_match_id,
        Match.kickoff_utc < before,
    )
    if venue == "home":
        query = query.filter(Match.home_team_id == team_id)
    elif venue == "away":
        query = query.filter(Match.away_team_id == team_id)
    return query.order_by(Match.kickoff_utc.desc()).limit(limit).all()


def _serialize_form_match(match: Match, team_id: int) -> dict:
    is_home = match.home_team_id == team_id
    goals_for = match.home_goals if is_home else match.away_goals
    goals_against = match.away_goals if is_home else match.home_goals
    result = "D" if goals_for == goals_against else ("W" if goals_for > goals_against else "L")
    opponent = match.away_team if is_home else match.home_team
    return {
        "match_id": match.id,
        "kickoff_utc": match.kickoff_utc,
        "opponent": opponent.canonical_name,
        "venue": "home" if is_home else "away",
        "goals_for": goals_for,
        "goals_against": goals_against,
        "result": result,
    }


@router.get("/{match_id}/form", response_model=MatchFormOut)
def get_match_form(
    match_id: int,
    limit: int = Query(5, ge=1, le=10, description="Partidos recientes a devolver por bloque"),
    db: Session = Depends(get_db),
) -> dict:
    """Pedido real del usuario: 'quiero un apartado tipo H2H de como viene
    ese equipo, que suele hacer dentro/fuera de casa' -- forma reciente de
    cada equipo (en general y especificamente en ese rol local/visitante)
    mas el cara a cara historico entre ambos, SOLO con partidos realmente
    'finished' anteriores a este (nunca resultados inventados ni partidos
    todavia sin jugar)."""
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")

    home_id, away_id = match.home_team_id, match.away_team_id
    kickoff = match.kickoff_utc
    home_recent = _recent_finished_matches(db, home_id, kickoff, match_id, limit)
    away_recent = _recent_finished_matches(db, away_id, kickoff, match_id, limit)
    home_recent_at_venue = _recent_finished_matches(
        db, home_id, kickoff, match_id, limit, venue="home"
    )
    away_recent_at_venue = _recent_finished_matches(
        db, away_id, kickoff, match_id, limit, venue="away"
    )
    h2h = (
        db.query(Match)
        .filter(
            or_(
                and_(Match.home_team_id == home_id, Match.away_team_id == away_id),
                and_(Match.home_team_id == away_id, Match.away_team_id == home_id),
            ),
            Match.status == "finished",
            Match.id != match_id,
        )
        .order_by(Match.kickoff_utc.desc())
        .limit(limit)
        .all()
    )

    return {
        "home_team": {
            "team": match.home_team,
            "recent": [_serialize_form_match(m, home_id) for m in home_recent],
            "recent_at_venue": [_serialize_form_match(m, home_id) for m in home_recent_at_venue],
        },
        "away_team": {
            "team": match.away_team,
            "recent": [_serialize_form_match(m, away_id) for m in away_recent],
            "recent_at_venue": [_serialize_form_match(m, away_id) for m in away_recent_at_venue],
        },
        "head_to_head": [_serialize_form_match(m, home_id) for m in h2h],
    }
