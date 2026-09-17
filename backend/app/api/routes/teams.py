from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.core import Team
from backend.app.schemas.team import TeamOut

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=list[TeamOut])
def list_teams(db: Session = Depends(get_db)) -> list[Team]:
    return db.query(Team).order_by(Team.canonical_name).all()
