# Modelado

## Modelos de goles comparados (`models/goals/`)

| Modelo | Enfoque | Notas |
|---|---|---|
| `baseline.py::LeagueAverageBaseline` | Poisson con lambda = media de la competicion | Ignora quien juega. Listón minimo obligatorio (seccion 52). |
| `poisson_model.py::PoissonRegressionModel` | 2x `sklearn.PoissonRegressor` (local/visitante) sobre features | Asume independencia entre goles local/visitante. |
| `dixon_coles.py::DixonColesModel` | Dixon & Coles (1997): attack/defense por equipo + home advantage + correccion `rho` en marcadores bajos | Interpretable, con decaimiento temporal (partidos recientes pesan mas). |
| `ml_classifier.py::MarketClassifierModel` | Un `LogisticRegression` por mercado, directo sobre features | No pasa por una distribucion de goles intermedia. |

Todos implementan (o se combinan a traves de) la misma interfaz de salida:
una distribucion de goles 0..5+ por equipo (`models/base.py::GoalsModel`), o
directamente una probabilidad de mercado (`MarketClassifierModel`). La
conversion distribucion -> probabilidad de mercado vive en UN solo sitio
(`prediction/probability.py`) para que todos los modelos se comparen de
forma identica.

## Por que Dixon-Coles y no solo Poisson

Poisson independiente subestima la probabilidad de marcadores bajos
correlacionados (0-0, 1-0, 0-1, 1-1). Dixon-Coles corrige exactamente esas 4
celdas con el parametro `rho`, ajustado por maxima verosimilitud junto con
attack/defense. Ver `DixonColesModel.predict_score_matrix`.

## Ensemble (`models/ensemble/ensemble.py`)

```
p_ensemble = w * p_statistical + (1 - w) * p_ml
```

`w` **no esta fijado a mano**: se busca por grid search en un set de
validacion separado del train, minimizando Log Loss
(`learn_ensemble_weight`). El peso aprendido se persiste junto al
`ModelVersion` (`hyperparameters`/artifact joblib).

## Calibracion (`models/calibration/`)

- **Metricas** (`metrics.py`): Brier Score, Log Loss, Expected Calibration
  Error, curva de fiabilidad con tamanho de muestra por bucket. Accuracy se
  calcula solo como referencia secundaria, nunca como criterio de seleccion.
- **Calibradores post-hoc** (`calibrators.py`): Isotonic Regression y Platt
  Scaling, siempre ajustados en un split de validacion DISTINTO del usado
  para medir la mejora despues (`backtesting/calibration.py::compare_calibration`).

## Confianza vs Edge vs Data Quality (seccion 72)

Tres numeros con significado distinto, nunca mezclados:

- **model_probability**: la probabilidad estimada, punto.
- **edge**: `model_probability - market_probability` (sin vig cuando es
  posible). Puede ser grande y venir de un modelo mal calibrado.
- **confidence** (`prediction/confidence.py`): combinacion documentada de
  calidad de calibracion historica + tamanho de muestra + acuerdo entre
  modelos + calidad de dato. Un edge enorme con confidence baja se
  presenta como tal, no se disfraza.
- **data_quality** (`prediction/data_quality.py`): completitud +
  frescura + tamanho de muestra de ESTA prediccion concreta. Independiente
  de si el modelo acierta.

## Versionado (seccion 34)

Cada `train_competition_models(...)` crea una fila nueva en `model_versions`
(nunca sobrescribe) con: features usadas, hiperparametros, metricas de
holdout, `dataset_version`, y la ruta al artefacto `joblib` con los objetos
Python de los modelos entrenados + pesos de ensemble.
