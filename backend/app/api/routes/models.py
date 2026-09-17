from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.modeling import ModelVersion
from backend.app.services.model_service import train_competition_models

router = APIRouter(prefix="/models", tags=["models"])


class ModelVersionOut(BaseModel):
    id: int
    name: str
    version: str
    market_family: str
    dataset_version: str
    metrics: dict

    model_config = {"from_attributes": True}


class TrainModelRequest(BaseModel):
    competition_code: str


@router.get("", response_model=list[ModelVersionOut])
def list_models(db: Session = Depends(get_db)) -> list[ModelVersion]:
    return db.query(ModelVersion).order_by(ModelVersion.created_at.desc()).all()


@router.get("/{model_id}", response_model=ModelVersionOut)
def get_model(model_id: int, db: Session = Depends(get_db)) -> ModelVersion:
    model_version = db.get(ModelVersion, model_id)
    if model_version is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado")
    return model_version


@router.post("/train")
def train_model(request: TrainModelRequest, db: Session = Depends(get_db)) -> dict:
    return train_competition_models(db, request.competition_code)
