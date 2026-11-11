"""Tracking de experimentos (MLflow) para `train_competition_models`
(services/model_service.py) -- gap directo de la auditoria de
arquitectura: cada entrenamiento ya persistia un `ModelVersion` con
hiperparametros/metricas como JSON (ver db/models/modeling.py), pero sin
ninguna forma de comparar runs entre si, buscar por metrica, o ver la
evolucion a lo largo del tiempo sin escribir SQL a mano.

Desactivado por defecto (`settings.mlflow_enabled`, mismo patron que
`agent_enabled`): nunca se anhade una dependencia operativa nueva sin que
el usuario la active a proposito. Cuando esta activo, usa un backend
SQLite LOCAL (`sqlite:///<repo>/mlruns.db` por defecto) -- no hace falta
levantar un servidor MLflow para que esto funcione; `mlflow ui
--backend-store-uri sqlite:///mlruns.db` basta para explorar los runs.
NO se usa el file store plano (`file://...`): en MLflow >= 3 esta en
"maintenance mode" y rechaza escribir salvo que se fuerce explicitamente
(`MLFLOW_ALLOW_FILE_STORE=true`) -- descubierto escribiendo el test de
este modulo, no asumido de la documentacion.

PURAMENTE INFORMATIVO: nunca decide que modelo se usa en produccion (eso
lo sigue decidiendo el backtest real contra el holdout, ver
models/goals/model_selection.py) -- esto es solo un registro navegable de
lo que ya se decidio y por que, para no perder esa historia.
"""

from __future__ import annotations

from backend.app.config.settings import REPO_ROOT, Settings, get_settings
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)


def log_training_run(
    competition_code: str, training_result: dict, settings: Settings | None = None
) -> None:
    """`training_result`: el dict que devuelve `train_competition_models`
    (status/model_version_id/n_matches/holdout_season/metrics). Si MLflow
    no esta activado, o algo falla al registrar (p.ej. el paquete no esta
    instalado en un entorno que desactivo el flag despues de instalar),
    nunca rompe el entrenamiento real -- se loguea el fallo y se continua,
    el tracking es un extra, no una dependencia dura del pipeline."""
    settings = settings or get_settings()
    if not settings.mlflow_enabled:
        return

    try:
        import mlflow
    except ImportError:
        logger.warning("mlflow_tracking.not_installed", extra={"competition": competition_code})
        return

    try:
        tracking_uri = settings.mlflow_tracking_uri or f"sqlite:///{REPO_ROOT / 'mlruns.db'}"
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment_name)

        with mlflow.start_run(
            run_name=f"{competition_code}-{training_result.get('model_version_id')}"
        ):
            mlflow.set_tag("competition_code", competition_code)
            mlflow.set_tag("status", training_result.get("status"))
            mlflow.log_param("model_version_id", training_result.get("model_version_id"))
            mlflow.log_param("holdout_season", training_result.get("holdout_season"))
            mlflow.log_param("n_matches", training_result.get("n_matches"))

            metrics = training_result.get("metrics", {})
            for market_key, market_metrics in metrics.get("markets", {}).items():
                for model_name, values in market_metrics.items():
                    if not isinstance(values, dict):
                        continue
                    for metric_name, value in values.items():
                        if isinstance(value, (int, float)):
                            mlflow.log_metric(
                                f"{market_key}.{model_name}.{metric_name}", float(value)
                            )
                if "ml_winner" in market_metrics:
                    mlflow.set_tag(f"ml_winner.{market_key}", market_metrics["ml_winner"])
    except Exception:
        # Nunca propaga: un fallo de tracking (p.ej. disco lleno, permisos
        # del directorio mlruns/) no debe tirar abajo un entrenamiento real.
        logger.warning(
            "mlflow_tracking.failed", exc_info=True, extra={"competition": competition_code}
        )
