"""Deteccion de cambios de distribucion (concept/data drift) en las
features de entrada -- gap directo de la auditoria de arquitectura: el
sistema reentrena por lotes con todo el historico, pero nunca comprobaba
si la distribucion de las features RECIENTES se ha movido respecto al
historico con el que se entreno, lo que podria degradar el modelo en
silencio hasta que `/models/performance` lo reflejara retroactivamente.

Population Stability Index (PSI): la metrica estandar de la industria
(originaria de scoring de riesgo crediticio) para medir cuanto ha cambiado
la distribucion de una variable entre una ventana de REFERENCIA y una
ventana ACTUAL. Umbrales de interpretacion ampliamente usados (no
inventados aqui):
    PSI < 0.10          -> sin cambio significativo
    0.10 <= PSI < 0.25  -> cambio moderado, vigilar
    PSI >= 0.25         -> cambio significativo, revisar el modelo

Esto es PURAMENTE INFORMATIVO (seccion "deteccion de drift" del brief
profesional): nunca bloquea `refresh`/`train`, solo da visibilidad de que
algo en los datos de entrada se esta moviendo, para decidir CON DATOS si
hace falta reentrenar antes de lo habitual o revisar una feature concreta.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PSI_MODERATE_THRESHOLD = 0.10
PSI_SIGNIFICANT_THRESHOLD = 0.25
DEFAULT_BINS = 10


def population_stability_index(
    reference: np.ndarray, current: np.ndarray, bins: int = DEFAULT_BINS
) -> float:
    """PSI de `current` respecto a `reference`, con los bins definidos por
    los PERCENTILES de `reference` (no de `current`): la pregunta es
    siempre "cuanto se ha movido lo actual respecto a lo que el modelo
    conocia", nunca al reves. `nan` cuando no hay suficiente variabilidad
    en la referencia para definir bins con sentido (nunca un numero
    inventado)."""
    reference = reference[~np.isnan(reference)]
    current = current[~np.isnan(current)]
    if len(reference) == 0 or len(current) == 0:
        return float("nan")

    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return float("nan")
    edges[0], edges[-1] = -np.inf, np.inf

    ref_counts, _ = np.histogram(reference, bins=edges)
    cur_counts, _ = np.histogram(current, bins=edges)
    # Clip a un minimo pequenho (no cero): un bin vacio en una muestra no
    # debe producir log(0) ni dividir por cero, pero sigue penalizando la
    # discrepancia (bin con mucho peso en un lado y nada en el otro).
    ref_pct = np.clip(ref_counts / ref_counts.sum(), 1e-4, None)
    cur_pct = np.clip(cur_counts / cur_counts.sum(), 1e-4, None)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def psi_tier(psi: float) -> str:
    if psi != psi:  # nan
        return "UNKNOWN"
    if psi < PSI_MODERATE_THRESHOLD:
        return "STABLE"
    if psi < PSI_SIGNIFICANT_THRESHOLD:
        return "MODERATE"
    return "SIGNIFICANT"


def feature_drift_report(
    table: pd.DataFrame, feature_cols: list[str], current_window: int = 50
) -> dict:
    """Divide los partidos YA FINALIZADOS de `table` (ordenados por fecha)
    en una ventana de referencia (todo menos los ultimos `current_window`)
    y una ventana actual (los ultimos `current_window`), y calcula PSI por
    feature entre ambas.

    Requiere al menos `2 * current_window` partidos finalizados -- con
    menos, la ventana de referencia seria demasiado pequenha para que PSI
    signifique algo; se devuelve `status: "insufficient_data"` en vez de
    un numero poco fiable.
    """
    finished = table.dropna(subset=["home_goals", "away_goals"]).sort_values("date")
    if len(finished) < current_window * 2:
        return {
            "status": "insufficient_data",
            "n_matches": len(finished),
            "required": current_window * 2,
        }

    reference = finished.iloc[:-current_window]
    current = finished.iloc[-current_window:]

    features: dict[str, dict] = {}
    for col in feature_cols:
        if col not in finished.columns:
            continue
        psi = population_stability_index(
            reference[col].to_numpy(dtype=float), current[col].to_numpy(dtype=float)
        )
        if psi != psi:  # nan: sin variabilidad suficiente para evaluar esta feature, se omite
            continue
        features[col] = {"psi": psi, "tier": psi_tier(psi)}

    significant = sorted(name for name, v in features.items() if v["tier"] == "SIGNIFICANT")
    moderate = sorted(name for name, v in features.items() if v["tier"] == "MODERATE")
    return {
        "status": "ok",
        "n_reference": len(reference),
        "n_current": len(current),
        "features": features,
        "features_with_significant_drift": significant,
        "features_with_moderate_drift": moderate,
    }
