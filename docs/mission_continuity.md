# Misión: plataforma profesional de predicción, evaluación y aprendizaje — documento de continuidad

Informe de una misión de alcance muy grande (auditoría profunda + rediseño de
calidad del modelo, oportunidades, visualización y persistencia histórica).
No se completa en una sola sesión — este documento es el punto de retoma
exacto. Formato: igual que el informe final pedido (secciones A-G), marcado
como **INTERIM** donde el trabajo aún no está terminado.

Fecha de este corte: 2026-11-18/19. Rama: `claude/football-edge-detector-x0f2ea`.

## A. Diagnóstico inicial — hallazgos reales (con cita de archivo:línea)

### Bloque 1 (calidad del modelo) — MUCHO YA EXISTE, verificado leyendo código:

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
- **Pendiente real (no implementado todavía)**: el prompt pide capturar
  también "contexto disponible en ese momento" (clasificación, forma
  reciente) dentro del snapshot — hoy `Prediction.features_used` solo
  guarda los NOMBRES de las features usadas (`{"feature_names": [...]}`),
  no sus VALORES reales en ese momento. Para reconstruir exactamente qué
  sabía el modelo (pedido explícito del Bloque 3.1), haría falta ampliar
  `features_used`/`PredictionSnapshot` para guardar también los valores,
  no solo los nombres. Riesgo de tamaño de fila (JSON grande) a evaluar.

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
edge. **Gap real**: no se ve un intervalo de confianza explícito ni aviso
de "N bajo" en la RESPUESTA de este endpoint (sí se usa un umbral mínimo
en otros sitios, `MIN_SETTLED_FOR_TRACK_RECORD=30`, pero aquí no se aplica
ni se muestra) — un segmento con n=5 se reporta igual que uno con n=500.

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

1. **Ampliar `PredictionSnapshot`/`features_used`** para guardar valores
   reales de las variables usadas, no solo nombres (Bloque 3.1 completo).
2. **`standings_snapshots`** (clasificación histórica por fecha) y
   **`provider_responses`** (respuesta cruda de API-Football archivada) —
   gaps de esquema reales, sin implementar.
3. Auditar `backend/app/api/routes/models.py::model_performance`
   (confianza/N bajo no se expone) y añadir intervalos de confianza/aviso
   de muestra pequeña a la respuesta del endpoint.
4. Auditar los 7 gráficos pedidos en el Bloque 5.2 uno a uno contra los ya
   existentes, aplicando la skill `dataviz` del proyecto.
5. Integración de lesiones/ausencias vía `/injuries` de API-Football
   (requiere verificar cobertura real para las 5 ligas antes de construir
   nada, mismo principio "nunca inventar" ya aplicado todo el día de hoy).
6. Shrinkage bayesiano para la fiabilidad jerárquica (en vez del corte
   duro de 30 observaciones) — evaluar con backtest antes de activar.

Nada de esto requiere credenciales nuevas ni decisiones que no pueda tomar
yo solo — son puramente de ingeniería. Continuar diciendo "sigue con la
misión" o similar retoma exactamente desde el punto 1 de esta lista.
