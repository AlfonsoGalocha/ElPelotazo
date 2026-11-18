from __future__ import annotations

import datetime as dt

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
    training_start: dt.date | None = None
    training_end: dt.date | None = None
    created_at: dt.datetime
    calibration_method: str | None = None
    # Seccion "Rendimiento del modelo" (promotion_service.py): `None` para
    # ModelVersion de antes del gate de promocion -- el frontend la trata
    # como activa igual que el backend (`isnot(False)`), nunca como
    # "rechazada" por ausencia de dato.
    is_active: bool | None = None
    # Derivado de `dataset_version` ("laliga:1234_matches" -> "laliga"),
    # nunca una columna nueva: es el mismo valor que ya usa
    # `prediction_service.py` para elegir la ModelVersion de cada
    # competicion (`dataset_version LIKE "{competition_code}:%"`).
    competition_code: str | None = None

    model_config = {"from_attributes": True}

    @staticmethod
    def from_model_version(model_version: ModelVersion) -> "ModelVersionOut":
        competition_code = (
            model_version.dataset_version.split(":", 1)[0] if model_version.dataset_version else None
        )
        return ModelVersionOut(
            id=model_version.id,
            name=model_version.name,
            version=model_version.version,
            market_family=model_version.market_family,
            dataset_version=model_version.dataset_version,
            metrics=model_version.metrics,
            training_start=model_version.training_start,
            training_end=model_version.training_end,
            created_at=model_version.created_at,
            calibration_method=model_version.calibration_method,
            is_active=model_version.is_active,
            competition_code=competition_code,
        )


class TrainModelRequest(BaseModel):
    competition_code: str


@router.get("", response_model=list[ModelVersionOut])
def list_models(db: Session = Depends(get_db)) -> list[ModelVersionOut]:
    versions = db.query(ModelVersion).order_by(ModelVersion.created_at.desc()).all()
    return [ModelVersionOut.from_model_version(v) for v in versions]


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
def get_model(model_id: int, db: Session = Depends(get_db)) -> ModelVersionOut:
    model_version = db.get(ModelVersion, model_id)
    if model_version is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado")
    return ModelVersionOut.from_model_version(model_version)
