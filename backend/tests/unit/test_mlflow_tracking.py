from __future__ import annotations

from backend.app.config.settings import Settings
from backend.app.services.mlflow_tracking import log_training_run


def _training_result() -> dict:
    return {
        "competition": "mlflow-test-league",
        "status": "trained",
        "model_version_id": 42,
        "n_matches": 500,
        "holdout_season": "2024/25",
        "metrics": {
            "markets": {
                "over_2_5": {
                    "dixon_coles": {"brier_score": 0.21, "log_loss": 0.6},
                    "logistic_independent": {"brier_score": 0.19, "log_loss": 0.58},
                    "ml_winner": "logistic_independent",
                    "ensemble": {"weight_statistical": 0.4, "log_loss": 0.57},
                }
            }
        },
    }


def test_disabled_by_default_is_a_no_op():
    settings = Settings(mlflow_enabled=False)
    assert log_training_run("mlflow-test-league", _training_result(), settings) is None


def test_enabled_logs_a_run_to_a_local_sqlite_store(tmp_path):
    tracking_uri = f"sqlite:///{tmp_path / 'mlruns.db'}"
    settings = Settings(
        mlflow_enabled=True, mlflow_tracking_uri=tracking_uri, mlflow_experiment_name="test-exp"
    )

    log_training_run("mlflow-test-league", _training_result(), settings)

    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment = client.get_experiment_by_name("test-exp")
    assert experiment is not None

    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 1
    run = runs[0]
    assert run.data.tags["competition_code"] == "mlflow-test-league"
    assert run.data.tags["ml_winner.over_2_5"] == "logistic_independent"
    assert run.data.params["model_version_id"] == "42"
    assert run.data.metrics["over_2_5.dixon_coles.brier_score"] == 0.21


def test_never_raises_even_if_mlflow_blows_up(monkeypatch):
    """Un fallo de tracking (disco lleno, permisos, lo que sea) no debe
    tirar abajo un entrenamiento real -- el tracking es un extra."""
    settings = Settings(mlflow_enabled=True, mlflow_tracking_uri="not-a-valid-uri-\x00")
    log_training_run("mlflow-test-league", _training_result(), settings)  # no debe lanzar
