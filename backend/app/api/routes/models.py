from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.modeling import ModelVersion
from backend.app.services.evaluation_service import real_performance_report
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


@router.post("/train")
def train_model(request: TrainModelRequest, db: Session = Depends(get_db)) -> dict:
    return train_competition_models(db, request.competition_code)


@router.get("/performance")
def model_performance(
    competition_code: str | None = Query(None),
    market: str | None = Query(None),
    model_version_id: int | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Fase 5 (seccion 10/11 del brief): rendimiento REAL del modelo,
    segmentado por competicion/mercado/version de modelo, calculado SOLO
    sobre predicciones ya liquidadas (`PredictionResult`, ver
    `services/evaluation_service.py::settle_finished_predictions`) -- nunca
    un backtest sintetico. Incluye la curva de calibracion real
    (predicho vs empirico por bucket de probabilidad) y el desglose por
    rango de edge.

    Definido ANTES de `/{model_id}` a proposito: FastAPI resuelve rutas en
    orden de declaracion, y `/{model_id}` (int) capturaria "performance"
    como si fuera un id invalido si fuera antes."""
    return real_performance_report(db, competition_code, market, model_version_id)


@router.get("/{model_id}", response_model=ModelVersionOut)
def get_model(model_id: int, db: Session = Depends(get_db)) -> ModelVersion:
    model_version = db.get(ModelVersion, model_id)
    if model_version is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado")
    return model_version
