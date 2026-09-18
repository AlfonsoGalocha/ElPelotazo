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
`MIN_EDGE_PP`, `MIN_BOOKMAKERS`, `MIN_DATA_QUALITY`, `MAX_SIGNAL_ODDS` en
`Settings`, nunca hardcodeados en el frontend) que descarta predicciones
sin mercado valido o con datos insuficientes, y un SCORING transparente
entre las que pasan:

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

**`MAX_SIGNAL_ODDS` (2026-09-18, feedback real de usuario)**: unica
excepcion deliberada a "el filtro duro no juzga calidad de la senhal, solo
que los datos sean validos". Un edge grande en un resultado muy
improbable (p.ej. modelo ve ~12% de probabilidad, cuota justa 8, el
mercado ofrece 15) es matematicamente un edge real, pero no es una
prediccion practica para destacar: sigue siendo mas probable que falle
que que acierte, y el `model_probability^2` del scoring no basta por si
solo para dejarlo fuera del top-N si un dia hay pocas senhales
candidatas (compite igual por "hueco" en la lista aunque su score
absoluto sea bajo). Por defecto `6.0` (cuotas por encima implican
probabilidad implicita < ~17%) — ajustable a tu propio criterio de
riesgo, o `None` para desactivarlo.

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

## Cerrar el ciclo: resultados reales -> reentrenar -> evaluar

El modelo nunca mantiene estado entre ejecuciones: cada `football-edge
train` reentrena TODO desde cero (Dixon-Coles/baseline/ML classifier)
leyendo TODOS los partidos ya jugados de la BD en ese momento. Por eso
"que el modelo aprenda de la realidad" no necesita ninguna infraestructura
de aprendizaje online -- solo necesita que los resultados reales de la
jornada que acaba de terminar lleguen a la BD antes de reentrenar.

**Bug real corregido (2026-09-18)**: `ClubFootballMatchDataProvider`
(fuente por defecto de `football-edge update`) descarga el CSV historico
UNA VEZ y lo cachea en disco (`data/external/`, ~45MB). El parametro
`force_refresh` para invalidar ese cache existia desde el principio del
proyecto, pero NADA lo invocaba nunca en `True` -- asi que, una vez
descargado el CSV la primera vez, re-ejecutar `update` (o `refresh`, que
lo incluye) NUNCA volvia a comprobar si habia partidos nuevos jugados,
por muchas veces que se ejecutara. Esto bloqueaba silenciosamente
exactamente el flujo que se pedia: "cuando acabe la jornada, recopilar
los datos reales". Corregido con `ClubFootballMatchDataProvider.refresh_cache()`,
invocado por defecto en `football-edge update` (desactivable con
`--no-refresh-cache` solo para pruebas repetidas el mismo dia sin gastar
red).

**Flujo recomendado tras acabar una jornada** (ya encadenado en
`football-edge refresh`, en este orden):
1. `update` -- trae los resultados reales (fuerza el refresco del cache).
2. `evaluate` -- compara las predicciones que YA estaban guardadas de esos
   partidos (hechas ANTES del pitido inicial, nunca se borran al
   finalizar el partido) contra el resultado real: Brier score, log loss,
   calibracion y accuracy siempre; ROI hipotetico solo si habia cuota de
   mercado real (ver `backtesting/metrics.py::market_strategy_metrics`).
   Escribe `data/processed/evaluation_report.json`. Es puramente
   observabilidad -- no cambia nada del modelo, solo permite VER como de
   bien predijo antes de decidir si hace falta ajustar algo.
3. `train` -- reentrena con los resultados nuevos ya incorporados.
4. `predict-upcoming` -- genera predicciones para los proximos partidos
   con el modelo ya actualizado.

Comando aislado: `football-edge evaluate --competition laliga` (o sin
`--competition` para todas). Ver `services/evaluation_service.py`.

## Versionado (seccion 34)

Cada `train_competition_models(...)` crea una fila nueva en `model_versions`
(nunca sobrescribe) con: features usadas, hiperparametros, metricas de
holdout, `dataset_version`, y la ruta al artefacto `joblib` con los objetos
Python de los modelos entrenados + pesos de ensemble.
