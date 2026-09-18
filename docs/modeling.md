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
  modelos + calidad de dato + **cobertura de mercado** (cuantas casas de
  apuestas independientes respaldan `market_probability`, ver
  `market/consensus.py` — una cuota de una unica casa pesa menos que un
  consenso de 5+). Un edge enorme con confidence baja se presenta como
  tal, no se disfraza.
- **data_quality** (`prediction/data_quality.py`): completitud +
  frescura + tamanho de muestra de ESTA prediccion concreta. Independiente
  de si el modelo acierta.

## Consenso de mercado, no una unica casa (`market/consensus.py`)

Con The Odds API llegan cuotas de VARIAS casas de apuestas por partido.
Tratar la cuota de una unica casa (aunque sea la "preferida") como "el
mercado" es fragil: una casa con un error de tipeo, una linea mal
identificada, o simplemente poco liquida, puede inflar un edge que en
realidad es ruido de datos, no una oportunidad real.

`compute_market_consensus()`:
1. Nunca mezcla `snapshot_type` distintos (pre_match/closing/opening/live)
   en el mismo calculo.
2. Quita el vig por bookmaker cuando cotiza ambas selecciones.
3. Descarta bookmakers outlier (probabilidad implicita a mas de 3.5
   desviaciones absolutas medianas de la mediana del grupo) ANTES de
   agregar — solo con 3+ casas, donde hay base estadistica para decidirlo.
4. Agrega con la MEDIANA de las probabilidades supervivientes.
5. Guarda `bookmakers_count`/`bookmakers_used`/`min_odds`/`max_odds`/
   `median_odds`/`average_odds` en cada `Prediction`, para poder mostrar
   evidencia de mercado y para que `confidence` la pondere.

## Ranking de senhales (`prediction/ranking.py`)

Dos fases separadas: un filtro DURO configurable (`MIN_SIGNAL_ODDS`,
`MIN_EDGE_PP`, `MIN_BOOKMAKERS`, `MIN_DATA_QUALITY` en `Settings`, nunca
hardcodeados en el frontend) que descarta predicciones sin mercado valido
o con datos insuficientes, y un SCORING transparente entre las que pasan:

```
score = model_probability^2 * max(edge, 0) * confidence * data_quality
```

El cuadrado de la probabilidad es deliberado: sin el, una jugada mediocre
con mucho edge en puntos porcentuales (p.ej. 55% a cuota 3.0) puede
puntuar por encima de una jugada solida de alta probabilidad (p.ej. 80% a
cuota 1.35) solo por el tamanho bruto del edge. `/predictions/best` y
`/predictions/top-signals` ("Mejores señales") usan este mismo scoring;
`/predictions/model-only` expone, sin competir en el ranking, las
predicciones sin mercado (goles sin cuota todavia, o tarjetas/corners si
API-Football no esta configurado — ver docs/data_sources.md, con esa
fuente activa tarjetas/corners tambien pueden tener mercado real y
competir con normalidad).

## Jornada actual (`services/round_service.py`)

La UI principal muestra la jornada en curso de cada liga, no simplemente
"los proximos partidos que haya": mientras queden partidos SIN JUGAR de
la jornada N, esa sigue siendo la jornada actual aunque la jornada N+1 ya
tenga fecha. El numero de jornada viene de `Match.matchday`, poblado solo
por el adapter de fixtures (openfootball, campo `round: "Matchday N"`,
verificado real y consistente en las 5 ligas del MVP) — el dataset
historico no trae jornada. Si un partido programado no tiene `matchday`
todavia (fixtures ingeridos antes de este cambio), se cae a un fallback
explicito por fecha en vez de fingir una jornada real.

## Versionado (seccion 34)

Cada `train_competition_models(...)` crea una fila nueva en `model_versions`
(nunca sobrescribe) con: features usadas, hiperparametros, metricas de
holdout, `dataset_version`, y la ruta al artefacto `joblib` con los objetos
Python de los modelos entrenados + pesos de ensemble.
