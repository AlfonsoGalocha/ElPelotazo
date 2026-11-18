"""Versionado de modelos, predicciones, backtests."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
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

    # Seccion "Rendimiento del modelo" (pedido real del usuario: "Solo se
    # activa una nueva version cuando demuestra mejoras fuera de
    # muestra... No quiero reentrenamientos automaticos que puedan
    # empeorar el sistema sin validacion"). Antes, `generate_predictions_
    # for_competition` usaba SIEMPRE la ModelVersion mas reciente de la
    # competicion sin comparar nunca con la anterior -- ver
    # services/promotion_service.py::evaluate_and_promote, que decide este
    # flag con una comparacion walk-forward real (nunca con datos ya
    # vistos en el entrenamiento del candidato) antes de que
    # `generate_predictions_for_competition` la use.
    #
    # NULLABLE a proposito (mismo patron aditivo que Competition.tier):
    # toda ModelVersion ya existente ANTES de esta columna queda en
    # `None`, y se trata como activa salvo que se diga lo contrario
    # (`isnot(False)`) -- asi el historico de entrenamientos de un
    # despliegue ya en marcha sigue funcionando exactamente igual que
    # antes, sin "desactivar" en silencio todo lo entrenado hasta ahora.
    is_active: Mapped[bool | None] = mapped_column(nullable=True)

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
    # Intervalo de incertidumbre sobre `model_probability` (ver
    # prediction/uncertainty.py): Wilson score interval por tamanho de
    # muestra efectivo, ensanchado por desacuerdo entre submodelos.
    # `None` cuando no se pudo calcular (nunca un intervalo degenerado
    # p.ej. [p, p] que sugeriria certeza absoluta inexistente).
    uncertainty_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_odds: Mapped[float] = mapped_column(Float)
    edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # EV con el extremo CONSERVADOR del intervalo de incertidumbre
    # (`uncertainty_low`, nunca `model_probability`) -- pedido real del
    # usuario ("revision motor 2.0", seccion 5): "si el modelo estima 70%
    # pero hay mucha incertidumbre, no tratar ese 70% como cifra fiable
    # para calcular el EV". Misma formula que `expected_value`
    # (p*cuota-1), con `p=uncertainty_low` en vez de `model_probability` --
    # NUNCA mas optimista que `expected_value` (uncertainty_low <=
    # model_probability siempre). `None` solo cuando no hay cuota.
    risk_adjusted_expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
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


class PredictionSnapshot(Base):
    """Copia INMUTABLE de una fila de `Prediction` justo antes de que
    `generate_predictions_for_competition` la borre para sustituirla por
    una nueva (partido aun no jugado, cuota/modelo actualizados) -- ver
    services/prediction_service.py.

    Bug real de diseño confirmado (pedido explicito del usuario,
    "mision historico"): `Prediction` nunca fue pensada como inmutable --
    el comentario original de `generate_predictions_for_competition` dice
    literalmente "como estos partidos aun no se han jugado, no hay ninguna
    razon para conservar la prediccion vieja: se borra y se sustituye
    siempre por la mas reciente". Eso es correcto para la UI en vivo (solo
    debe existir UNA prediccion "actual" por partido/mercado, nunca
    duplicados -- ver el comentario de esa funcion), pero significa que
    si la cuota se mueve de 1.65 a 1.52 entre el lunes y el viernes, la
    prediccion del lunes desaparece sin dejar rastro: no hay forma de
    saber despues que probabilidad/cuota/version de modelo se uso en cada
    momento. Esta tabla NUNCA se borra ni se actualiza (append-only, mismo
    patron ya usado en `MatchOddsHistory` para cuotas) -- cada vez que una
    `Prediction` esta a punto de ser reemplazada, se archiva aqui tal cual
    estaba, ANTES del delete. `Prediction` (la fila "actual") y su
    comportamiento no cambian en nada: esta tabla es puramente aditiva.

    `original_prediction_id` NO es una foreign key real (el id que
    referencia se borra en el mismo paso que crea este snapshot) -- es
    solo un entero de trazabilidad, para poder decir "esto fue la
    Prediction #1234 antes de ser reemplazada", nunca para hacer join
    relacional (esa fila ya no existe)."""

    __tablename__ = "prediction_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_prediction_id: Mapped[int] = mapped_column(Integer)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    market: Mapped[str] = mapped_column(String(64))
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    selection: Mapped[str] = mapped_column(String(32))
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))

    # Idéntico a como estaba en `Prediction` en el momento de archivar --
    # ver esa clase para el significado de cada campo.
    model_probability: Mapped[float] = mapped_column(Float)
    calibrated_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_odds: Mapped[float] = mapped_column(Float)
    edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_adjusted_expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    data_quality: Mapped[float] = mapped_column(Float)
    explanation: Mapped[dict] = mapped_column(JSON)
    features_used: Mapped[dict] = mapped_column(JSON)
    market_probability_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bookmakers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bookmakers_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market_odds_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_odds_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Momento ORIGINAL en que se genero esa prediccion (copiado de
    # `Prediction.created_at`, nunca reescrito) -- junto con `archived_at`
    # (cuando se reemplazo) define el intervalo de validez real de ese
    # snapshot concreto.
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    archived_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


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
    # Closing Line Value (ver market/clv.py): cuanto se movio la cuota de
    # consenso entre generar la prediccion y el cierre del mercado.
    # `None` explicito cuando no hay historico suficiente (MatchOddsHistory
    # append-only, solo existe desde que se anhadio esta tabla -- nunca
    # retroactivo) para calcularlo, nunca un numero inventado.
    closing_line_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Seccion "Rendimiento del modelo" (pedido real del usuario): estado
    # de liquidacion completo -- "won"|"lost"|"void"|"half_won"|
    # "half_lost" ("pending" NUNCA se guarda aqui: una Prediction sin
    # PredictionResult todavia ES "pending", por ausencia de fila, igual
    # que siempre). NULLABLE a proposito (mismo patron aditivo que
    # Competition.tier en db/models/core.py): las filas YA EXISTENTES
    # antes de esta columna no la tienen, y se interpretan con el
    # `outcome` booleano de siempre ("won" si True, "lost" si False) --
    # ver evaluation_service.py y api/routes/history.py, nunca se
    # backfillea a mano.
    #
    # "void" (mercado anulado, stake devuelto) es el unico valor que
    # produce hoy el pipeline (partidos CANCELADOS -- ver
    # evaluation_service.py::void_cancelled_predictions): se EXCLUYE de
    # Brier/log loss/ROI en vez de contar como "lost". "half_won"/
    # "half_lost" (ganancia o perdida parcial) quedan soportados por el
    # esquema pero SIN mercado real que los produzca todavia (ningun
    # mercado de este sistema admite un "push" parcial hoy) -- limitacion
    # conocida, documentada en architecture_audit.md.
    settlement_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    prediction: Mapped[Prediction] = relationship(back_populates="result")


class AgentAlert(Base):
    """Alerta proactiva de Jarvis (Fase 6 del brief del agente, seccion 13:
    "revisa los partidos de hoy" -> detectar contradicciones/senhales de
    baja fiabilidad/outliers/senhales de valor SIN que el usuario tenga que
    preguntar). La genera `backend/app/agent/alerts.py`, nunca el motor de
    prediccion -- una tabla nueva y separada en vez de reusar `Prediction`
    para no mezclar "lo que predice el modelo" con "lo que decide destacar
    el agente".

    Idempotente por diseno (`UniqueConstraint` match+tipo): generar alertas
    varias veces al dia (la pasada horaria del scheduler) nunca duplica la
    misma alerta para el mismo partido.
    """

    __tablename__ = "agent_alerts"
    __table_args__ = (UniqueConstraint("match_id", "alert_type", name="uq_agent_alert_match_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    prediction_id: Mapped[int | None] = mapped_column(ForeignKey("predictions.id"), nullable=True)
    # Mismas constantes que prediction/anomaly.py (CONTRADICCION,
    # SENAL_BAJA_FIABILIDAD, OUTLIER, HIGH_PROBABILITY_LOW_VALUE) mas
    # "SENAL_DE_VALOR" (propia de este modulo: mejor prediccion del
    # partido con edge/confianza por encima del umbral configurado).
    alert_type: Mapped[str] = mapped_column(String(32))
    # Texto en lenguaje llano, SOLO con numeros reales trazables a la
    # prediccion (nunca "apuesta segura"/certeza) -- mismo principio que
    # `explanation_summary` de /predictions/{id}/detail.
    message: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    match = relationship("Match", foreign_keys=[match_id])
    prediction = relationship("Prediction", foreign_keys=[prediction_id])


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
