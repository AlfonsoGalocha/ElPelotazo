from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.standings import StandingsSnapshot
from backend.app.schemas.match import CompetitionOut, RoundOut
from backend.app.services.round_service import get_current_round

router = APIRouter(prefix="/competitions", tags=["competitions"])


@router.get("", response_model=list[CompetitionOut])
def list_competitions(db: Session = Depends(get_db)) -> list[Competition]:
    """Nunca devuelve una competicion deshabilitada explicitamente
    (`enabled is False`, ver db/models/core.py::Competition) -- `None`
    (competiciones ya existentes antes de anhadir ese campo) cuenta como
    habilitada, nunca se oculta por falta de dato."""
    competitions = db.query(Competition).all()
    visible = [c for c in competitions if c.enabled is not False]
    return sorted(visible, key=lambda c: (-(c.priority or 0), c.name))


@router.get("/{competition_code}/current-round", response_model=RoundOut)
def current_round(competition_code: str, db: Session = Depends(get_db)) -> dict:
    """Jornada actual de la competicion (ver services/round_service.py):
    la jornada con el `matchday` mas bajo que aun tiene partidos
    pendientes. Nunca "los proximos partidos que haya" sin mas."""
    round_info = get_current_round(db, competition_code)
    if round_info is None:
        raise HTTPException(
            status_code=404, detail="Sin partidos programados para esta competicion"
        )
    return {
        "competition_code": competition_code,
        "round": round_info.round,
        "round_name": round_info.round_name,
        "round_start": round_info.round_start,
        "round_end": round_info.round_end,
        "next_round": round_info.next_round,
        "is_fallback": round_info.is_fallback,
        "match_ids": round_info.match_ids,
    }


@router.get("/{competition_code}/standings")
def standings_as_of(
    competition_code: str,
    season: str,
    as_of: datetime | None = None,
    db: Session = Depends(get_db),
):
    query = (
        db.query(StandingsSnapshot)
        .join(Competition, Competition.id == StandingsSnapshot.competition_id)
        .join(Season, Season.id == StandingsSnapshot.season_id)
        .filter(Competition.code == competition_code, Season.label == season)
    )
    if as_of is not None:
        cutoff = as_of.astimezone(timezone.utc).replace(tzinfo=None) if as_of.tzinfo else as_of
        query = query.filter(StandingsSnapshot.as_of <= cutoff)
    captured_at = query.with_entities(StandingsSnapshot.as_of).order_by(
        StandingsSnapshot.as_of.desc()
    ).limit(1).scalar()
    if captured_at is None:
        raise HTTPException(status_code=404, detail="No existe una clasificación archivada para ese corte")
    return query.filter(StandingsSnapshot.as_of == captured_at).order_by(StandingsSnapshot.position).all()


@router.get("/research/status")
def experiment_status(db: Session = Depends(get_db)):
    from sqlalchemy import func, or_

    from backend.app.db.models.matches import Match, MatchOddsHistory, MatchStatistics
    from backend.app.db.models.modeling import ModelVersion, Prediction
    from backend.app.db.models.publication import StrategyCapture
    from backend.app.normalization.competitions import EXPERIMENTAL_CATALOG

    reports = []
    for code, (name, country, api_id, phase) in EXPERIMENTAL_CATALOG.items():
        competition = db.query(Competition).filter_by(code=code).one_or_none()
        row = dict(code=code, name=name, country=country, api_id=api_id, phase=phase,
                   data_status="pending_data", model_status="pending", tracking_status="pending",
                   evaluation_status="pending", seasons=[], model=None, quotes=0, forecast_count=0,
                   captures=0, settled_captures=0)
        if competition is not None:
            matches = db.query(Match).filter_by(competition_id=competition.id)
            match_ids = matches.with_entities(Match.id)
            for label, total, finished, goals, corners in db.query(
                Season.label, func.count(Match.id),
                func.count(Match.id).filter(Match.status == "finished"),
                func.count(Match.id).filter(Match.status == "finished", Match.home_goals >= 0, Match.away_goals >= 0),
                func.count(Match.id).filter(MatchStatistics.home_corners >= 0, MatchStatistics.away_corners >= 0),
            ).join(Match, Match.season_id == Season.id).outerjoin(
                MatchStatistics, MatchStatistics.match_id == Match.id,
            ).filter(Season.competition_id == competition.id).group_by(Season.label).order_by(Season.label):
                row["seasons"].append(dict(season=label, fixtures=total, finished=finished, goals=goals, corners=corners))
            if row["seasons"]:
                row["data_status"] = (
                    "data_available" if len(row["seasons"]) >= 3 and sum(s["corners"] for s in row["seasons"]) >= 50
                    else "importing_history"
                )
            version = db.query(ModelVersion).filter(
                ModelVersion.is_active.is_(False),
                or_(ModelVersion.dataset_version.like(f"{code}:%"),
                    ModelVersion.dataset_version.like(f"learning:{code}:%")),
            ).order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).first()
            if version:
                row["model_status"] = "trained"
                research_metrics = (version.metrics or {}).get("research_holdout")
                row["model"] = dict(id=version.id, version=version.version, validation=research_metrics)
                row["evaluation_status"] = "evaluation_available" if research_metrics else "evaluation_pending"
            row["forecast_count"] = db.query(Prediction).filter(Prediction.match_id.in_(match_ids)).count()
            row["quotes"] = db.query(MatchOddsHistory).join(Match, Match.id == MatchOddsHistory.match_id).filter(
                Match.competition_id == competition.id, MatchOddsHistory.snapshot_type == "pre_match",
                MatchOddsHistory.provider_updated_at <= MatchOddsHistory.fetched_at,
                MatchOddsHistory.fetched_at <= MatchOddsHistory.recorded_at,
                MatchOddsHistory.recorded_at < Match.kickoff_utc,
            ).count()
            captures = db.query(StrategyCapture).join(Match, Match.id == StrategyCapture.match_id).filter(
                Match.competition_id == competition.id, StrategyCapture.captured_at < Match.kickoff_utc,
            )
            row["captures"] = captures.count()
            row["settled_captures"] = captures.filter(Match.status == "finished").count()
            row["tracking_status"] = "prospective_active" if row["captures"] else "awaiting_first_capture"
        reports.append(row)
    return reports
