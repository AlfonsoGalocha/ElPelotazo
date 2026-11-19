# Misión: plataforma profesional de predicción, evaluación y aprendizaje — documento de continuidad

Informe de una misión de alcance muy grande (auditoría profunda + rediseño de
calidad del modelo, oportunidades, visualización y persistencia histórica).
No se completa en una sola sesión — este documento es el punto de retoma
exacto. Formato: igual que el informe final pedido (secciones A-G), marcado
como **INTERIM** donde el trabajo aún no está terminado.

Fecha de este corte: 2026-11-18/19. Rama: `claude/football-edge-detector-x0f2ea`.

## A. Diagnóstico inicial — hallazgos reales (con cita de archivo:línea)

### Bloque 1 (calidad del modelo) — bug real encontrado y corregido EN PRODUCCIÓN

El usuario reportó en vivo (captura de pantalla, Bundesliga "Ambos equipos
marcan"): modelo crudo 78.5%, `calibrated_probability` 97.2% (+18.7pp),
EV estimado inflado a +42.9%. Causa real confirmada: la calibración
Isotonic se activaba SIEMPRE que hubiera suficiente holdout GLOBAL, sin
validar nunca si mejoraba el Brier score para ESE mercado en concreto —
exactamente el pathology conocido de Isotonic Regression sobreajustando
un cluster pequeño de la zona de probabilidad alta. **Corregido** (commit
`a7fb6d5`, mergeado a `main`): nueva `_fit_validated_isotonic_calibrator`
en `model_service.py` — solo activa la calibración de un mercado si
demuestra mejorar el Brier score en un sub-split de test disjunto del de
ajuste (reutiliza `backtesting/calibration.py::compare_calibration`, antes
solo usado para informes offline). 3 tests nuevos, 567/567 en verde.
**Importante**: este fix solo se aplica en el PRÓXIMO entrenamiento de
cada competición (el calibrador va dentro del artefacto de modelo
guardado) — hace falta `football-edge train` (o esperar al ciclo de 6h
del scheduler) para que Bundesliga deje de mostrar el 97.2%.

### Bloque 1 (resto) — MUCHO YA EXISTE, verificado leyendo código:

- **Dixon-Coles + ensemble ML + pesos por simplex**: `backend/app/models/goals/dixon_coles.py`,
  `backend/app/models/ensemble/ensemble.py`.
- **Calibración por mercado** (no global): `backend/app/services/model_service.py:259`
  — `calibrators[market_key] = IsotonicCalibrator().fit(...)`, con
  `MIN_CALIBRATION_MATCHES = 60` (línea 48) y motivo explícito logueado
  cuando no hay holdout suficiente (nunca se inventa calibración).
- **Platt + Isotonic comparados con Brier/LogLoss en holdout separado**:
  `backend/app/models/calibration/calibrators.py`,
  `backend/app/backtesting/calibration.py` (`compare_calibration`,
  `ensemble_calibration_report`).
- **ECE (Expected Calibration Error)**: existe y se usa en
  `backend/app/backtesting/ranking_comparison.py:273` y en
  `market_track_records` (ver Bloque 2 abajo).
- **Incertidumbre**: `backend/app/prediction/uncertainty.py` (Wilson score
  interval por tamaño de muestra efectivo). `Prediction.uncertainty_low/high`
  ya persistido.
- **Probabilidad conservadora** (`conservative_probability` en
  `backend/app/prediction/ranking.py`): mínimo entre calibrada y
  `uncertainty_low`, nunca un blend — ya implementado tal como lo pide el
  Bloque 1.5 del prompt.
- **Sin límite artificial** (0.85/0.90 cap): confirmado por grep, no existe
  ningún clip de probabilidad en el pipeline de predicción.
- **Shin's method** (quitar vig con modelo de dinero informado): documentado
  como **deliberadamente diferido** en `backend/app/market/vig.py` — motivo
  real: el entorno de desarrollo no tuvo acceso de red a ninguna fuente
  verificable (arxiv/wikipedia/paper original) para confirmar la fórmula
  cerrada, y "una fórmula sutilmente incorrecta corrompería `market_probability`
  en silencio" — se prefirió no implementarla sin poder verificarla. Sigue
  usando el método proporcional estándar.

**Gap real identificado**: no hay evidencia de *drift de lesiones/ausencias*
ni de ningún indicador de "equipo recién ascendido" explícito — el prompt
pide tratarlos cuando haya datos; API-Football SÍ puede dar lesiones
(`/injuries`, visto en su documentación oficial) pero no está integrado hoy.
Pendiente (ver sección G).

### Bloque 2 (Oportunidades) — gap real y concreto confirmado

- El filtro v3 (`backend/app/prediction/ranking.py::evaluate_quality_gate_v3`,
  `ranking_key_v3`) ya implementa exactamente el orden de prioridad pedido
  (calibrada → EV conservador → fiabilidad histórica → incertidumbre →
  calidad de datos → edge como desempate), cuota real inclusive [1.35, 1.90]
  configurable, EV conservador obligatorio. **Ya existe el endpoint de
  diagnóstico pedido explícitamente en el Bloque 2.1**: `GET
  /predictions/best/debug` (`backend/app/api/routes/predictions.py:690`),
  devuelve included/excluded con motivo exacto por candidata.
- **GAP REAL CONFIRMADO Y YA RESUELTO (Bloque 2.2, fiabilidad jerárquica)**:
  `backend/app/services/evaluation_service.py::market_track_records` solo
  calculaba fiabilidad por **(competición, mercado) exacto**, con corte
  duro `MIN_SETTLED_FOR_TRACK_RECORD = 30`: si esa combinación exacta no
  llegaba a 30 liquidaciones reales, desaparecía del diccionario entero —
  causa real, verificada, de que "Oportunidades" mostrara 0 partidos en
  Bundesliga con ventana de 1 día (visto en vivo en esta misma sesión).
  **IMPLEMENTADO Y PROBADO** (commit `9714948`, mergeado a `main`): nueva
  jerarquía en `market_track_records_with_provenance` — (competición,
  mercado) exacto → (tier de competición ya existente, CORE/EXPERIMENTAL,
  mercado) → mercado global → ninguno. `market_track_records()` mantiene
  el mismo contrato de siempre (sin tocar ningún llamador), la jerarquía
  vive en la función nueva, que ahora también alimenta
  `/predictions/best/debug` con `source_level`/`n_observations` por
  predicción (nunca se presenta un nivel general como si fuera
  específico). 564/564 tests.
  **También implementado y mergeado** (commit `403ba57`): `/top-signals`,
  `/best` y `/best-of-day` ahora devuelven `reliability` por predicción
  (`ReliabilityOut`: value/source_level/n_observations), y la tabla de
  Oportunidades muestra una columna "Fiabilidad histórica" con etiqueta
  de procedencia (esta liga / ligas similares / global) — nunca presenta
  un nivel general como si fuera específico.
  **Pendiente real todavía**: shrinkage bayesiano (James-Stein o
  Beta-Binomial) en vez de corte duro de 30 — evaluado como mejora futura,
  no implementado.

### Bloque 3 (histórico inmutable) — PRIORIDAD MÁXIMA, trabajo real hecho esta sesión

- **Gap real confirmado por código** (no asumido):
  `generate_predictions_for_competition`
  (`backend/app/services/prediction_service.py`, antes de este commit)
  hacía `db.query(Prediction).filter(...).delete()` en cada regeneración —
  la predicción anterior desaparecía SIN DEJAR RASTRO. Confirmado con un
  comentario explícito ya existente en el código que documentaba esta
  decisión de diseño (correcta para la UI en vivo, pero incompatible con
  "inmutabilidad histórica").
- **IMPLEMENTADO Y PROBADO** (commit `02b91b3`, mergeado a `main`): nuevo
  modelo `PredictionSnapshot` (`backend/app/db/models/modeling.py`,
  append-only, mismo patrón ya probado en `MatchOddsHistory`). Cada
  `Prediction` se archiva ahí, campo por campo, justo antes de ser
  reemplazada. `Prediction` (la fila "actual") no cambia de comportamiento
  — cambio puramente aditivo, cero riesgo de regresión.
  Test de regresión:
  `test_regenerating_predictions_archives_the_superseded_ones_in_prediction_snapshot`
  en `backend/tests/integration/test_api.py`.
- **Ya existía antes de esta sesión** (confirmado, no nuevo): `ModelVersion`
  nunca se sobrescribe (cada train = fila nueva), `MatchOddsHistory`
  (append-only para cuotas, alimenta CLV), `OddsIngestionRun` (append-only,
  diagnóstico de cada intento de ingesta).
- **Hallazgo real importante, confirmado por grep exhaustivo**: ya existe
  `backend/app/db/models/features.py::TeamMatchFeatures` — una tabla
  pensada EXACTAMENTE para esto ("Snapshot de features conocidas ANTES
  del kickoff... materializadas para reproducibilidad", `as_of` como
  instante de corte, `features: JSON`, `UniqueConstraint(match_id,
  team_id, feature_set_version)`) — pero **nunca se ha escrito ninguna
  fila en ella en todo el proyecto** (confirmado: `grep -rl
  "TeamMatchFeatures("` solo encuentra su propia definición). Es una
  tabla muerta, nunca conectada al pipeline real.
  `Prediction.explanation` SÍ guarda valores reales (`raw_value` por
  feature), pero solo de las top-N features más explicativas (ver
  `prediction/explanation.py`), no del vector completo usado.
  **IMPLEMENTADO Y PROBADO** (commit `2ca996a`, mergeado a `main`):
  `generate_predictions_for_competition` ahora archiva una fila por
  equipo en `TeamMatchFeatures` cada vez que genera predicciones, usando
  `feature_columns()` (la misma lista ya probada, sin leakage) y una
  `feature_set_version` nueva en cada llamada (append-only, nunca
  sobreescribe). 2 tests nuevos. 568/568 tests.
  **Diseño descartado (ya resuelto) para conectar `TeamMatchFeatures`** (no
  implementado todavía, deliberadamente no apresurado):
  1. `build_match_feature_table` (`backend/app/features/goals.py`) ya
     devuelve columnas con prefijo `home_`/`away_` consistente — separar
     por equipo es mecánico: `{col.removeprefix("home_"): valor for col
     in table.columns if col.startswith("home_")}` (y análogo para away).
  2. Hook en `generate_predictions_for_competition` (prediction_service.py),
     justo después de construir `table`, antes de archivar en
     `PredictionSnapshot`.
  3. **Decisión de diseño pendiente**: la restricción de unicidad actual
     (match_id, team_id, feature_set_version) implica UPSERT si
     `feature_set_version` se queda fija ("v1") — eso perdería historial
     igual que `Prediction` antes del fix de hoy. Opciones: (a) versionar
     `feature_set_version` con un timestamp/hash por cada regeneración
     (verdaderamente append-only, más filas), o (b) aceptarlo como "última
     foto conocida" y añadir un `PredictionSnapshot`-equivalente aparte
     SOLO si hace falta reconstruir una version anterior exacta. Evaluar
     volumen esperado antes de decidir.

### Bloque 3.4/3.5 (esquema DB) — auditoría de tablas existentes vs pedidas

Ya existen (confirmado, `backend/app/db/models/*.py`):
`competitions`, `seasons`, `teams`, `team_name_mappings`, `matches`,
`match_statistics`, `match_odds` (snapshot actual), `match_odds_history`
(append-only), `model_versions`, `predictions`, `prediction_snapshots`
(nuevo, esta sesión), `prediction_results`, `ranking_config`,
`odds_ingestion_runs` (append-only, diagnóstico), `backtests`, `agent_alerts`.

Pedidas en el prompt y **sin equivalente directo todavía**:
`standings_snapshots` (clasificación histórica por fecha — hoy se calcula
al vuelo desde `matches`, no se guarda snapshot por jornada),
`opportunity_evaluations` (el propio Bloque 2 lo cubriría si se persiste
el resultado de `/best/debug` en vez de ser solo observabilidad en
caliente), `data_sync_runs` (ya existe un equivalente real:
`odds_ingestion_runs`, pero solo cubre cuotas, no fixtures/resultados/
clasificaciones), `provider_responses` (no se guarda la respuesta cruda de
API-Football en ningún sitio — si la API cambia de formato sin avisar, no
hay forma de auditar retroactivamente qué devolvió exactamente).

### Bloque 3.6 (liquidación/settle) — YA CUBIERTO, verificado

Tests ya existentes confirmando exactamente lo pedido:
`test_settlement_is_idempotent_never_duplicates_result`,
`test_settlement_never_mutates_predictive_fields_of_prediction`
(`backend/tests/unit/test_settlement.py`), más
`test_match_status_lifecycle.py` y `test_void_settlement.py` (aplazados/
cancelados, "void" en vez de "lost"). No se encontró gap real aquí.

### Bloque 4 (Rendimiento del modelo) — YA MUY COMPLETO, verificado

`backend/app/services/evaluation_service.py::real_performance_report` +
`backend/app/backtesting/metrics.py` ya calculan, por segmento
(competición × mercado × versión de modelo), usando SOLO liquidaciones
reales (nunca pendientes, "void" excluido de Brier/ROI):
Brier score, log loss, ECE, reliability curve, accuracy, ROI, PnL total,
max drawdown, desglose por rango de probabilidad (predicha vs frecuencia
empírica real — exactamente el Bloque 5.2.2 del prompt) y por rango de
edge. **Gap real, IMPLEMENTADO Y PROBADO** (commit `c44a04b`, mergeado a
`main`): cada segmento de `real_performance_report` ahora incluye
`accuracy`, `accuracy_confidence_interval_95` (Wilson score, reutilizando
`prediction/uncertainty.py::wilson_interval`, antes solo usado para CI de
`model_probability`) y `low_sample_size` (`True` si
`n_settled < MIN_SAMPLE_SIZE_FOR_RELIABLE_STATS = 30`) — un segmento con
n=5 ya no se reporta igual que uno con n=500: lleva su intervalo de
confianza explícito y una señal clara de muestra insuficiente. 570/570
tests backend en verde.

### Bloque 5 (gráficos) — YA EXISTEN varios, verificado por inventario de componentes

`CalibrationChart.tsx`, `EdgeRoiChart.tsx`, `ModelVsMarketChart.tsx`,
`PerformanceEvolutionChart.tsx`, `ModelPerformanceTabs/Board.tsx` ya
existen en `frontend/components/`. No se auditó su contenido pixel a pixel
contra los 7 gráficos pedidos en el Bloque 5.2 ni contra la skill de
dataviz del proyecto — pendiente real para la siguiente sesión.

### Bloque 6 (calidad API-Football) — prácticamente completado ESTA MISMA SESIÓN (antes de recibir este prompt)

Ya resuelto hoy: paginación real, ventana 1-14 días, panel de diagnóstico
(`/config/odds-diagnostics`), scheduler de cuotas cada hora (confirmado
contra `/status` real: plan Pro, 7500 req/día), bug de corners/doble-oportunidad
contaminando el mercado de goles (confirmado con evidencia real de
producción, Real Madrid Over/Under 2.5), inconsistencia de probabilidad
calibrada vs cruda entre Dashboard y detalle de partido. Todo mergeado a
`main` antes de empezar esta misión.

## B. Cambios implementados en ESTA sesión de la misión

- `backend/app/db/models/modeling.py`: nuevo modelo `PredictionSnapshot`.
- `backend/app/services/prediction_service.py`: archivado antes de borrar.
- `backend/tests/integration/test_api.py`: test de regresión nuevo.
- Commit `02b91b3`, mergeado a `main`. 562/562 tests backend en verde.

## C. Modelo predictivo — sin cambios de cálculo esta sesión

No se ha tocado ningún cálculo de probabilidad en esta sesión de la misión
(solo auditoría). La fiabilidad jerárquica (Bloque 2.2) es el cambio de
cálculo real pendiente de mayor impacto.

## D. Oportunidades — sin cambios de ranking esta sesión

Diseño de fiabilidad jerárquica documentado arriba (sección A), no
implementado todavía.

## E. Base de datos

`prediction_snapshots` (nueva tabla, append-only) ya en `main`,
auto-creada por `Base.metadata.create_all()` (este proyecto no usa
Alembic). Nada más se ha tocado.

## F. Resultados de pruebas

562/562 backend tests pasan (`python -m pytest backend/tests -q`).
Frontend no se tocó en esta sesión de la misión (sin necesidad de rebuild
por este bloque de trabajo).

## G. Pendientes (siguiente paso EXACTO para retomar)

Orden de prioridad recomendado para la próxima sesión (1 y 2.2 completos,
UI de fiabilidad incluida):

1. **`standings_snapshots`** (clasificación histórica por fecha) y
   **`provider_responses`** (respuesta cruda de API-Football archivada) —
   gaps de esquema reales, sin implementar.
2. Auditar los 7 gráficos pedidos en el Bloque 5.2 uno a uno contra los ya
   existentes (`CalibrationChart.tsx`, `EdgeRoiChart.tsx`,
   `ModelVsMarketChart.tsx`, `PerformanceEvolutionChart.tsx`,
   `ModelPerformanceTabs/Board.tsx`), aplicando la skill `dataviz` del
   proyecto. **EN CURSO ahora mismo.**
3. Integración de lesiones/ausencias vía `/injuries` de API-Football
   (requiere verificar cobertura real para las 5 ligas antes de construir
   nada, mismo principio "nunca inventar" ya aplicado todo el día de hoy).
4. Shrinkage bayesiano para la fiabilidad jerárquica (en vez del corte
   duro de 30 observaciones) — evaluar con backtest antes de activar.

**Completado desde el último corte de este documento**: intervalos de
confianza y aviso de muestra pequeña en `/models/performance` (punto 3 de
la lista anterior, ver Bloque 4 arriba), commit `c44a04b` mergeado a
`main`.

Nada de esto requiere credenciales nuevas ni decisiones que no pueda tomar
yo solo — son puramente de ingeniería. Continuar diciendo "sigue con la
misión" o similar retoma exactamente desde el punto 1 de esta lista.
