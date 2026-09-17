from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.core import Competition
from backend.app.schemas.match import CompetitionOut

router = APIRouter(prefix="/competitions", tags=["competitions"])


@router.get("", response_model=list[CompetitionOut])
def list_competitions(db: Session = Depends(get_db)) -> list[Competition]:
    return db.query(Competition).order_by(Competition.name).all()
