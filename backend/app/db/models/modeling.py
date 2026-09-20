"""Versionado de modelos, predicciones, backtests."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.database import Base


class ModelVersion(Base):
    """Un entrenamiento concreto. Nunca se sobrescribe: cada train crea una fila nueva."""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))  # p.ej. "dixon_coles", "logistic_baseline"
    version: Mapped[str] = mapped_column(String(32))  # p.ej. "2024-06-01T12:00:00"
    market_family: Mapped[str] = mapped_column(String(32))  # "goals" | "cards" | "corners" | "shots"
    training_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    training_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    features: Mapped[list] = mapped_column(JSON)
    hyperparameters: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    dataset_version: Mapped[str] = mapped_column(String(64))
    calibration_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    predictions: Mapped[list[Prediction]] = relationship(back_populates="model_version")


class Prediction(Base):
    """Una prediccion de probabilidad para (partido, mercado, linea, modelo).

    Guarda todo lo necesario para reconstruir exactamente que sabia el sistema
    en `created_at`: probabilidad del modelo, cuota de mercado usada, edge,
    confianza y las variables (snapshot) que alimentaron la prediccion.
    """

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    market: Mapped[str] = mapped_column(String(64))
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    selection: Mapped[str] = mapped_column(String(32))

    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    model_probability: Mapped[float] = mapped_column(Float)
    # Probabilidad post-calibracion (IsotonicRegression ajustada en el
    # holdout de entrenamiento, ver services/model_service.py). Distinta de
    # `model_probability` a proposito (seccion 9 del brief): se guardan
    # AMBAS para poder auditar despues cuanto corrigio la calibracion.
    # `None` cuando no hubo holdout suficiente para calibrar ese mercado
    # (nunca se inventa un valor) -- ver MIN_CALIBRATION_MATCHES.
    calibrated_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_odds: Mapped[float] = mapped_column(Float)
    edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    data_quality: Mapped[float] = mapped_column(Float)
    explanation: Mapped[dict] = mapped_column(JSON)
    features_used: Mapped[dict] = mapped_column(JSON)

    # Evidencia de mercado (revision seccion 5/6): de cuantas casas viene
    # `market_probability`/`market_odds` y como se calcularon, para no
    # tratar una unica cuota aislada como "el mercado". Ver
    # backend/app/market/consensus.py. Todas nullable: predicciones sin
    # mercado (tarjetas/corners, o goles sin odds aun) no tienen nada de
    # esto, nunca se inventa un valor.
    market_probability_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bookmakers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bookmakers_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market_odds_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds_average: Mapped[float | None] = mapped_column(Float, nullable=True)

    # `signal_score` CONGELADO en el momento de generar la prediccion (ver
    # services/prediction_service.py::generate_predictions_for_competition):
    # es el mismo numero usado para el ranking cuando la senhal se mostro,
    # nunca se recalcula con la formula/pesos actuales al leerlo despues
    # (seccion 1/9 del brief -- "mejor prediccion" debe ser explicable con
    # los datos que existian entonces, no con criterios de hoy).
    signal_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    model_version: Mapped[ModelVersion] = relationship(back_populates="predictions")
    result: Mapped[PredictionResult | None] = relationship(
        back_populates="prediction", uselist=False, cascade="all, delete-orphan"
    )
    match = relationship("Match", foreign_keys=[match_id])


class PredictionResult(Base):
    """Resultado observado de una prediccion, para backtesting/monitorizacion."""

    __tablename__ = "prediction_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), unique=True)
    outcome: Mapped[bool] = mapped_column()  # True si la seleccion se cumplio (= is_correct)
    # Resultado real LITERAL del partido en el momento de liquidar (p.ej.
    # "2-1", "over", "btts_yes"), ademas del booleano `outcome` -- para poder
    # mostrar en el Historico "resultado real: 2-1" y no solo "acertada/fallada"
    # (seccion 8 del brief). `None` si no se pudo derivar (nunca inventado).
    actual_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settled_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    prediction: Mapped[Prediction] = relationship(back_populates="result")


class Backtest(Base):
    """Ejecucion de un backtest walk-forward sobre un modelo/mercado/competicion."""

    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    competition_id: Mapped[int | None] = mapped_column(ForeignKey("competitions.id"), nullable=True)
    market: Mapped[str] = mapped_column(String(64))
    split_type: Mapped[str] = mapped_column(String(32))  # "expanding" | "rolling"
    n_predictions: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
