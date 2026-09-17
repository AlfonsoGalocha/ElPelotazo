from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.backtesting.engine import run_walk_forward_backtest, summarize_backtest
from backend.app.db.database import get_db
from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import Backtest, ModelVersion
from backend.app.features.goals import build_match_feature_table
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.prediction.market_labels import MARKET_DEFINITIONS
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import load_latest_model_version

router = APIRouter(tags=["backtesting"])


class BacktestListItem(BaseModel):
    id: int
    market: str
    n_predictions: int
    metrics: dict
    model_version_id: int

    model_config = {"from_attributes": True}


class RunBacktestRequest(BaseModel):
    competition_code: str
    market: str = "over_2_5"


@router.get("/backtests", response_model=list[BacktestListItem])
def list_backtests(db: Session = Depends(get_db)) -> list[Backtest]:
    return db.query(Backtest).order_by(Backtest.created_at.desc()).all()


@router.post("/backtests/run", response_model=BacktestListItem)
def run_backtest(request: RunBacktestRequest, db: Session = Depends(get_db)) -> Backtest:
    if request.market not in MARKET_DEFINITIONS:
        raise HTTPException(status_code=400, detail=f"Mercado desconocido: {request.market}")

    competition = db.query(Competition).filter_by(code=request.competition_code).one_or_none()
    if competition is None:
        raise HTTPException(status_code=404, detail="Competicion no encontrada")

    matches = load_matches_dataframe(db, competition.id)
    table = build_match_feature_table(matches)

    results = run_walk_forward_backtest(table, DixonColesModel, request.market)
    summary = summarize_backtest(results)

    model_version = load_latest_model_version(db) or _placeholder_model_version(db)

    backtest = Backtest(
        model_version_id=model_version.id,
        competition_id=competition.id,
        market=request.market,
        split_type="expanding",
        n_predictions=summary.get("model_quality", {}).get("n_predictions", 0),
        metrics=summary,
    )
    db.add(backtest)
    db.commit()
    db.refresh(backtest)
    return backtest


def _placeholder_model_version(db: Session) -> ModelVersion:
    """Si aun no se ha entrenado ningun modelo formalmente, registra un
    ModelVersion minimo para poder asociar el backtest (evita romper la
    trazabilidad de seccion 7: toda prediccion/backtest debe apuntar a una
    version de modelo concreta)."""
    placeholder = ModelVersion(
        name="dixon_coles_ad_hoc_backtest",
        version="ad-hoc",
        market_family="goals",
        features=[],
        hyperparameters={},
        metrics={},
        dataset_version="ad-hoc",
    )
    db.add(placeholder)
    db.commit()
    db.refresh(placeholder)
    return placeholder
