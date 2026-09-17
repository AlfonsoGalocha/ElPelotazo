"""Entry point de la API FastAPI."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.routes import (
    backtesting,
    competitions,
    health,
    matches,
    models,
    predictions,
    teams,
)
from backend.app.db.database import init_db
from backend.app.utils.logging import configure_logging

configure_logging()

app = FastAPI(
    title="Football Edge Detector API",
    description="Probabilidades, edges de mercado y backtesting para futbol.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(health.router)
app.include_router(competitions.router)
app.include_router(teams.router)
app.include_router(matches.router)
app.include_router(predictions.router)
app.include_router(backtesting.router)
app.include_router(models.router)
