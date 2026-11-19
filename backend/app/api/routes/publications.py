from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.features import TeamMatchFeatures
from backend.app.db.models.publication import FixturePublication, FixtureRankingRevision
from backend.app.services.publication_service import publication_report

router = APIRouter(prefix="/publications", tags=["publications"])


@router.get("")
def publications(
    competition: str | None = None,
    market: str | None = None,
    model_version: int | None = None,
    min_probability: float | None = Query(None, ge=0, le=1),
    matchday: int | None = None,
    outcome: str | None = None,
    max_probability: float | None = Query(None, ge=0, le=1),
    db: Session = Depends(get_db),
):
    versions = {
        row["feature_set_version"]
        for group in publication.groups.values() for row in group["alternatives"]
        if row.get("feature_set_version")
    }
    snapshots = db.query(TeamMatchFeatures).filter(
        TeamMatchFeatures.match_id == match_id, TeamMatchFeatures.feature_set_version.in_(versions)
    ).all()
    return {**jsonable_encoder(publication), "feature_snapshots": snapshots}_report(db, competition, market, model_version, min_probability, matchday, outcome, max_probability)


@router.get("/{match_id}")
def publication_detail(match_id: int, db: Session = Depends(get_db)):
    publication = db.get(FixturePublication, match_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="No existe una captura oficial para este partido")
    return publication


@router.get("/{match_id}/revisions")
def ranking_revisions(match_id: int, limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    return db.query(FixtureRankingRevision).filter_by(match_id=match_id).order_by(
        FixtureRankingRevision.captured_at.desc()
    ).limit(limit).all()
