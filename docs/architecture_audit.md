# Auditoria de arquitectura y hoja de ruta — "de detector de edge a plataforma de prediccion"

Fecha: 2026-09-20. Punto de partida: commit `7dbad98` (152 tests backend en
verde). Este documento es el Fase 1 (auditoria) pedido por el usuario, y
tambien registra que se implemento despues (Fase 2 parcial) y que queda
pendiente, para que una sesion futura pueda retomarlo sin releer todo el
codigo desde cero.

## 1. Como funciona hoy el sistema (auditoria)

**Fixtures**: `Match` (`backend/app/db/models/matches.py`) con `status`
scheduled|finished (no solo fecha), `matchday`, `home_goals`/`away_goals`
nullable hasta que se conoce el resultado. `MatchOdds` guarda cuotas por
bookmaker/mercado/linea/seleccion con `snapshot_type` (opening/closing).
`MatchStatistics` guarda estadisticas POST-partido, explicitamente marcadas
para no usarse como feature del mismo partido (anti-leakage).

**Modelos**: por competicion se entrena (`services/model_service.py`):
baseline (media de liga), Dixon-Coles (con correccion rho para marcadores
bajos), y un `MarketClassifierModel` (`models/goals/ml_classifier.py`) que
es, literalmente, **un `LogisticRegression` independiente por cada
market_key** (`over_1_5`, `over_2_5`, `under_2_5`, `btts`, `home_win`,
`draw`, `away_win`, `double_chance_*`, `*_team_over_*`) entrenado
directamente sobre las features, cada uno optimizando su propio log loss
sin ninguna restriccion conjunta entre mercados.

**Ensemble**: `models/ensemble/ensemble.py::learn_ensemble_weight` busca por
grid search, **por mercado y de forma independiente**, el peso `w` que
minimiza el log loss de `w * p_estadistico + (1-w) * p_ml` en un holdout.
No hay ninguna restriccion de que los pesos de mercados relacionados
(`home_win`/`draw`/`away_win`, o `over_2_5`/`under_2_5`) coincidan.

**Probabilidad de mercado (estadistica)**: `prediction/market_labels.py`
define TODOS los mercados de goles a partir de UNA UNICA matriz conjunta de
marcador (`probability_from_score_matrix`), particionando el mismo
`score_matrix` con una mascara booleana por mercado. Por construccion
matematica, cualquier grupo de mercados mutuamente excluyentes derivado asi
(`home_win`+`draw`+`away_win`, `over_2.5`+`under_2.5`) suma exactamente 1.

**Mercados y cuotas**: 1X2, Over/Under 1.5/2.5/3.5, BTTS, doble oportunidad
(derivada matematicamente de 1X2 sin vig, nunca una cuota inventada), goles
por equipo (sin cuota de mercado hoy, ninguna fuente la trae). Consenso de
mercado con mediana + deteccion de outliers por MAD
(`market/consensus.py`), quitado de vig (`market/vig.py`).

**Edge/fair odds**: `edge = model_probability - market_probability`
(`prediction/edge.py`), `fair_odds = 1/model_probability`
(`prediction/fair_odds.py`). Documentado en `docs/modeling.md` que un edge
grande NO es sinonimo de buena senhal por si solo — de ahi el
`signal_score` separado.

**Ranking**: dos fases (`prediction/ranking.py`): filtro duro
(`evaluate_quality_gate`, con `ExclusionReason` explicito — nunca una
senhal "desaparece" en silencio) + `signal_score` (soft ranking) para las
que pasan el filtro.

**Calidad de mercado vs confianza del modelo**: separados a proposito
(`prediction/market_quality.py` vs `prediction/confidence.py`) — el primero
mide evidencia de mercado (numero de casas + dispersion), el segundo mide
fiabilidad del modelo (calibracion historica, tamanho de muestra, acuerdo
entre submodelos, calidad de datos, cobertura de mercado).

**Historico/settlement (antes de este cambio)**: `PredictionResult` ya
existia (`outcome: bool`, `settled_at`), y
`services/evaluation_service.py::evaluate_settled_predictions` ya calcula
Brier/log loss/ROI hipotetico por mercado sobre predicciones YA GUARDADAS de
partidos YA finalizados — sin recalcular nada con el modelo actual. Lo que
faltaba: exponer esto como historico navegable (endpoints/frontend), y
completar el snapshot con `calibrated_probability`/`signal_score`
congelados y `actual_result` literal (ver seccion 3).

**Persistencia/migraciones**: sin Alembic. `db/database.py::init_db()` hace
`Base.metadata.create_all()` + `_sync_missing_columns()`, que anhade
columnas NUEVAS (nullable) a tablas ya existentes via `ALTER TABLE ADD
COLUMN`, sin tocar filas existentes ni borrar nada. Es el patron ya
establecido en este repo (documentado en el propio archivo) — este cambio
lo sigue en vez de introducir Alembic de golpe.

## 2. RAIZ del problema de inconsistencia entre mercados (verificado leyendo codigo, no asumido)

La hipotesis del usuario era correcta: **el culpable es
`MarketClassifierModel` + `learn_ensemble_weight`**, no un fallo de
normalizacion de cuotas ni de matching.

Cadena exacta:
1. `probability_from_score_matrix` (estadistico) es consistente por
   construccion: `home_win`+`draw`+`away_win`=1 y `over_2_5`+`under_2_5`=1
   siempre, porque salen de particionar la MISMA matriz conjunta.
2. `MarketClassifierModel.fit` entrena un `LogisticRegression` **por
   mercado, por separado**, cada uno viendo solo su propia etiqueta
   binaria. Nada le dice al clasificador de `over_2_5` que existe un
   `under_2_5` con el que debe sumar 1.
3. `learn_ensemble_weight` elige, **independientemente para cada
   market_key**, el peso `w` que minimiza SU log loss. En la practica los
   pesos de `over_2_5` y `under_2_5` (o de `home_win`/`draw`/`away_win`) casi
   nunca coinciden exactamente.
4. Resultado: `p_final = w*p_stat + (1-w)*p_ml` para cada mercado del grupo
   por separado, y la suma del grupo ya no es 1 en cuanto CUALQUIERA de los
   pesos ML del grupo es > 0 y los clasificadores discrepan del score matrix
   (que es la situacion normal, no un caso raro).

Esto se confirmo con un test end-to-end nuevo
(`backend/tests/unit/test_market_consistency.py::test_predict_markets_for_table_is_internally_consistent_despite_disagreeing_ml_model`)
que reproduce el ejemplo literal del brief (over_2.5=80%, under_2.5=70%) con
un `ml_model` de mentira y confirma que, SIN el fix, la suma es 1.50, no 1.0.

### Fix implementado (Fase 2)

`prediction/market_labels.py::MUTUALLY_EXCLUSIVE_GROUPS` +
`renormalize_mutually_exclusive_groups`: despues del blending del ensemble
(en `prediction/predictor.py::predict_markets_for_table`, ANTES de calcular
edge/fair_odds/explicacion), cada grupo mutuamente excluyente completo se
proyecta sobre el simplex (`p_i / sum(p_grupo)`), fila a fila. Esto:

- Es una operacion matematicamente principiada (proyeccion sobre el simplex
  de probabilidad), no "esconder una de las dos": conserva toda la
  informacion relativa del ensemble (cual seleccion es mas probable y por
  cuanto), solo restaura la restriccion de que son mutuamente excluyentes.
- Se aplica tambien en entrenamiento (`services/model_service.py`) antes de
  ajustar los calibradores, para que `calibrated_probability` se calibre
  sobre la MISMA distribucion que se sirve en produccion (si se calibrara
  sobre la version cruda, el numero guardado no correspondería a la
  probabilidad realmente usada para edge/ranking).
- Es un no-op cuando no hay modelo ML (`ml_model=None`) o cuando los pesos
  del ensemble son iguales entre los mercados de un grupo: en ese caso el
  input ya sumaba 1 y la renormalizacion no cambia nada (verificado por los
  tests).

**Limite honesto de este fix (no es la solucion completa)**: renormalizar
DESPUES de mezclar corrige el sintoma con un mecanismo principiado, pero la
causa arquitectonica de fondo (clasificadores binarios independientes en
vez de un modelo conjunto restringido al simplex) sigue ahi. La correccion
definitiva seria sustituir `MarketClassifierModel` por un clasificador
**multinomial conjunto** para cada grupo mutuamente excluyente (softmax
sobre las clases del grupo en vez de un sigmoid por clase), entrenado y
evaluado igual que el resto (mismo Brier/log loss walk-forward). Esto queda
como trabajo futuro documentado aqui (ver seccion 6), porque es un cambio
de modelo que requiere su propio backtest de validacion antes de
reemplazar el actual — no se hace a ciegas bajo presion de tiempo.

## 3. Cambios de esquema (snapshot inmutable, seccion 9 del brief)

Anhadido a `Prediction` (nullable, via el patron `_sync_missing_columns`
existente, sin Alembic):
- `calibrated_probability: float | None` — salida de un
  `IsotonicRegression` ajustado en el holdout de entrenamiento (nunca sobre
  los mismos datos), solo cuando hay >= `MIN_CALIBRATION_MATCHES` (60)
  partidos en el holdout para ese mercado; si no, `None` explicito (nunca
  inventado). Guardado ADEMAS de `model_probability` (no en su lugar): se
  decidio NO cambiar que probabilidad dirige edge/ranking (ver seccion 6,
  "decisiones deliberadamente no tomadas").
- `signal_score: float | None` — el `signal_score` calculado y CONGELADO en
  el momento de generar la prediccion (`services/prediction_service.py`),
  con los datos de mercado vigentes en ESE momento. Nunca se recalcula
  despues con pesos/umbrales mas nuevos.

Anhadido a `PredictionResult`:
- `actual_result: str | None` — pensado para el resultado literal
  (`"2-1"`, `"over"`, etc.), distinto del booleano `outcome` (que ya hacia
  de `is_correct`). **No cableado todavia** (ver seccion 6): el settlement
  pipeline que lo rellena es Fase 4, no completada en esta sesion.

`model_version_id` en `Prediction` y `ModelVersion.calibration_method` ya
existian; ahora `calibration_method` se rellena de verdad (`"isotonic"` o
`None`) en vez de estar siempre a `None`.

## 4. `signal_score`: formula completa (Fase 2, seccion 1 del brief)

Documentado en detalle en el docstring de `prediction/ranking.py::signal_score`
y en `docs/modeling.md`. Resumen:

```
signal_score = model_probability^2
             * max(edge, 0)
             * confidence
             * data_quality
             * market_quality_multiplier   # HIGH=1.0 / MEDIUM=0.85 / LOW=0.65
             * market_track_record          # [0,1], neutro=1.0 por defecto
```

- `model_probability^2`, `edge`, `confidence`, `data_quality`: formula
  original de la sesion anterior, sin cambios (ya pasaba los tests que
  validan sus propiedades: castiga probabilidad baja mas que edge en
  bruto, hunde a ~0 cuando el edge es ~0 aunque la probabilidad sea 98%).
- `market_quality_multiplier` (NUEVO): antes, "cuantas casas respaldan la
  cuota" solo entraba diluido dentro de `confidence` (uno de sus 5
  componentes ponderados al 20%) y SIN mirar dispersion entre casas. Ahora
  `market_quality_tier` (HIGH/MEDIUM/LOW, que SI mira dispersion) es un
  factor propio y explicito del `signal_score`, para que una cuota
  respaldada por 1 sola casa discrepante no pueda ganar el ranking de
  "mejor senhal" solo por probabilidad/edge nominales altos.
- `market_track_record` (NUEVO, parametro opcional): pensado para el
  rendimiento historico observado de ESE tipo de mercado (constraint del
  brief: "historico de ese tipo de mercado"). Por defecto 1.0 (neutro: un
  mercado sin historico suficiente no se penaliza por falta de dato). **No
  esta cableado automaticamente en el pipeline de generacion todavia** —
  requiere que el pipeline de settlement (Fase 4) este completo para poder
  calcular calibracion historica por mercado de forma continua. El
  parametro existe y esta testeado
  (`test_signal_score_market_track_record_defaults_to_neutral`) para que
  conectar esto despues sea cambiar una llamada, no rediseñar la formula.

Deliberadamente NO se uso `calibrated_probability` en vez de
`model_probability` dentro del `signal_score`/edge: adoptar la probabilidad
calibrada como la que dirige el ranking es una decision de producto que
requiere validar OUT-OF-SAMPLE que mejora el ranking real (no solo que
mejora el Brier score en el holdout de calibracion, que es circular si se
usa para lo mismo). Se documenta como decision pendiente, no como olvido.

## 5. Auditoria de disponibilidad de datos por mercado (seccion 26 del brief)

| Mercado | Historico | Cuotas | Modelo disponible | Muestra | Estado |
|---|---|---|---|---|---|
| 1X2 (home/draw/away) | ALTA (Bet365 en dataset historico + h2h de The Odds API) | ALTA | SI (Dixon-Coles + score matrix + ML) | Big5, miles de partidos/temporada | **CORE** |
| Over/Under 2.5 goles | ALTA (misma fuente, linea principal) | ALTA | SI | igual | **CORE** |
| Over/Under 1.5 / 3.5 goles | ALTA (derivable del mismo dataset) | MEDIA (`alternate_totals`, anhadido despues, sin verificar en produccion aun segun `docs/data_sources.md`) | SI | igual | **CORE** (goles), cuota con menor cobertura que la linea 2.5 |
| BTTS | ALTA (derivable de goles reales) | MEDIA (mercado `btts` de The Odds API, anhadido despues, sin verificacion end-to-end en produccion todavia) | SI | igual | **CORE** |
| Doble oportunidad (1X/X2/12) | ALTA (derivada matematicamente de 1X2, no depende de una fuente propia) | ALTA (derivada del no-vig de h2h, nunca None si hay h2h) | SI | igual | **CORE** |
| Goles por equipo (home/away over 0.5/1.5) | ALTA (goles reales por equipo) | **NINGUNA fuente actual la trae** | SI (modelo, sin cuota) | igual | **FUTURE** para señal de mercado; ya vive en `/predictions/model-only` |
| Asian Handicap | — | No integrada | No | — | **FUTURE** (Prioridad 2, sin trabajo empezado) |
| Draw No Bet | — | No integrada (derivable matematicamente igual que doble oportunidad, en cuanto se decida priorizar) | Derivable del score matrix | — | **FUTURE** (Prioridad 2, tecnicamente trivial de anhadir, no priorizado esta sesion) |
| Corners O/U (total y por equipo) | MEDIA (dataset historico SI trae corners; ver `MatchStatistics`) | **Aparcado por decision de producto (2026-09-18)**: unica fuente viable (API-Football) exige plan de pago | Parcial (`TotalCountPoissonModel` YA EXISTE y entrena, `models/ensemble` no lo cubre) | Big5 | **FUTURE** (modelo listo, sin cuota real fiable hoy) |
| Cards O/U (total y por equipo) | MEDIA (igual, dataset historico trae tarjetas) | Igual que corners: aparcado | Igual que corners | Big5 | **FUTURE** |

Ninguna decision de esta sesion contradice esta tabla: no se ha
implementado Asian Handicap/Draw No Bet/Corners/Cards como señales reales,
solo se documenta su estado (varios ya tenian trabajo de modelo hecho en
sesiones previas, pero sin cuota fiable no compiten en el ranking — quedan
en `/predictions/model-only`, consistente con el criterio "nunca inventar
`market_probability`").

## 6. Que queda pendiente (para retomar sin releer todo)

Esta sesion completo Fase 1 (esta auditoria) y una parte acotada pero real
de Fase 2 (root cause + fix de consistencia, `calibrated_probability` +
`signal_score` persistidos, formula de `signal_score` extendida y
documentada). **No** se llego a completar Fase 3-8 por el volumen del
encargo frente al tiempo disponible en esta sesion. Se prioriza dejar el
repo verde y documentado en vez de dejar cambios a medias sin terminar.

Pendiente, en orden sugerido:

1. **Fase 3 — Mejor prediccion por partido / mejor senhal del dia**:
   - Selector: para cada `match_id`, elegir la `Prediction` con mayor
     `signal_score` entre las que pasan `evaluate_quality_gate` (ya hay
     `rank_signals` para el ranking global; falta agrupar por partido).
   - Endpoint nuevo o extension de `/predictions/best` para exponer
     "mejor prediccion" por partido en listados de proximos partidos.
   - Requisitos minimos de la seccion 16 (mercado real, cuota valida,
     bookmakers suficientes, mercado reciente, modelo calibrado, edge
     minimo, sin contradiccion) — el filtro duro actual ya cubre casi
     todos salvo "sin contradiccion", que ahora es trivial de anhadir
     gracias al fix de la seccion 2: comprobar que ninguna otra prediccion
     del mismo grupo mutuamente excluyente para el mismo partido tiene
     `model_probability` tambien > 0.5 (deberia ser imposible tras el fix,
     pero es la comprobacion de seguridad barata de anhadir).
   - Etiquetas HIGH_PROBABILITY vs BEST_VALUE (seccion 3/18): con
     `signal_score` ya separado de probabilidad pura, es una funcion de
     comparacion directa, sin nuevo calculo.

2. **Fase 4 — Historico/settlement completo**:
   - Rellenar `actual_result` (columna ya anhadida, sin cablear) al
     liquidar en `evaluation_service.py` (o un nuevo `settlement_service.py`
     dedicado), junto con `outcome`/`settled_at` que ya se escriben.
   - Endpoints `/fixtures/finished`, `/history`, `/history/{id}` (o
     adaptando los nombres a los ya existentes) filtrando por
     `Match.status == "finished"`, nunca por fecha.
   - Frontend: pestaña Historico.

3. **Fase 5 — Metricas/calibracion sobre datos reales**: el modulo ya
   existe (`backtesting/metrics.py`, `backtesting/calibration.py`,
   `evaluation_service.py`) — falta el endpoint `/model/performance` que
   lo exponga segmentado (mercado/liga/rango de cuota/rango de
   probabilidad/rango de edge/version de modelo) y el dashboard frontend.
   Con esto en marcha, conectar `market_track_record` (seccion 4) deja de
   ser un parametro sin usar.

4. **Fase 6 — Frontend/UX**: navegacion Inicio/Proximos/Señales/Historico/
   Modelo, badges de anomalias (CONTRADICCION -- ya casi imposible tras el
   fix, pero conviene un test de regresion visible en UI; SEÑAL DE BAJA
   FIABILIDAD; OUTLIER; HIGH PROBABILITY/LOW VALUE), actualizar
   `frontend/types/index.ts` + `frontend/lib/api.ts` para los campos nuevos
   (`calibrated_probability`, `signal_score`) en las respuestas donde se
   expongan.

5. **Fase 7 — Los tests de la seccion 27 no cubiertos todavia**: de los 16,
   esta sesion anhadio cobertura directa para "no se seleccionan
   simultaneamente Over y Under como señales fuertes" y "probabilidades
   mutuamente excluyentes son coherentes" (`test_market_consistency.py`) y
   para el scoring de "mejor prediccion"/"high probability != high value"
   parcialmente (`test_ranking.py`, ya existia antes). Faltan: partido
   finalizado desaparece de Home / aparece en Historico, resultado se
   guarda, prediccion historica inmutable end-to-end, prediccion
   acertada/fallada correctamente determinada via el pipeline completo,
   MODEL_ONLY no entra en señales (probablemente ya cubierto
   indirectamente por el filtro duro, pero sin test explicito nombrado
   asi), partidos futuros no aparecen en jornada actual (related a
   `round_service.py`, ya testeado parcialmente), snapshots de cuotas
   funcionan, model_version queda guardada (ya se guarda, falta test
   explicito).

6. **Refactor arquitectonico completo del punto 2** (clasificador
   multinomial conjunto en vez de N binarios independientes): la solucion
   "de raiz" definitiva, no urgente porque el fix de renormalizacion ya
   corrige la inconsistencia observable, pero documentada aqui para no
   perderla de vista.

7. **CompetitionConfig centralizado** (seccion 14/15 del brief): hoy
   `Competition` (`db/models/core.py`) no tiene campos `priority`/`enabled`/
   tier CORE-vs-EXPERIMENTAL. No se toco esta sesion por acotar el alcance
   al problema de consistencia de mercados, que era el pedido explicito
   mas critico. Anhadir esos campos es un cambio de esquema pequenho
   (mismo patron `_sync_missing_columns`) cuando se aborde Fase 6/priorizacion
   de ligas.

## 7. Estado de los tests al cierre de esta sesion

`cd backend && pytest -q` (o `make test` desde la raiz): **160 passed**
(152 preexistentes + 8 nuevos: 6 en `test_market_consistency.py`, 2 en
`test_ranking.py`), 0 failed, solo warnings de deprecacion de
`datetime.utcnow()` preexistentes (no introducidos por este cambio).

No se toco nada de `frontend/` en esta sesion (ningun endpoint/contrato
existente cambio de forma), asi que no se ejecuto `npm run build` — no
deberia haber ningun impacto, pero queda pendiente confirmarlo en la
proxima sesion antes de anhadir los endpoints nuevos de Fase 3/4 que si
tocaran el contrato.

## 8. Fase 3-8 (sesion siguiente, 2026-09-20): que se completo

Esta sesion retomo el punto exacto donde quedo la seccion 6 y completo
Fase 3 a Fase 7 en el orden sugerido, mas esta Fase 8 (cierre). Resumen
por fase, con puntero exacto de codigo para cada pieza:

**Fase 3 — mejor prediccion / mejor senhal del dia / anomalias**
(`backend/app/prediction/anomaly.py`, nuevo modulo):
- `compute_anomaly_flags(prediction, match_predictions, settings)`:
  CONTRADICCION (misma logica de grupo mutuamente excluyente que
  `market_labels.MUTUALLY_EXCLUSIVE_GROUPS`, red de seguridad ahora que
  el fix de renormalizacion deberia hacerla casi imposible),
  SENAL_BAJA_FIABILIDAD (`confidence`/`data_quality` < 0.4),
  OUTLIER (`market_quality_tier` == LOW), HIGH_PROBABILITY_LOW_VALUE
  (`model_probability >= 0.75` y `edge < 0.03`). Umbrales documentados en
  el docstring del modulo, no repartidos por el codigo.
- `best_prediction_per_match(predictions_by_match)`: usa `rank_signals`
  (filtro duro) + excluye CONTRADICCION, nunca elige por probabilidad/
  edge/cuota en crudo.
- `/predictions/top-signals` y `/predictions/best`
  (`backend/app/api/routes/predictions.py`) excluyen ahora cualquier
  prediccion con CONTRADICCION (nunca Over y Under como señales fuertes a
  la vez, seccion 2).
- Nuevo `/predictions/best-of-day`: filtro MAS estricto (edge minimo =
  2x el umbral general, calibracion disponible, sin contradiccion), con
  cada requisito trazable en `explanation`; devuelve `prediction: null`
  cuando nadie lo cumple (nunca se rebaja el filtro para forzar un
  resultado).
- `/matches/{id}/predictions` expone `anomaly_flags`/`is_best_prediction`
  por prediccion (`backend/app/api/routes/matches.py`).

**Fase 4 — settlement / Historico**
(`backend/app/services/evaluation_service.py::settle_finished_predictions`):
- Crea `PredictionResult` (tabla YA EXISTENTE desde Fase 2, nunca usada
  hasta ahora) para cada `Prediction` de un partido `status="finished"`
  sin resultado todavia. Idempotente (no duplica si se llama dos veces).
  **Nunca** toca los campos predictivos de `Prediction` -- solo INSERTA
  una fila en la tabla separada `prediction_results` (relacion 1-a-1).
  `actual_result` es el marcador literal (`"2-1"`), no una interpretacion
  por mercado (mas simple y siempre derivable, sin inventar nada).
- Cableado en el comando `evaluate` del CLI (`backend/app/cli.py`), que ya
  estaba dentro de `refresh` -- ningun comando nuevo que acordarse de
  ejecutar.
- Endpoints nuevos: `/matches/fixtures/finished` (filtra por
  `Match.status`, nunca por fecha), `/history`, `/history/{match_id}`
  (`backend/app/api/routes/history.py`).
- Frontend: `frontend/app/history/page.tsx` (listado con mejor prediccion
  + ACERTADA/FALLADA/sin resolver) y `frontend/app/history/[id]/page.tsx`
  (detalle: TODAS las predicciones del partido vs la realidad).

**Fase 5 — metricas/calibracion reales**
(`backend/app/services/evaluation_service.py::real_performance_report`):
- Reusa integramente `backtesting/metrics.py`
  (`model_quality_metrics`, `performance_by_probability_bucket`,
  `performance_by_edge_bucket`, `market_strategy_metrics`), pero
  aplicado SOLO sobre predicciones con `PredictionResult` (liquidadas),
  segmentado por competicion/mercado/version de modelo.
- Endpoint `GET /models/performance` (`backend/app/api/routes/models.py`,
  declarado ANTES de `/models/{model_id}` a proposito para no colisionar
  con la ruta de id entero).
- Frontend: `frontend/app/model/page.tsx` -- curva de calibracion
  predicho-vs-real por bucket, resultado real por rango de edge, ROI
  hipotetico, sin libreria de graficos nueva (tablas + barras CSS con la
  misma paleta del resto del tablero).
- `market_track_record` (parametro de `signal_score`, Fase 2) **sigue sin
  cablearse automaticamente**: ahora que `/models/performance` existe,
  conectar "1 - error de calibracion historico del mercado" es una
  llamada mas, no un rediseño -- se dejo fuera de esta sesion por acotar
  alcance (habria significado recalcular y cachear ese numero en el
  pipeline de generacion de predicciones, un cambio con mas superficie
  que las fases pedidas explicitamente).

**Fase 6 — frontend/UX**:
- Nav Inicio/Señales/Histórico/Modelo (`frontend/app/layout.tsx`).
- `frontend/components/BestOfDayWidget.tsx` en Inicio (usa
  `/predictions/best-of-day`, muestra honestamente "ninguna" cuando no
  hay candidata).
- `frontend/components/AnomalyBadge.tsx`: badges de las 4 anomalias de
  Fase 3, integrados en `PredictionRow.tsx` junto a la estrella ★ de
  "mejor prediccion del partido".
- Colores solo transmiten estado (ambar=aviso, verde/rojo=acertada/
  fallada real, nunca "todo verde" por edge positivo).
- Auditoria de copy: no se encontro (ni se anhadio) "segura"/"ganadora"/
  "100% segura" en ningun texto del frontend (`grep -rniE` limpio antes
  y despues del cambio).
- **No hecho** (fuera del alcance pedido, no roto): rediseño visual
  completo de las cards existentes (`MatchCard`/`PredictionRow` ya tenian
  jerarquia razonable de una sesion anterior); esta sesion extendio esos
  componentes en vez de rehacerlos, para no arriesgar regresiones
  visuales sin pedido explicito de rediseño total.

**Fase 7 — tests**: +19 tests nuevos en total sobre los 160 de cierre de
Fase 2 (`test_settlement.py`: 9; `test_signal_exclusions.py`: 2; el resto
de items de la seccion 27 del brief -- jornada actual vs futura, snapshots
de cuotas -- ya tenian cobertura preexistente en `test_round_service.py` /
`test_data_service_odds_preservation.py`, confirmado leyendo esos ficheros
en vez de duplicar tests). **171 passed, 0 failed** al cierre de esta
sesion.

**No leakage / inmutabilidad (verificado, no solo documentado)**:
`test_settlement_never_mutates_predictive_fields_of_prediction` congela
`model_probability`/`edge`/`signal_score`/`market_odds`/`model_version_id`
ANTES de liquidar y comprueba que son bit-a-bit iguales despues -- el
mecanismo de inmutabilidad no es solo el diseño de esquema (tabla
`prediction_results` separada, 1-a-1, sin UPDATE posible sobre
`Prediction`), esta comprobado en un test real end-to-end usando el
pipeline de entrenamiento/prediccion real, no un mock.

**Fase 8 — build final**: `cd backend && pytest -q` → **171 passed**, 0
failed (solo warnings preexistentes de `datetime.utcnow()` y de
`FutureWarning`/`UserWarning` de pandas/sklearn, ninguno introducido por
este cambio). `cd frontend && npm run build` → compila limpio, 8 rutas
(`/`, `/history`, `/history/[id]`, `/matches/[id]`, `/model`,
`/signals/[id]`, `/top-signals`, `/_not-found`).

## 9. Que queda deferido (para una sesion futura)

En el mismo orden de prioridad que dejaria el equipo si se retomara hoy:

1. **`market_track_record` sin cablear automaticamente** (ver seccion 5
   de este documento): el endpoint `/models/performance` ya da todo lo
   necesario (Brier/calibracion real por mercado); falta que
   `services/prediction_service.py::generate_predictions_for_competition`
   consulte ese historico al generar cada prediccion nueva y se lo pase a
   `signal_score`. Requiere decidir una ventana temporal razonable (¿ultimos
   N partidos liquidados de ese mercado? ¿esa competicion o todas?) antes
   de implementarlo -- no es solo mecanica, es una decision de producto
   pequenha pero real.
2. **Refactor arquitectonico del clasificador multinomial conjunto**
   (ver seccion 6, punto 6, sin cambios: sigue sin ser urgente).
3. **`CompetitionConfig` centralizado / tiers CORE-vs-EXPERIMENTAL** (ver
   seccion 6, punto 7, sin cambios: sigue sin abordarse).
4. **Corners/Cards/Asian Handicap/Draw No Bet siguen FUTURE-tier**, sin
   cambios respecto a la tabla de la seccion 5 (decision de producto de
   2026-09-18, no revisada ni cuestionada esta sesion: ninguna de esas
   fuentes de cuota nuevas se ha vuelto viable, no se ha buscado una
   razon para anhadirlas).
5. **Rediseño visual completo del frontend** (seccion 25 del brief, mas
   alla de lo ya hecho en Fase 6): esta sesion prioriza extender
   componentes existentes (badges, widget nuevo, paginas nuevas) sobre
   rehacer la jerarquia visual de cero, que no se pidio explicitamente y
   habria sido mas riesgo que valor dado el tiempo disponible.
6. **Segmentacion adicional de `/models/performance`** por rango de cuota
   explicito (hoy segmenta por competicion/mercado/version de modelo, y
   cada segmento YA incluye desglose por bucket de probabilidad/edge --
   falta un filtro de query explicito `odds_min`/`odds_max` si se quiere
   acotar el propio segmento por rango de cuota, no solo ver el
   desglose).

## 10. Fase 9 (sesion 2026-11): "motor profesional de prediccion"

El usuario pidio explicitamente "el modelo mas preciso que haya en el
mundo" y dio permiso para decidir e implementar todo sin pedir
confirmacion por fase, tras una auditoria Fase 1 propia de esta sesion
(no la de las secciones 1-9 de arriba, que son de la sesion anterior).
Esa auditoria Fase 1 identifico gaps reales frente a un brief mucho mas
ambicioso (CatBoost/LightGBM, Poisson bivariado, MLflow, SHAP, Shin's
method, deteccion de drift, CLV, cuantificacion de incertidumbre) y
propuso un orden de implementacion por impacto/esfuerzo. Esta sesion
ejecuto esa lista completa, con la misma disciplina que las sesiones
anteriores: nunca sustituir un componente que funciona sin backtest real,
tests en verde en cada commit, nunca inventar un numero sin datos
suficientes.

**1. Clasificador multinomial conjunto por grupo** (la correccion de RAIZ
que la seccion 6 punto 6 de este documento dejaba pendiente):
`models/goals/multinomial_classifier.py::MultinomialGroupClassifier` -- un
unico modelo por grupo mutuamente excluyente (home_win/draw/away_win,
over_2_5/under_2_5) en vez de N binarios independientes, con probabilidades
que suman 1 por construccion. **Nunca sustituye al clasificador
independiente a ciegas**: `models/goals/model_selection.py` compara ambos
(mas LightGBM, ver punto 2) por log loss real en el holdout, GRUPO A GRUPO
(nunca mercado a mercado dentro del mismo grupo -- un bug real detectado
por un test propio de esta sesion antes de llegar a produccion: elegir
ganadores distintos para "home_win" y "draw" reintroduce la misma
inconsistencia que se queria corregir). La decision queda trazada en
`ModelVersion.metrics["markets"][mercado]["ml_winner"]`.

**2. LightGBM como tercer candidato** (`models/goals/gradient_boosting.py`):
se entrena SIEMPRE junto a los otros dos y compite por el mismo backtest.
Hiperparametros deliberadamente conservadores tras comprobar
EMPIRICAMENTE que una configuracion mas agresiva sobreajusta en datasets
de unos cientos de partidos (peor que adivinar al 50% en el holdout) --
documentado en el modulo, no solo en el commit. Explicabilidad real via
SHAP (`prediction/shap_explanation.py`) para cuando este candidato gana,
ya que no expone coeficientes lineales.

**3. Ensemble generalizado de 2 a N modelos**
(`models/ensemble/ensemble.py::learn_ensemble_weights`): grid search sobre
pesos no negativos que suman 1 para cualquier numero de candidatos, no
solo 2 -- paso previo necesario para los puntos 1 y 2.

**4. `market_track_record` conectado por fin a `signal_score`**
(seccion 4 de este documento dejaba el parametro sin cablear):
`services/evaluation_service.py::market_track_records()` calcula "1 -
error de calibracion historico" por (competicion, mercado) sobre
predicciones YA LIQUIDADAS, con un minimo de 30 liquidaciones antes de
confiar en el numero. Cableado en los 3 endpoints de ranking real y en el
`signal_score` CONGELADO al generar la prediccion.

**5. Cuantificacion de incertidumbre** (`prediction/uncertainty.py`):
intervalo de Wilson sobre `model_probability` usando el tamanho de
muestra efectivo (partidos previos conocidos por equipo), ensanchado por
el desacuerdo entre submodelos. `Prediction.uncertainty_low/high`
persistidos y mostrados en el frontend junto a la probabilidad.

**6. Deteccion de drift** (`monitoring/drift.py`): Population Stability
Index (metrica estandar de scoring de riesgo crediticio, umbrales
0.10/0.25 ampliamente usados) comparando los ultimos N partidos jugados
contra el resto del historico, por feature. Comando CLI
`football-edge detect-drift`, puramente informativo, nunca bloquea
refresh/train.

**7. Shin's method: investigado, NO implementado.** Se intento verificar
la formula cerrada (parametro z, expresion con raiz cuadrada) contra una
fuente autoritativa antes de escribir codigo -- el entorno de esta sesion
no tenia acceso de red a ninguna fuente que lo confirmara (arxiv.org,
karlwhelan.com, en.wikipedia.org, todas bloqueadas por la politica de red
del entorno). Implementar una formula de probabilidad sin poder
verificarla arriesgaba corromper `market_probability`/edge en silencio --
peor que no implementarla. Documentado en `market/vig.py` con esta
justificacion explicita; sigue en pie la normalizacion proporcional.

**8. Infraestructura de CLV (Closing Line Value)**: tabla nueva
APPEND-ONLY `MatchOddsHistory` (`db/models/matches.py`), escrita en
paralelo a `MatchOdds` (que sigue borrando-y-reemplazando el snapshot
vigente) -- limite honesto, solo existe desde este cambio, CLV nunca
puede calcularse retroactivamente. `market/clv.py` + cableado en
`settle_finished_predictions`. Bug real encontrado escribiendo los tests
(solo reproducible con la suite completa, nunca en aislamiento): `Match`
no tenia cascade hacia `MatchOddsHistory`, asi que borrar un partido
(`reset_scheduled`) dejaba historico huerfano que SQLite podia "adoptar"
en un partido nuevo que reciclara el mismo id -- corregido con el mismo
cascade que `odds`/`statistics` ya tenian.

**9. CompetitionConfig** (`tier`/`enabled`/`priority` en `Competition`,
seccion 14/15 de este documento): nullable a proposito, porque
`_sync_missing_columns` anhade columnas sin DEFAULT a nivel de BD y una
columna NOT NULL habria dejado las competiciones ya existentes con NULL,
invisibles ante un filtro `WHERE enabled = true`.

**10. MLflow** (`services/mlflow_tracking.py`): desactivado por defecto,
nunca propaga un fallo hacia el entrenamiento real. Backend SQLite local
por defecto, no file store plano -- MLflow >= 3 pone ese backend en
maintenance mode y lo rechaza salvo que se fuerce explicitamente,
descubierto escribiendo el test de este modulo.

**Disciplina mantenida en los 10 puntos**: cada uno se commiteo por
separado con la suite completa en verde antes del siguiente (de 203 tests
al empezar esta fase a 256 al cerrarla), lint sin anhadir deuda nueva
sobre la ya existente, y `npm run build` del frontend verificado despues
de cada cambio que tocaba la API. Tres bugs reales encontrados y
corregidos ANTES de llegar a produccion, todos por tests escritos en esta
misma sesion (no reportados por el usuario): la seleccion de modelo ML
mercado-a-mercado dentro de un grupo (punto 1), el cascade de borrado
faltante para `MatchOddsHistory` (punto 8), y el file store de MLflow
deprecado (punto 10) -- los tres documentados en sus modulos
correspondientes, no solo aqui.

**Honestamente, no es "el modelo mas preciso del mundo"** -- esa frase no
tiene una definicion verificable. Lo que si es cierto: cada mejora de esta
fase se valida contra un backtest real antes de usarse en produccion
(nunca una sustitucion a ciegas), y donde no se pudo verificar algo con
confianza suficiente (Shin's method) se dejo documentado en vez de
adivinado. Pendiente para una sesion futura, en el mismo orden de
prioridad que se dejaria hoy:

1. Conectar CLV/incertidumbre a `signal_score`/`market_quality` (hoy son
   informativos, mostrados pero no usados para rankear).
2. Backtest real comparando el clasificador multinomial/LightGBM contra
   el baseline anterior con datos de produccion reales (esta sesion solo
   verifico con datos sinteticos y el holdout de entrenamiento normal).
3. Revisar Shin's method si en el futuro hay acceso de red a una fuente
   verificable, o si el usuario pega el texto exacto del paper.
4. Extender `GradientBoostingMarketModel` a una variante conjunta por
   grupo (como `MultinomialGroupClassifier` pero con arboles) si el
   binario independiente demuestra ser el cuello de botella.

## 11. Fase 10 (sesion 2026-11, continuacion): "motor profesional 2.0" — revision del ranking

El usuario reporto el problema real de raiz de todo este bloque, con un
caso concreto (Getafe vs Atletico Madrid, doble oportunidad 1X: modelo
81.9% frente a mercado 41.9%, edge +40pp) y pidio una revision completa
del motor de ranking/edge/EV, auditando primero, implementando y
validando despues, sin pedir confirmacion fase a fase.

### 11.1 Problemas encontrados (auditoria con codigo real, no supuestos)

1. **`signal_score` (produccion) usa `model_probability` CRUDO, nunca
   `calibrated_probability`** (`prediction/ranking.py`) — documentado ya
   como decision diferida, pero nunca resuelta: una probabilidad extrema
   sin calibrar domina el ranking via `model_probability**2`.
2. **`signal_score` NUNCA usa el intervalo de incertidumbre**
   (`uncertainty_low`/`uncertainty_high`, ya calculado y persistido desde
   la Fase 9) — una predicción con intervalo [0.40, 0.95] puntuaba
   EXACTAMENTE igual que otra con [0.78, 0.85] al mismo punto estimado.
   Esta es la causa mecanica mas directa del caso reportado.
3. **El EV (`compute_expected_value`) tambien usa `model_probability`
   crudo**, nunca una version conservadora — contradice la propia
   formula de EV que pide rigor estadistico.
4. **La calibracion de produccion SI existe y SI se ajusta sobre el
   ENSEMBLE real** (`model_service.py`, `IsotonicCalibrator` sobre la
   probabilidad del ensemble ya renormalizada, en un holdout real) —
   correccion a una suposicion propia de esta misma sesion horas antes.
   Pero: (a) usa un UNICO split train/holdout, nunca walk-forward: mucho
   menos riguroso que el motor de backtest que existe para otras cosas;
   (b) solo usa isotonic — `PlattCalibrator` existe en el codigo
   (`models/calibration/calibrators.py`) desde la Fase 9 pero nunca se
   habia usado en el entrenamiento real; (c) el comando
   `calibration-report` de la CLI (el unico que se habia corrido contra
   datos reales hasta ahora) NO evalua esto: evalua calibracion sobre
   `DixonColesModel` AISLADO via walk-forward
   (`backtesting/engine.py::run_walk_forward_backtest`, que solo sabe
   entrenar un `GoalsModel` suelto, nunca el ensemble con el clasificador
   ML) — la herramienta de validacion rigurosa probaba el modelo
   EQUIVOCADO. Nunca se habia podido responder con confianza si calibrar
   el ensemble real (con Platt o con isotonic) mejora algo.
5. **Vig removal**: solo normalizacion proporcional (`market/vig.py`) —
   Shin's method sigue deliberadamente sin implementar (ver seccion 10,
   punto 7), sin cambios en esta fase.
6. **`market_track_record` SI esta conectado de verdad**
   (`evaluation_service.py::market_track_records`, 1 − ECE historico real
   por competicion/mercado, >= 30 liquidaciones) — otra correccion a una
   suposicion propia: esto ya funcionaba bien desde la Fase 9.
7. **No existia ningun motor de backtest que supiera evaluar el ENSEMBLE
   completo (DC + ML + pesos aprendidos) fold a fold** — solo para un
   `GoalsModel` aislado. Sin esto, era imposible validar sin leakage
   ninguna mejora de calibracion/ranking contra lo que realmente se sirve
   en produccion.

### 11.2 Que se ha cambiado

- **`backtesting/ensemble_engine.py`** (nuevo): walk-forward real del
  ENSEMBLE completo (DC + 3 candidatos ML + seleccion de ganador por
  grupo + pesos aprendidos + calibradores isotonic/platt), con un holdout
  de seleccion/calibracion separado del fold de test en cada iteracion —
  nunca leakage. Comando CLI `ensemble-calibration-report`.
- **`Prediction.risk_adjusted_expected_value`**: EV calculado con
  `uncertainty_low` (extremo conservador del intervalo) en vez de
  `model_probability` — nunca mas optimista que `expected_value`.
  (`prediction/predictor.py`, `services/prediction_service.py`, API,
  frontend).
- **`prediction/anomaly.py::HIGH_UNCERTAINTY` ("ALTA_INCERTIDUMBRE")**:
  nueva flag que SI excluye de "mejor prediccion"/"señal fuerte"
  (`is_strong_signal`), igual que `CONTRADICCION` — antes solo habia
  flags informativas. Umbral (ancho de intervalo >= 0.60) derivado
  EMPIRICAMENTE de la distribucion real de anchos en un backtest
  sintetico (mediana ~0.32, p90 ~0.59) — un primer intento con 0.30
  resulto demasiado agresivo (regresion real: 12 tests rompieron, todos
  corregidos antes de continuar).
- **`prediction/ranking.py::signal_score_v2`/`rank_signals_v2`** (nuevo,
  NUNCA sustituye a v1 en produccion por si solo): usa
  `model_probability` (cruda, igual que v1 -- ver 11.5: la calibrada se
  descarto con evidencia real), `risk_adjusted_expected_value` en vez de
  `edge` bruto, y un factor CONTINUO de penalizacion por ancho de
  intervalo (ademas del corte binario de `HIGH_UNCERTAINTY`).
- **`backtesting/ranking_comparison.py`** (nuevo): compara v1 vs v2 sobre
  predicciones YA LIQUIDADAS, por TOP-N de cada estrategia (ROI, win
  rate, PnL, drawdown, Brier, log loss). Sin riesgo de leakage: ambos
  scores usan solo campos ya congelados antes del resultado real.
  Comando CLI `compare-ranking-strategies`.
- **Dashboard**: `AnomalyBadges` cableado en `MatchCard`/`top-signals`
  (antes solo en detalle de partido/historico); filtro nuevo "EV minimo"
  en `/predictions/top-signals` (`min_ev`, fraccion, filtro SQL sobre
  `Prediction.expected_value`).

### 11.3 Metricas usadas ahora (todas ya existian o se añaden aqui, ninguna inventada)

Brier score, log loss, Expected Calibration Error (`models/calibration/metrics.py`,
ya existian), ROI/yield/win-rate/max-drawdown/PnL
(`backtesting/metrics.py`, `ranking_comparison.py`, ya existian para
backtest sintetico, reusadas aqui sobre liquidaciones reales), e
intervalo de Wilson para incertidumbre (`prediction/uncertainty.py`, Fase
9). Reliability diagrams: `reliability_curve` ya existe en
`models/calibration/metrics.py` desde antes de esta fase.

### 11.4 Como funciona el ranking nuevo (signal_score_v2)

```
score_v2 = model_probability^2 * max(risk_adjusted_expected_value, 0) * confidence
           * data_quality * market_quality_multiplier * market_track_record
           * factor_incertidumbre(ancho_intervalo)
```

(Nunca `calibrated_probability`: el diseno inicial de esta formula la
preferia cuando existia, pero `ensemble-calibration-report` corrido
contra LaLiga -- ver 11.5 -- mostro que isotonic y Platt empeoran
Brier/log-loss/ECE en los 4 mercados centrales probados. Corregido con
esa evidencia, no a priori.)

`factor_incertidumbre` decae linealmente de 1.0 (ancho=0) a un suelo de
0.30 (ancho=1.0, el maximo teorico) — nunca colapsa a cero por si solo
(eso ya lo hace la exclusion binaria `HIGH_UNCERTAINTY` aparte). Un EV
ajustado al riesgo negativo anula el score entero (igual que v1 anula con
edge negativo). **v2 NO esta activo en produccion**: solo
`rank_signals`/`signal_score` (v1) generan y congelan lo que ve el
usuario hoy. v2 es la candidata que se backtestea contra v1.

### 11.5 Resultados comparativos del backtesting (datos REALES, corridos por el usuario)

Esta sesion no tiene acceso al historico real (vive solo en el Docker
Compose del usuario), pero el usuario corrio los comandos y pego la
salida real -- estos son resultados REALES, no sinteticos.

**`ensemble-calibration-report` (laliga, los 4 mercados centrales del
caso que motivo esta revision), walk-forward real, 7 folds, n=2719:**

| mercado           | raw ECE | isotonic ECE | platt ECE |
|--------------------|---------|--------------|-----------|
| home_win           | 0.0400  | 0.0577 (peor)| 0.0445 (peor) |
| draw               | 0.0243  | 0.0433 (peor)| 0.0279 (peor) |
| away_win           | 0.0403  | 0.0526 (peor)| 0.0321 (peor) |
| double_chance_1x   | 0.0592  | 0.0923 (peor)| 0.0724 (peor) |

Brier score y log loss muestran el mismo patron en los 4 mercados: raw
siempre mejor que isotonic y que Platt. **Conclusion real, no supuesta**:
ni isotonic ni Platt mejoran el ensemble real en NINGUNO de los 4
mercados centrales -- consistente, no ruido de un mercado aislado. Como
consecuencia directa de esta evidencia, `signal_score_v2` se corrigio
para NUNCA usar `calibrated_probability` (el diseno inicial de este
mismo documento, seccion 11.4, lo preferia cuando existia -- era
incorrecto, corregido aqui con los numeros reales en la mano, no a
priori).

**`compare-ranking-strategies` (todas las competiciones/mercados,
n=1133 predicciones liquidadas con cuota real):**

```
       top-5        top-10       top-20       top-50       top-100      all (n=1133)
v1   roi=+0.007   roi=-0.219   roi=-0.138   roi=+0.062   roi=+0.016   roi=-0.083
     win=60.0%    win=50.0%    win=55.0%    win=72.0%    win=66.0%    win=51.5%
v2   roi=+0.232   roi=+0.018   roi=-0.032   roi=-0.019   roi=+0.013   roi=-0.083
     win=80.0%    win=60.0%    win=65.0%    win=58.0%    win=54.0%    win=51.5%
```

(`all` es identico entre v1/v2 por construccion: el conjunto de
apuestas no cambia, solo el orden -- lo que SI varia es el max_drawdown,
ligeramente mejor en v2.)

**Lectura honesta**: a n=1133 (la cifra mas fiable que hay), apostar a
TODO lo que pasa el filtro duro pierde dinero (ROI -8.3%) en ambas
estrategias -- elegir bien importa, confirmado con datos reales. En los
tramos TOP-N (donde se juega la diferencia real entre v1 y v2), v1
muestra un patron NO monotono y a contramano: su top-10/20 (sus señales
"mas seguras") rinden PEOR que su propio top-50 -- exactamente el
sintoma que el usuario reporto (el ranking actual no correlaciona bien
con lo que realmente acierta). v2 top-5 rinde notablemente mejor
(ROI+23.2%, 80% de acierto) que v1 top-5. n=5/10/20 son muestras
demasiado pequenas para una conclusion estadisticamente solida por si
solas, pero la direccion es consistente con el diagnostico del usuario
Y el usuario confirmo via la app en vivo que el sintoma seguia
presente (porque v1 seguia siendo el default en ese momento) -- con
esa confirmacion real adicional, se decidio ACTIVAR v2 en produccion
(ver abajo), no seguir esperando mas muestra de forma indefinida.

**ACTIVADO EN PRODUCCION (2026-11)**: `signal_score_v2` sustituye a
`signal_score` (v1) en TODOS los puntos que generan lo que ve el
usuario: `services/prediction_service.py` (snapshot congelado de cada
prediccion nueva), `/predictions/top-signals`, `/predictions/best`,
`/predictions/best-of-day`, `/predictions/summary`,
`/predictions/best/debug`, y `prediction/anomaly.py::best_prediction_per_match`
(la "mejor prediccion" de cada partido). `signal_score`/`rank_signals`
(v1) NUNCA se borraron -- siguen existiendo como funciones, y
`backtesting/ranking_comparison.py` las RECALCULA explicitamente
(nunca lee `prediction.signal_score`, que desde ahora congela v2) para
poder seguir comparando v1 contra v2 -- o v2 contra una v3 futura --
indefinidamente, independientemente de cual sea el default activo.

### 11.6 Limitaciones que siguen existiendo

- Shin's method sigue sin implementar (sin fuente verificable, ver
  seccion 10 punto 7) — solo normalizacion proporcional.
- La decision de activar v2 se tomo con evidencia real direccionalmente
  fuerte pero estadisticamente delgada en el tramo top-N (n=5-20) —
  nunca se fingio que fuera una prueba estadistica solida por si sola.
  Se recomienda seguir corriendo `compare-ranking-strategies`
  periodicamente a medida que se liquiden mas predicciones, para
  confirmar que v2 sigue rindiendo bien con mas muestra.
- El umbral de `HIGH_UNCERTAINTY` (0.60) y la pendiente/suelo de
  `factor_incertidumbre` en v2 (0.70/0.30) se derivaron de un dataset
  SINTETICO (Poisson simple) por falta de acceso a datos reales desde
  este entorno — deberian remedirse contra predicciones reales ya
  liquidadas una vez haya suficiente historico, no asumirse validos para
  siempre.
- `ensemble_calibration_report`/`compare_ranking_strategies` son
  herramientas de diagnostico nuevas, verificadas solo con datos
  sinteticos en esta sesion (10 + 6 tests respectivamente) — su
  comportamiento con el volumen y la distribucion reales de las 5 ligas
  del MVP todavia no se ha observado.
- El "indicador de fiabilidad" pedido explicitamente ya existe repartido
  en varios campos (`signal_tier`, `market_quality`, `anomaly_flags`,
  `confidence`, `data_quality`) en vez de un unico numero compuesto
  nuevo — evita inventar una metrica adicional no validada, pero exige
  mirar mas de un campo para "fiabilidad" en vez de uno solo.

## 12. Fase 11 (sesion 2026-11, continuacion): factor de fiabilidad del edge

### 12.1 Problema real encontrado tras activar v2

El usuario reporto, tras regenerar predicciones con `signal_score_v2` ya
activo en produccion: "sigo viendo lo mismo eh". Se diagnostico en vivo
contra la BD real: el caso original que motivo toda la revision
(match_id=61, Getafe-Atletico, mercado `double_chance_1x`, edge +39.6pp)
seguia siendo la #1 del ranking.

Causa raiz real (no supuesta): `uncertainty_low`/`uncertainty_high` de
ese partido eran **0.718/0.911** — un intervalo de Wilson ESTRECHO
(ancho 0.193, muy por debajo del umbral de `HIGH_UNCERTAINTY`, 0.60),
porque el modelo tiene mucha muestra historica para ese tipo de
prediccion. `factor_incertidumbre` (seccion 11.4) mide ruido de
MUESTRA — "cuanto puede fiarse el modelo de su propia estimacion dado
el tamanho de la muestra" — nunca "cuanto discrepa esa estimacion de una
fuente independiente fuerte (el mercado)". Son dos tipos de
incertidumbre DISTINTOS, y el sistema solo tenia el primero. Por eso el
EV ajustado al riesgo de ese partido seguia siendo alto
(`risk_adjusted_expected_value=0.700`) y el caso seguia dominando el
ranking: v2 corrigio el sintoma sintetico que se habia probado, pero no
el mecanismo real que lo producia.

### 12.2 Evidencia real antes de corregir (nunca un umbral inventado)

Antes de anhadir cualquier penalizacion por tamanho de edge, se pidio
evidencia real de que un edge mas grande REALMENTE acierta peor en este
sistema. Se construyo `scripts/debug_edge_vs_hitrate.py` (reutiliza
`backtesting/metrics.py::performance_by_edge_bucket`, ya existente,
ninguna metrica nueva) y el usuario lo corrio contra las 1133
predicciones liquidadas reales:

| edge (pp) | n   | hit rate | ROI    |
|-----------|-----|----------|--------|
| 0-5       | —   | 53.4%    | +3.2%  |
| 5-10      | —   | 41.4%    | negativo |
| 10-15     | —   | 31.8%    | -46.4% |

La caida es practicamente monotona, y se mantiene igual o se acentua
restringiendo a mercado bien respaldado (>=4 casas). Conclusion real:
en este sistema, un edge mas grande es señal de que el modelo
probablemente se equivoco (sesgo del ensemble frente a un mercado bien
informado), no de mejor oportunidad — exactamente la intuicion original
del usuario ("a veces la mejor [señal] es la que mas probabilidad dan
tanto el modelo y las casas y que se pague mejor").

### 12.3 Primer intento (INSUFICIENTE, descartado tras verificarlo en vivo)

Con esa evidencia (y solo con ella) se anhadio un primer mecanismo:

- `services/evaluation_service.py::fit_edge_reliability_curve(db)`: ajusta
  una `IsotonicRegression` (misma herramienta que
  `models/calibration/calibrators.py`, aqui con `increasing=False`
  porque la relacion real observada es DECRECIENTE) sobre
  `(edge_en_puntos_porcentuales -> resultado real)`, agregada sobre
  TODAS las competiciones/mercados a la vez (asi se midio la evidencia).
- Un factor `edge_reliability_factor(edge, curve)` que normalizaba el
  hit-rate empirico de ese tamanho de edge contra el de un edge minimo
  (~0pp) y se MULTIPLICABA al final de `signal_score_v2`.

**Esto se desplego y se verifico EN VIVO contra la BD real del
usuario (no se dio por valido sin comprobarlo)**: el factor SI se
aplicaba (redujo el score de Getafe de 0.2015 a 0.0561, un recorte real
del 72%), pero el caso **seguia siendo el #1** del ranking
(`docker compose exec backend python -c "..."` contra `rank_signals_v2`
sobre la jornada actual real, ver el historial de este chat). Causa:
un descuento multiplicativo APLICADO DESPUES no corrige la causa de
fondo -- `risk_adjusted_expected_value` (+0.70) seguia calculado con
`uncertainty_low=0.718`, un numero que la propia evidencia (hit rate
real ~14% a un edge de este tamanho) dice que es irrealmente optimista.
Descontar el score final de un EV ya inflado no basta: el EV mismo
tenia que corregirse. Esta seccion se deja escrita (en vez de borrarse)
porque documenta honestamente un intento real que no funcionaba
numericamente, no solo en teoria.

### 12.4 La correccion real: `reality_capped_expected_value`

En vez de un factor aparte, la misma curva se usa para poner un TECHO
al EV mismo, antes de que entre en la formula:

- **`prediction/ranking.py::reality_capped_expected_value(risk_adjusted_ev,
  edge, market_odds, curve)`** (funcion PURA, sustituye a
  `edge_reliability_factor`): calcula `real_hit_rate = curva.predict(edge*100)`
  y `real_ev = real_hit_rate * market_odds - 1`, y devuelve
  `min(risk_adjusted_ev, real_ev)`. Nunca un blend arbitrario (la
  seccion 9 del brief original lo pide explicitamente): es un TECHO,
  mismo principio que ya usa `uncertainty_low` como cota conservadora
  sobre `model_probability` -- aqui se anhade una segunda cota
  conservadora, independiente, sobre el sesgo SISTEMATICO del ensemble
  para ESTE TAMANO de edge (en vez de sobre el ruido de muestra de
  este partido en concreto), y se toma el minimo de las dos. Neutro
  (`risk_adjusted_ev` sin tocar) sin curva, `edge` o `market_odds`.
- `signal_score_v2` pasa el EV por este techo antes de recortarlo a 0:

```
ev_techo   = reality_capped_expected_value(risk_adjusted_expected_value, edge, market_odds, curva)
score_v2 = model_probability^2 * max(ev_techo, 0) * confidence
           * data_quality * market_quality_multiplier * market_track_record
           * factor_incertidumbre(ancho_intervalo)
```

- `fit_edge_reliability_curve(db)` (sin cambios) sigue calculando la
  curva; el parametro `edge_reliability_curve` de
  `signal_score_v2`/`rank_signals_v2`/`best_prediction_per_match` (sin
  cambios de firma ni de cableado en produccion -- solo cambio lo que
  pasa POR DENTRO con ese parametro) se sigue pasando igual en todos
  los puntos ya cableados en la seccion 12 original:
  `services/prediction_service.py`, los 5 endpoints de
  `/predictions/*`, `/matches/*`, `/history/*`, `agent/tools.py`,
  `agent/alerts.py`.

**Verificado con los numeros reales del caso Getafe**: edge=39.6pp,
cuota=2.37, `uncertainty_low`=0.718 (EV optimista = +0.70). La curva
real predice un hit rate de ~15% para ese tamanho de edge (clip al
extremo mas alto observado en la muestra real). `real_ev = 0.15*2.37-1
≈ -0.64`. `min(+0.70, -0.64) = -0.64` -> `max(-0.64, 0) = 0` -> el
score se anula por completo (en vez de solo reducirse un 72%). Un edge
modesto y bien respaldado por la curva (hit rate real cercano al
break-even de su propia cuota) no se ve afectado -- el techo solo actua
sobre el optimismo EXCESIVO de edges grandes, nunca sobre cualquier
edge por igual.

### 12.5 Tests anhadidos

- `test_ranking.py`: tests para `reality_capped_expected_value` (neutro
  sin curva/edge/cuota, recorta un EV optimista al valor real implicado
  por la curva para un edge tipo Getafe, nunca SUBE un EV ya peor que
  el techo, apenas toca un edge modesto bien respaldado);
  `signal_score_v2` con/sin curva (reproduccion explicita del caso
  Getafe real: antes de conectar la curva, el EV optimista seguia dando
  score positivo; con la curva, el score se anula por completo);
  `rank_signals_v2` y `best_prediction_per_match` pasando la curva de
  extremo a extremo.
- `test_evaluation_service.py`: 2 tests para `fit_edge_reliability_curve`
  (`None` por debajo del minimo; la curva ajustada predice menor hit
  rate para edge grande que para edge pequenho). Corridos contra un
  motor SQLite EN MEMORIA aislado (no el fichero compartido de toda la
  sesion de pytest, via `session_scope()`) — `fit_edge_reliability_curve`
  pool TODAS las liquidaciones sin filtrar por competicion a proposito,
  y otros tests de ese mismo fichero ya generan predicciones reales con
  cuota/edge via el pipeline completo; aislar el motor evita que ese
  volumen cambie el resultado de "por debajo del minimo" segun el orden
  de ejecucion.
- Suite completa: 401 tests, todos en verde. `ruff check backend`: cero
  errores nuevos frente al baseline antes de este cambio (comparado
  explicitamente con `git stash`).

### 12.6 Como verificarlo tras desplegar

`git pull` + `docker compose up -d --build` (el fix vive solo en
codigo Python, no requiere migracion de esquema ni regenerar
predicciones: `edge_reliability_curve` se calcula EN VIVO en cada
request a partir de las liquidaciones ya existentes, nunca se persiste
por prediccion). El caso match_id=61 (Getafe-Atletico,
`double_chance_1x`) deberia desaparecer del todo de "Oportunidades"
(score=0, no solo bajar de posicion) en cuanto haya al menos 30
predicciones liquidadas con cuota real en la BD -- que ya las hay,
confirmado en vivo durante esta misma sesion (`fit_edge_reliability_curve`
devolvio una curva ajustada real contra la BD de produccion).

### 12.7 Limitaciones que siguen existiendo

- La curva se ajusta GLOBALMENTE (todas las competiciones/mercados a la
  vez) porque asi se midio la evidencia real — no se ha probado si una
  curva por mercado seria mejor o peor con mas muestra; medirlo exige
  mas liquidaciones de las que hay hoy.
- Igual que `market_track_records`, la curva se recalcula en cada
  request desde la BD (coste de una query + un fit de `IsotonicRegression`,
  ambos baratos en la escala actual de datos) en vez de cachearse — si
  el volumen de liquidaciones crece mucho, valdria la pena revisar el
  coste.
- `real_hit_rate` es un promedio agregado de TODOS los mercados a ese
  tamanho de edge -- usarlo para poner techo al EV de un mercado
  concreto asume que el sesgo del ensemble a ese tamanho de edge es
  parecido entre mercados; no se ha medido si algun mercado especifico
  se desvia mucho de esa media.
- No se ha medido todavia el impacto de esta correccion en
  `compare-ranking-strategies` (ROI/win-rate real de v2 CON el techo de
  EV frente a v2 sin el) por falta de acceso continuo a la BD real
  desde este entorno — se recomienda correr ese comando de nuevo
  periodicamente para confirmar que la correccion tambien mejora el
  backtest agregado, no solo el caso puntual que la motivo. La leccion
  de la seccion 12.3 (un primer intento parecia razonable en tests
  sinteticos pero no bastaba en produccion real) es la razon de fondo
  para seguir verificando esto con datos reales, no darlo por cerrado.

## 13. Fase 12 (sesion 2026-11, continuacion): favorite-longshot bias

### 13.1 Problema real encontrado tras el fix de edge

Con el techo de EV por edge ya desplegado y verificado (seccion 12), el
usuario reporto un patron RELACIONADO pero distinto: "victoria local" a
cuota 5.50/6.00 (con `model_probability` de 33.5%/28.3%) seguia
destacada como "mejor oportunidad" en el dashboard. Cita textual: "una
cuota 5 o una cuota 6 no es una buena prediccion".

Es una hipotesis DISTINTA a la de edge grande: esta vez la pregunta es
si la CUOTA/PROBABILIDAD en si (independiente del tamanho del edge)
predice peor acierto -- el conocido "favorite-longshot bias" de la
literatura de apuestas deportivas (los mercados, y potencialmente un
modelo entrenado contra ellos, tienden a sobrevalorar las selecciones
poco probables).

### 13.2 Evidencia real antes de corregir

Se anhadio `backtesting/metrics.py::performance_by_odds_bucket` (mismo
patron que `performance_by_edge_bucket`, nunca una metrica nueva de
cero) y `scripts/debug_odds_vs_hitrate.py`, corrido por el usuario
contra las mismas 1133 predicciones liquidadas reales:

| cuota | n   | model_prob media | hit rate real | ROI    |
|-------|-----|-------------------|----------------|--------|
| 1-2   | 642 | 66.0%             | 68.2%          | -2.5%  |
| 2-3   | 243 | 41.0%             | 38.7%          | -10.3% |
| 3-4   | 141 | 28.4%             | 28.4%          | -2.8%  |
| 4-6   | 79  | 21.7%             | 13.9%          | -34.3% |
| 6+    | 28  | 15.8%             | 3.6%           | -77.9% |

Conclusion real, clara e inequivoca: en las cuotas 1-4 el modelo esta
razonablemente bien calibrado (`model_prob` y `hit rate` casi
coinciden); a partir de cuota 4 el modelo SOBRESTIMA su propia
probabilidad de forma creciente y dramatica (21.7% dicho vs 13.9% real;
15.8% dicho vs 3.6% real) -- confirma el "favorite-longshot bias" en
este sistema concreto, con datos reales, nunca asumido de antemano. Es
un problema DISTINTO del de edge grande: ocurre incluso con edge
modesto.

### 13.3 Primer intento (INSUFICIENTE, descartado tras verificarlo en vivo)

Mismo principio ya validado en la seccion 12 (nunca un blend, un TECHO
al EV basado en evidencia independiente), aplicado a una segunda fuente
de sesgo -- primera version, luego corregida (ver 13.3bis):

- `services/evaluation_service.py::fit_odds_reliability_curve(db)`:
  ajustaba una `IsotonicRegression` (`increasing=True`) sobre
  **`model_probability -> resultado real`**.
- `prediction/ranking.py::reality_capped_expected_value` consultaba esa
  curva con `model_probability` como clave.

**Esto se desplego y se verifico EN VIVO contra la BD real del
usuario** (no se dio por valido sin comprobarlo): el caso real que
motivo esta seccion (match_id=61, "victoria local", `model_probability`
=0.335, cuota 5.50) seguia saliendo #1 con `score=0.0273`, apenas
afectado. Causa raiz real: el primer intento agrupaba/consultaba por
`model_probability`, pero la evidencia de la tabla de 13.2 esta medida
por **CUOTA**, no por probabilidad del modelo. Un `model_probability`
de 33.5% aparece tanto en apuestas bien calibradas (cuota ~3, donde ese
nivel de probabilidad es normal y correcto) como en apuestas mal
calibradas (cuota ~5.5, la cola real del sesgo) -- agrupar por
probabilidad MEZCLA ambos casos entre si y diluye la correccion justo
donde mas hacia falta, exactamente igual que el primer intento de la
seccion 12.3 diluia el problema con un factor multiplicativo en vez de
un techo al EV. Esta seccion se deja escrita (en vez de borrarse) por
la misma razon que la 12.3: documenta honestamente un intento real que
no funcionaba numericamente.

### 13.3bis La correccion real: la curva se ajusta y se consulta por CUOTA

- **`services/evaluation_service.py::fit_odds_reliability_curve(db)`**
  (corregida): ajusta la `IsotonicRegression` (`increasing=False`,
  porque la relacion real observada es DECRECIENTE: mas cuota, menos
  hit rate) sobre **`market_odds -> resultado real`** -- la MISMA
  variable que agrupa `scripts/debug_odds_vs_hitrate.py`, agregada
  sobre TODAS las competiciones/mercados a la vez (mismo motivo que
  `fit_edge_reliability_curve`). `None` con menos de
  `MIN_SETTLED_FOR_ODDS_RELIABILITY` (30, mismo criterio que los otros
  `MIN_SETTLED_FOR_*`) liquidaciones con cuota real.

  **OJO, para no contradecir la seccion 11.5**: esto NO reemplaza
  `model_probability` para mostrar ni para rankear (ese camino ya se
  probo con `ensemble_calibration_report`, walk-forward y sin fuga, y
  empeoraba Brier/ECE en los 4 mercados centrales de LaLiga) -- se usa
  UNICAMENTE como otra cota conservadora sobre el EV, nunca para
  sustituir la probabilidad que ve el usuario.

- **`prediction/ranking.py::reality_capped_expected_value`** (extendida,
  no duplicada): ahora acepta tambien `odds_reliability_curve`, y la
  consulta con `market_odds` (nunca con `model_probability`), tomando
  el **minimo** entre TODAS las cotas disponibles (la de edge Y la de
  cuota) -- cualquier evidencia real que diga "este EV es mas optimista
  de lo que deberia" puede bajarlo, ninguna puede subirlo. Nunca un
  blend.

```
ev_techo = min(
    risk_adjusted_expected_value,
    hit_rate_real_por_edge(edge) * market_odds - 1,         # seccion 12
    hit_rate_real_por_cuota(market_odds) * market_odds - 1, # seccion 13
)
score_v2 = model_probability^2 * max(ev_techo, 0) * confidence
           * data_quality * market_quality_multiplier * market_track_record
           * factor_incertidumbre(ancho_intervalo)
```

- `signal_score_v2`/`rank_signals_v2`/`best_prediction_per_match` ganan
  el parametro opcional `odds_reliability_curve` (mismo patron
  que `edge_reliability_curve`: `None` es neutro). Cableado en TODOS los
  mismos puntos de produccion ya cableados en la seccion 12:
  `services/prediction_service.py`, los 5 endpoints de `/predictions/*`,
  `/matches/*`, `/history/*`, `agent/tools.py`, `agent/alerts.py` --
  cada uno calcula AMBAS curvas una vez por request/generacion (mismo
  patron que `market_track_records`).

**Verificado con los numeros reales del caso Getafe tras la correccion**:
cuota=5.50. La curva real predice un hit rate de ~12-14% para esa
cuota (interpolando entre los buckets reales 4-6 y 6+). `real_ev =
0.13*5.50-1 ≈ -0.33`, frente al `+0.70`/`+0.30` optimista que daba
`uncertainty_low`/el EV ajustado al riesgo. Con la curva por
probabilidad (el primer intento), esa misma consulta devolvia un hit
rate mucho mas alto (contaminado por apuestas de menor cuota con la
misma probabilidad de 33.5%), lo que explica por que el score apenas
bajaba.

### 13.4 Tests anhadidos

- `backtesting/test_metrics.py`: 3 tests para `performance_by_odds_bucket`
  (agrupa por rango de cuota en un dataset sintetico verificable,
  vacio sin cuotas validas, reporta Brier/ROI/hit_rate por bucket).
- `test_ranking.py`: tests para `reality_capped_expected_value` con la
  curva de cuota (neutro sin ella, anula un EV optimista para un caso
  "longshot" sintetico por CUOTA, apenas toca una cuota bien calibrada,
  toma el MINIMO cuando ambas curvas -- edge y cuota -- estan
  conectadas a la vez); `signal_score_v2`/`rank_signals_v2`/
  `best_prediction_per_match` con la curva de cuota conectada de
  extremo a extremo.
- `test_evaluation_service.py`: 2 tests para
  `fit_odds_reliability_curve` (`None` por debajo del minimo; la
  curva ajustada predice menor hit rate para cuota alta que para cuota
  baja), corridos contra el mismo motor SQLite EN MEMORIA aislado que
  ya protegia los tests de `fit_edge_reliability_curve` (mismo riesgo
  de contaminacion cruzada: tambien pool TODO sin filtrar por
  competicion a proposito).
- `scripts/debug_odds_vs_hitrate.py` + su test de extremo a extremo
  (mismo patron que `debug_edge_vs_hitrate.py`, incluida la leccion del
  `DetachedInstanceError` real: valores extraidos DENTRO del `with
  session_scope()`).
- Suite completa: 417 tests, todos en verde. `ruff check backend scripts`:
  cero errores nuevos frente al baseline antes de este cambio (comparado
  explicitamente con `git stash`).

### 13.5 Como verificarlo tras desplegar

`git pull` + `docker compose up -d --build` (sin migracion de esquema
ni regenerar predicciones -- ambas curvas se calculan EN VIVO en cada
request). Una "victoria local"/senhal a cuota alta (tipo cuota 5+)
deberia dejar de aparecer como oportunidad destacada (o salir con score
mucho mas bajo que antes) en cuanto haya al menos 30 liquidaciones con
cuota real en la BD -- que ya las hay, confirmado en vivo durante esta
misma sesion con `debug_odds_vs_hitrate.py` (n=1133) y con la
verificacion directa de `rank_signals_v2` contra la jornada real tras
desplegar la correccion de 13.3bis.

### 13.6 Limitaciones que siguen existiendo

- Igual que la curva de edge, esta se ajusta GLOBALMENTE (todas las
  competiciones/mercados a la vez) -- no se ha probado si una curva por
  mercado seria mejor o peor con mas muestra.
- Las dos curvas (edge y probabilidad) son dimensiones RELACIONADAS
  (edge grande normalmente implica probabilidad del modelo alejada de
  la del mercado) pero no identicas -- no se ha medido su correlacion
  real ni si aplicar ambos techos a la vez sobre-corrige en algun
  segmento; el minimo de ambas es deliberadamente conservador por
  diseño, pero "conservador" puede volverse "casi todo anulado" si
  ambas curvas son simultaneamente severas sobre el mismo conjunto de
  señales -- esto debe vigilarse con `compare-ranking-strategies` tras
  desplegar, no darse por bueno de antemano.
- No se ha medido el impacto agregado de las DOS correcciones juntas
  (seccion 12 + seccion 13) en `compare-ranking-strategies`
  (ROI/win-rate real), por la misma limitacion de acceso continuo a la
  BD real desde este entorno. Mismo patron que la seccion 12.6: hace
  falta seguir verificando esto con datos reales periodicamente, no
  darlo por cerrado.
- Si tras desplegar este fix el usuario reporta OTRO patron relacionado
  (p.ej. un mercado concreto, o una combinacion especifica de
  bookmakers_used/cuota), la misma disciplina aplica: pedir evidencia
  real con un diagnostico reutilizando `performance_by_*_bucket` antes
  de anhadir una tercera cota, nunca inventar un umbral mas.

## 14. Fase 13 (sesion 2026-11, continuacion): auditoria completa y rollback del techo de cuota

### 14.1 El usuario pidio una auditoria completa antes de seguir parcheando

Tras dos correcciones de la seccion 13 desplegadas y corregidas en vivo
(13.3 -> 13.3bis), el usuario pidio explicitamente PARAR de parchear
reactivamente y auditar el flujo completo con datos reales antes de
cambiar mas codigo (ranking.py, prediction_service.py, edge.py,
uncertainty.py, anomaly.py, evaluation_service.py, backtesting/,
scripts de diagnostico). Se construyo
`scripts/debug_full_ranking_audit.py` (reutiliza
`performance_by_edge_bucket`, `performance_by_odds_bucket`,
`market_quality_tier`, `fit_edge_reliability_curve`,
`fit_odds_reliability_curve`, `rank_signals_v2` -- ninguna metrica
nueva) para reunir en un solo informe: hit rate/ROI por edge, por
cuota, por mercado, por competicion, por rango de probabilidad, por
numero de bookmakers, por calidad de mercado; el desglose paso a paso
de un `match_id` concreto; y la comparacion score ALMACENADO vs score
EN VIVO del top-10 de la jornada actual.

### 14.2 Hallazgo critico: el techo de cuota (seccion 13) anulaba PRACTICAMENTE TODO

El usuario corrio el script contra la BD de produccion real (n=1133).
El top-10 de la jornada actual, SIN excepcion, salio con `score=0.0000`
tanto almacenado como en vivo. No era un problema de orden: no habia
ranking real, todo empataba en cero.

Causa raiz (confirmada con la propia tabla de hit rate/ROI por cuota
que el script genero):

| cuota   | hit rate real | ROI    |
|---------|----------------|--------|
| 1.0-2.0 | 68.2%          | -2.5%  |
| 2.0-3.0 | 38.7%          | -10.3% |
| 3.0-4.0 | 28.4%          | -2.8%  |
| 4.0-6.0 | 13.9%          | -34.3% |
| 6.0+    | 3.6%           | -77.9% |

**Las 5 cuotas, sin excepcion, tienen ROI real negativo** (el vig del
mercado: ya documentado en la seccion 11.5, "apostar a TODO pierde
dinero, ROI -8.3%"). `reality_capped_expected_value` (secciones 12/13)
usa el hit rate PROMEDIO de ese nivel de cuota/edge como TECHO del EV
de cada prediccion individual. Pero si el promedio de cada nivel de
cuota es negativo, ese techo es negativo en practicamente todos los
casos, independientemente de lo bueno que sea el modelo para esa
apuesta en concreto -- el mecanismo asume implicitamente que el modelo
tiene CERO habilidad por encima del mercado (el techo = lo que gana el
apostante medio a esa cuota), que es precisamente lo contrario de lo
que un detector de edge intenta medir. El techo de cuota (seccion 13)
es el mas destructivo de los dos: NINGUN bucket de cuota observado
tiene ROI positivo, asi que captura casi cualquier señal
indiscriminadamente; el techo de edge (seccion 12) es menos severo
porque el bucket 0-5pp SI tiene ROI real positivo (+3.2%, n=253, la
mayoria de la muestra), pero aun asi el techo de cuota, aplicado
despues via el `min()`, se come lo que sobreviva al primero.

Esta es exactamente la situacion que la seccion 13.6 advertia como
riesgo ("conservador puede volverse casi todo anulado") -- aqui se
confirma con datos reales que SE MATERIALIZO.

### 14.3 Accion tomada: rollback del techo de cuota (parche de emergencia, aprobado explicitamente)

Se desconecta el techo de cuota (`odds_reliability_curve`) de TODOS los
puntos de produccion (mismos 6 ficheros/16 sitios cableados en la
seccion 13): `services/prediction_service.py`, los 5 endpoints de
`/predictions/*`, `/matches/*`, `/history/*`, `agent/tools.py`,
`agent/alerts.py`. **Nunca se borra el mecanismo**: `fit_odds_reliability_curve`,
`reality_capped_expected_value` (con su parametro
`odds_reliability_curve`), `signal_score_v2`/`rank_signals_v2`/
`best_prediction_per_match` (con su parametro del mismo nombre) siguen
existiendo, totalmente testeados -- solo se deja de LLAMAR a
`fit_odds_reliability_curve(db)` y de pasar su resultado en produccion,
igual principio que mantener `signal_score`/`rank_signals` (v1) vivos
sin usarlos tras activar v2. El techo de edge (seccion 12) NO se toca:
su bucket mayoritario (0-5pp) tiene ROI real positivo, asi que no
produce el mismo colapso total.

### 14.4 Lo que queda pendiente antes de reactivar CUALQUIER techo

Hallazgo adicional de la auditoria: `backtesting/ranking_comparison.py::_score_v2`
llama a `signal_score_v2(prediction)` SIN pasar ninguna curva -- la
herramienta que deberia validar si estos techos mejoran el ROI/win-rate
real (`compare-ranking-strategies`) es CIEGA a ambos desde que existen.
Antes de reactivar el techo de cuota (o de ajustar el de edge), hace
falta:

1. Corregir `ranking_comparison.py` para que pueda evaluar variantes
   CON cada curva, con disciplina walk-forward real (la curva de cada
   variante debe ajustarse SOLO con liquidaciones anteriores al
   periodo evaluado, nunca con el conjunto completo -- la version
   actual de `fit_edge_reliability_curve`/`fit_odds_reliability_curve`
   usa TODO el historico, lo cual vale para el ranking EN VIVO sobre
   partidos futuros pero NO para validar out-of-sample).
2. Comparar v1, v2 sin techos, v2+techo-edge, v2+techo-cuota, v2+ambos
   sobre el MISMO conjunto de liquidaciones reales.
3. Activar en produccion solo la variante que demuestre mejorar ROI/
   win-rate/Brier out-of-sample con muestra suficiente y drawdown
   estable -- nunca por intuicion, exactamente el criterio que ya pide
   el usuario en esta seccion.

Hasta que eso exista, el estado de produccion es: v2 + techo de edge
unicamente (seccion 12), techo de cuota construido y testeado pero
INACTIVO.

## 15. Fase 14 (sesion 2026-11, continuacion): calibracion de la cola + comparador walk-forward

### 15.1 Pedido del usuario

Tras el rollback de emergencia del techo de cuota (seccion 14), el
usuario confirmo el siguiente paso con una frase que resume el
principio de fondo: "quiero que el modelo sea realista tambien con las
predicciones, no solo porque haya mucha diferencia de edge entre el
modelo y la cuota de mercado". Es decir: el problema de fondo no es (solo)
"premiar diferencias grandes modelo-mercado" -- es que, en la cola de
cuota alta, la PROBABILIDAD del modelo en si no es realista.

### 15.2 Por que el techo de cuota (seccion 13) fallaba de raiz

El techo de cuota usaba el ROI PROMEDIO historico de un bucket de cuota
entero como limite del EV de cada prediccion individual. Como el ROI
promedio es negativo en TODAS las cuotas observadas (vig del mercado --
seccion 14.2), ese techo castigaba indiscriminadamente casi cualquier
señal, sin distinguir una buena de una mala: asumia implicitamente que
el modelo nunca puede superar al apostante medio, justo lo contrario de
lo que un detector de edge intenta demostrar.

### 15.3 La correccion: calibrar la PROBABILIDAD, no limitar el EV por ROI agregado

- **`services/evaluation_service.py::fit_longshot_calibration_curve(db,
  odds_threshold=4.0)`** (nuevo): ajusta una `IsotonicRegression`
  (`increasing=True`, calibracion real: mas probabilidad dicha, mas
  acierto real) sobre `model_probability -> resultado`, pero SOLO con
  liquidaciones de la COLA (`market_odds >= LONGSHOT_ODDS_THRESHOLD`,
  4.0 -- el punto donde la seccion 13.2 encontro sobreconfianza real:
  cuota 4-6 dice 21.7% y acierta 13.9%; cuota 6+ dice 15.8% y acierta
  3.6%; por debajo de cuota 4 el modelo esta razonablemente calibrado).
  `None` con menos de `MIN_SETTLED_FOR_LONGSHOT_CALIBRATION` (30)
  liquidaciones de cola.

  **Por que esto NO repite el error de la seccion 13**: una curva de
  calibracion de probabilidad esta acotada en [0,1] y nunca mezcla el
  vig del mercado (el `market_odds` solo entra DESPUES, al multiplicar
  `probabilidad_realista * market_odds - 1`, exactamente igual que con
  `model_probability` cruda) -- no hereda el sesgo "el ROI medio es
  negativo en todas partes" que colapsaba el ranking entero.

  **Por que se restringe a la cola y no al rango completo**: la seccion
  11.5 ya midio con `ensemble_calibration_report` (walk-forward, sin
  fuga) que calibrar la probabilidad del ensemble en el rango COMPLETO
  empeora Brier/ECE en los 4 mercados centrales de LaLiga. Esta curva es
  deliberadamente mas estrecha (solo cuota >=4), ajustada unicamente
  donde hay evidencia real de sobreconfianza, para no repetir ese
  resultado negativo.

- **`prediction/ranking.py::reality_capped_expected_value`** (extendida
  de nuevo): gana `model_probability`/`longshot_calibration_curve`. Para
  `market_odds >= LONGSHOT_ODDS_THRESHOLD`, calcula
  `probabilidad_realista = longshot_calibration_curve.predict(model_probability)`
  y toma el minimo entre el EV ya calculado y
  `probabilidad_realista * market_odds - 1` -- mismo principio de TECHO
  (nunca sube, solo puede bajar) que las dos cotas anteriores, pero
  sobre una cantidad que nunca hereda el vig agregado.

- **Refactor sin cambio de comportamiento**: la logica de ajuste de las
  tres curvas (`fit_edge_reliability_curve`, `fit_odds_reliability_curve`,
  `fit_longshot_calibration_curve`) se extrajo a funciones PURAS
  (`prediction/ranking.py::fit_*_from_observations`, sin BD) para que
  `backtesting/ranking_comparison.py` pudiera reutilizar EXACTAMENTE el
  mismo calculo dentro de un walk-forward, sin duplicar logica ni
  arriesgarse a que las dos versiones diverjan con el tiempo.

- **NUNCA activada en produccion todavia**: `longshot_calibration_curve`
  no se pasa en ningun endpoint -- solo existe en
  `backtesting/ranking_comparison.py` (seccion 15.4), pendiente de la
  validacion walk-forward real que decidira si se activa.

### 15.4 El comparador que faltaba: `compare_ranking_strategies_walk_forward`

Hallazgo de la seccion 14.4: `compare_ranking_strategies` (v1 vs v2) es
valido SIN walk-forward porque ninguna de las dos formulas tiene ningun
parametro ajustado -- pero las estrategias con curva (`v2_edge_cap`,
produccion actual; `v2_edge_cap_longshot`, la candidata de esta seccion)
SI lo tienen, y ajustarlas sobre el conjunto COMPLETO de liquidaciones
seria leakage de validacion (la curva "veria" el resultado que esta
puntuando).

`backtesting/ranking_comparison.py::compare_ranking_strategies_walk_forward`
(nuevo):

1. Ordena TODAS las liquidaciones con cuota cronologicamente por
   `Match.kickoff_utc` (nunca por id/orden de insercion).
2. Las primeras `min_train_n` (30 por defecto, mismo criterio que los
   `MIN_SETTLED_FOR_*`) nunca se evaluan -- no hay "antes" suficiente
   todavia.
3. Cada liquidacion evaluada se puntua con una curva ajustada SOLO con
   liquidaciones estrictamente anteriores a ella (nunca la propia, nunca
   el futuro) -- las curvas se reajustan cada `WALK_FORWARD_REFIT_EVERY`
   (25) liquidaciones nuevas, no en cada fila, por eficiencia (imita
   ademas como funciona produccion de verdad: la curva se reajusta por
   REQUEST, no por prediccion individual).
4. Compara 4 estrategias (`WALK_FORWARD_STRATEGIES`): `v1`, `v2` (sin
   ningun techo), `v2_edge_cap` (produccion actual, seccion 12),
   `v2_edge_cap_longshot` (candidata de esta seccion, con ambos techos
   conectados).

Verificado con dos tests de "espia" (nunca solo inferido de resultados
agregados): se intercepta la funcion de ajuste real y se comprueba que
el PRIMER ajuste de cada curva recibe EXACTAMENTE las observaciones
anteriores al punto donde empieza la evaluacion (ni una mas, lo que
incluiria la propia prediccion evaluada; ni una menos, lo que dejaria
fuera historico valido), y que los reajustes posteriores ocurren
exactamente cada `WALK_FORWARD_REFIT_EVERY` liquidaciones.

Comando CLI nuevo: `compare-ranking-strategies-walk-forward` (mismas
opciones que `compare-ranking-strategies`: `--competition`, `--market`,
mas `--min-train-n`).

### 15.5 Tests anhadidos

- `test_ranking.py`: `fit_longshot_calibration_curve_from_observations`
  (creciente, pura); `reality_capped_expected_value` con la curva de
  cola (neutro sin ella, neutro por debajo de `LONGSHOT_ODDS_THRESHOLD`,
  capa un longshot sobreconfiado, nunca sube un EV ya peor, combina con
  el techo de edge tomando el minimo); `signal_score_v2`/`rank_signals_v2`
  con la curva de cola conectada de extremo a extremo (reproduce el caso
  real: match_id=61, home_win, 33.5%, cuota 5.5).
- `test_evaluation_service.py`: `fit_longshot_calibration_curve` (`None`
  por debajo del minimo; ignora liquidaciones por debajo de
  `LONGSHOT_ODDS_THRESHOLD` aunque haya muchisima muestra; la curva
  ajustada predice menor hit rate para probabilidad dicha baja que para
  probabilidad dicha alta), mismo motor SQLite en memoria aislado que ya
  protegia las otras curvas.
- `test_ranking_comparison.py`: forma del informe walk-forward (4
  estrategias, solo se evaluan liquidaciones despues de `min_train_n`);
  **dos pruebas de espia** que verifican DIRECTAMENTE (no solo infieren)
  que ni la curva de edge ni la de cola reciben jamas datos propios o
  futuros; la curva de cola ignora liquidaciones de favoritos (cuota
  baja) aunque haya muchas.
- Suite completa: 437 tests, todos en verde. `ruff check backend
  scripts`: cero errores nuevos frente al baseline antes de este cambio
  (comparado explicitamente con `git stash`).

### 15.6 Como validar esto (pendiente, pedido explicito al usuario)

Este entorno no tiene acceso a la BD real de produccion. Para decidir
con datos (nunca por intuicion, exactamente el criterio que el propio
usuario puso como condicion) si `v2_edge_cap_longshot` deberia
reemplazar a `v2_edge_cap` en produccion, hace falta correr:

```
docker compose exec backend football-edge compare-ranking-strategies-walk-forward
```

Y comparar, para cada top-N, `v2_edge_cap` (produccion actual) contra
`v2_edge_cap_longshot` (candidata): ROI, win-rate, Brier, log-loss,
max-drawdown y `n_bets` (tamanho de muestra -- un top-5/10 con muy poca
muestra no es una base solida para decidir nada, por buen aspecto que
tenga). Solo se activa en produccion si `v2_edge_cap_longshot` mejora de
forma consistente y con muestra razonable -- si no, se queda construida
y testeada pero inactiva, igual que ya paso con el techo de cuota.

### 15.7 Limitaciones que siguen existiendo

- `LONGSHOT_ODDS_THRESHOLD = 4.0` se eligio directamente de donde la
  seccion 13.2 mostraba sobreconfianza clara en los datos agregados por
  bucket -- no se ha explorado si un umbral distinto (3.5, 4.5...)
  rendiria mejor; el comparador walk-forward puede usarse para probar
  variantes si hace falta.
- La curva de cola pool TODAS las competiciones/mercados juntas (mismo
  motivo que las otras dos: asi se midio la evidencia) -- con mas
  historico, valdria la pena probar si una curva por mercado mejora
  sobre esta.
- `WALK_FORWARD_REFIT_EVERY = 25` es una eleccion de eficiencia, no
  validada por si misma -- un valor mas pequenho seria mas fiel
  (recalibra mas a menudo) a costa de mas computo; no se ha medido si
  esto cambia el resultado de forma importante a la escala actual de
  datos (~1133 liquidaciones).
- El comparador walk-forward, igual que `ensemble_calibration_report`,
  esta verificado solo con datos SINTETICOS en esta sesion -- su
  comportamiento con el volumen y la distribucion reales de las 5 ligas
  del MVP todavia no se ha observado (seccion 15.6).

### 15.8 Hallazgo tras la primera ejecucion real: v2/v2_edge_cap/v2_edge_cap_longshot salen IDENTICOS

El usuario corrio `compare-ranking-strategies-walk-forward` sobre su BD
real (n=1133, `min_train_n=30` por defecto) y las tres estrategias con
curva (`v2_edge_cap`, `v2_edge_cap_longshot`) salieron BYTE A BYTE
identicas a `v2` (sin ningun techo) en TODOS los top-N (5/10/20/50/100) y
en TODAS las metricas (ROI, win-rate, PnL, max-drawdown, Brier) -- un
resultado mucho mas fuerte que "el techo rara vez actua", y que exige NO
asumir una causa (instruccion explicita del usuario para toda esta
auditoria) sino comprobarla.

Dos reproducciones sinteticas para descartar un bug en el bucle
walk-forward en si:

1. Con un `risk_adjusted_expected_value` OPTIMISTA (`model_probability *
   market_odds - 1`, sin ajuste de incertidumbre) y una relacion
   edge-vs-acierto fuertemente decreciente desde el principio de la
   serie cronologica: `v2_edge_cap` SI difiere de `v2` de forma clara
   (p.ej. top-10: `v2 roi=0.0` vs `v2_edge_cap roi=-1.0`).
2. Con un `risk_adjusted_expected_value` CONSERVADOR-WILSON (igual que
   produccion de verdad: `compute_expected_value(uncertainty_low,
   market_odds)`, con `uncertainty_low` calculado via
   `compute_uncertainty_interval` con tamanhos de muestra efectivos
   pequenhos tipicos, 3-20): `v2_edge_cap` TODAVIA difiere de `v2`,
   aunque con una magnitud menor (p.ej. top-20: `v2 roi=-1.0` vs
   `v2_edge_cap roi=-0.25`).

Ninguna de las dos reproducciones (ver
`test_compare_ranking_strategies_walk_forward_*` para la version con
"spy", y los experimentos ad-hoc de esta fase) logra el resultado real:
cero diferencia en absolutamente ningun top-N. Esto descarta que el
bucle walk-forward este roto en si mismo (si lo estuviera, mis
reproducciones sinteticas tambien saldrian identicas) y apunta a algo
especifico de la distribucion real de datos: el candidato mas probable
es que, para las predicciones que YA quedan en el TOP del ranking segun
`v2` (es decir, las de mayor `risk_adjusted_expected_value` crudo tras el
recorte Wilson), la curva de edge/cola ajustada con el historico real
nunca prediga un `hit_rate` tan bajo como para que
`min(raw_ev, curva_implica_ev)` se resuelva por debajo de `raw_ev` --
dicho de otro modo, puede que el propio recorte Wilson de `v2` ya este
descartando (empujando a score bajo/cero) precisamente las apuestas de
edge extremo que el techo esta disenhado para corregir, dejando sin
efecto visible al techo justo donde mas importa (el TOP del ranking).

Para comprobar esto con evidencia real (no suposicion) se anhadio
`scripts/debug_walk_forward_cap_binding.py`: reutiliza EXACTAMENTE la
misma logica de produccion (nunca la reimplementa) para recorrer el
mismo walk-forward e instrumentar, fila a fila:

1. cuantas predicciones evaluadas tienen `risk_adjusted_expected_value`
   crudo ya <= 0 (ahi ningun techo puede cambiar nada, por el
   `max(capped, 0)` final de `signal_score_v2`);
2. de las que tienen EV crudo > 0 (las UNICAS donde un techo podria
   actuar), cuantas tienen el techo de edge/de cola REALMENTE activo
   (`capped_ev < raw_ev`, comprobado via la propia
   `reality_capped_expected_value`, nunca reimplementado);
3. especificamente dentro del TOP-100 de `v2`, cuantas de esas
   predicciones cambian de score bajo `v2_edge_cap`/
   `v2_edge_cap_longshot`.

Verificado con un escenario sintetico disenhado para que el techo SI
actue (test
`test_main_runs_end_to_end_and_detects_a_real_edge_cap_binding` en
`backend/tests/unit/test_debug_walk_forward_cap_binding_script.py`): el
script detecta correctamente el binding cuando ocurre, asi que un
resultado de "cero binding" al correrlo contra la BD real seria una
senhal fiable, no un falso negativo del propio script.

**Pendiente**: pedir al usuario que ejecute

```
docker compose exec backend python scripts/debug_walk_forward_cap_binding.py
```

y pegue el resultado, antes de proponer ningun cambio de codigo (sigue
vigente "no modifiques codigo hasta haber identificado la causa").

### 15.9 Causa real confirmada: `risk_adjusted_expected_value` NULL en el 100% del historico liquidado

El usuario corrio el diagnostico de la seccion 15.8. Resultado: de las
1103 predicciones liquidadas evaluadas, el 100% tiene
`risk_adjusted_expected_value` crudo <= 0 -- y de esas, el 100% es
literalmente NULL (0 son `0.0` explicito, 0 son negativas). `max(capped_ev,
0.0)` en `signal_score_v2` ya da cero para TODAS sin necesidad de ningun
techo, asi que el techo de edge/cola no puede diferenciar nada: no hay
nada por encima de cero que recortar. El puzle original ("por que v2 /
v2_edge_cap / v2_edge_cap_longshot salen identicos") queda resuelto, pero
revela algo mas serio: `v2` en si misma nunca ha tenido un termino de EV
ajustado al riesgo real para evaluar su ranking contra el historico
liquidado.

Causa raiz (confirmada con `git log`, nunca una suposicion):
`uncertainty_low`/`uncertainty_high` se desplegaron en el commit
`72b21fe` (2026-11-11); `risk_adjusted_expected_value` se desplego en el
commit `226d467` (**2026-11-17**, el mismo dia de esta sesion). Como este
campo queda CONGELADO en el momento de crear cada prediccion (nunca se
recalcula solo, ver docstring de `backtesting/ranking_comparison.py`),
toda prediccion liquidada hasta hoy quedo con `uncertainty_low` correcto
pero `risk_adjusted_expected_value` sencillamente inexistente en ese
momento -- no es un bug de calculo, es que el campo es mas nuevo que
absolutamente todo el historico liquidado real.

Dos caminos posibles, puestos al usuario explicitamente (nunca decidido
unilateralmente, por la instruccion "no modifiques codigo hasta haber
identificado la causa" Y por tocar datos liquidados, ambas sensibles):
esperar a que se acumule historial nuevo (semanas/meses), o rellenar
(`backfill`) el hueco con la MISMA formula determinista que ya usa
produccion. El usuario elige el backfill.

**`scripts/backfill_risk_adjusted_expected_value.py`** (nuevo, sigue el
patron ya existente de `purge_excluded_bookmaker_odds.py` para mutaciones
puntuales explicitamente pedidas): para cada `Prediction` con
`risk_adjusted_expected_value IS NULL` y `uncertainty_low`/`market_odds`
disponibles, calcula `compute_expected_value(uncertainty_low,
market_odds)` -- la misma funcion pura que usa `predictor.py` para cada
prediccion nueva -- y rellena SOLO esa columna. Garantias, verificadas con
4 tests reales contra BD (`test_backfill_risk_adjusted_expected_value_script.py`):

- nunca toca `model_probability`/`market_probability`/`edge`/el resultado
  liquidado -- SOLO la columna derivada que faltaba;
- nunca sobreescribe una fila que YA tiene `risk_adjusted_expected_value`
  poblado (nunca "corrige" lo que el pipeline en vivo ya calculo, aunque
  recalcularlo diera un numero distinto);
- nunca inventa un valor para una fila sin `uncertainty_low` o sin
  `market_odds` (coherente con "no inventes datos si el registro ya no
  esta disponible", seccion 9 del pedido original);
- idempotente: una segunda pasada no cambia ninguna fila ya rellenada.

**Pendiente**: pedir al usuario que ejecute

```
docker compose exec backend python scripts/backfill_risk_adjusted_expected_value.py
```

y despues repita `compare-ranking-strategies-walk-forward` -- esa es la
primera vez que esa comparacion tendra datos reales con los que
diferenciar `v1`/`v2`/`v2_edge_cap`/`v2_edge_cap_longshot`.

### 15.10 Primera comparacion real (post-backfill): v1 le gana a toda la familia v2

El usuario corrio el backfill (1133 filas rellenadas) y repitio
`compare-ranking-strategies-walk-forward`. Primera vez que esta
comparacion tiene datos reales con los que diferenciar algo. Dos
resultados:

1. **La pregunta que motivo la seccion 15 (¿activar
   `v2_edge_cap_longshot`?): NO**, por la propia regla del usuario
   ("si no demuestra mejorar la anterior, no la activa"). En los cortes
   mas selectivos (top-5/10/20) sale IDENTICA a `v2_edge_cap`
   (produccion): roi=-1.0, win_rate=0% en top-5 y top-10 para ambas. Solo
   mejora algo en top-50/100 (roi=-0.366 vs -0.474 en top-50; -0.276 vs
   -0.335 en top-100), pero sigue en negativo y sigue peor que `v1` en
   esos mismos cortes. Se queda construida, testeada y documentada, sin
   activar -- igual que el techo de cuota (seccion 14).

2. **Hallazgo mas serio, no buscado**: `v1` le gana a TODA la familia v2
   (incluida `v2_edge_cap`, produccion actual) en absolutamente todos los
   top-N:

   | top-N | v1 roi | v2_edge_cap roi (produccion) |
   |---|---|---|
   | 5 | -0.403 | -1.000 |
   | 10 | -0.241 | -1.000 |
   | 20 | -0.176 | -0.860 |
   | 50 | **+0.016** (unico positivo de toda la tabla) | -0.474 |
   | 100 | -0.009 | -0.335 |

   Salvedades explicitas (seccion 5 del pedido original: "no elijas la
   estrategia con mayor ROI sin considerar tamanho de muestra, drawdown y
   estabilidad temporal"): top-5/10/20 son muestras de 5-20 apuestas,
   nada fiables en solitario; el bucket "all" da roi=-0.091 igual en las
   cuatro estrategias por construccion (el orden no importa si se
   apuesta a todo) y solo refleja el vig del mercado.

El usuario pidio investigar ANTES de tocar produccion: comprobar si
`v1 > v2_edge_cap` es real y estable, o un artefacto de un tramo
concreto del historico (una liga con pocos datos liquidados todavia, un
periodo de un modelo mas viejo).

**`scripts/debug_walk_forward_by_segment.py`** (nuevo): repite
`compare_ranking_strategies_walk_forward` (nunca una formula nueva, nunca
reimplementada) sobre varios recortes del mismo historico real, siempre
con la misma metodologia walk-forward:

1. todo el historico (linea base);
2. cada competicion por separado;
3. cada mercado liquidado por separado;
4. primera mitad cronologica vs segunda mitad cronologica, partidas por
   la fecha MEDIANA real de kickoff (no un numero de dias arbitrario),
   para que ambas mitades tengan tamanho de muestra comparable -- prueba
   directamente si la diferencia se concentra en un periodo concreto.

Imprime solo `v1`/`v2`/`v2_edge_cap` en top-20/top-50 (bastante muestra
sin llegar al "all", que no aporta nada a esta pregunta por
construccion); `v2_edge_cap_longshot` ya quedo descartada arriba, no se
repite. Anhadido tambien `min_kickoff`/`max_kickoff` a
`load_settled_predictions_with_kickoff` para poder partir por fecha sin
tocar la funcion de comparacion en si. Dos tests nuevos reales contra BD
(`test_debug_walk_forward_by_segment_script.py`): el filtro de fecha
selecciona exactamente lo esperado, y el script completo corre sin
`DetachedInstanceError` ni ningun otro error.

**Pendiente**: pedir al usuario que ejecute

```
docker compose exec backend python scripts/debug_walk_forward_by_segment.py
```

y pegue el resultado completo -- con eso se decide si la diferencia
`v1 > v2_edge_cap` es real/estable (y entonces hay que revisar produccion
en serio) o se concentra en un tramo concreto (y entonces el hallazgo es
mas matizado de lo que parece en el agregado).

## 16. Fase 15 (sesion 2026-11, continuacion): el filtro duro, no el scoring, es lo que faltaba

### 16.1 El desglose confirmo que `v1 > v2_edge_cap` es real y estable

El usuario corrio `debug_walk_forward_by_segment.py`. Resultado: la
diferencia NO es un artefacto de una liga ni de un tramo temporal
concreto -- se mantiene en las dos mitades cronologicas (top-20: primera
mitad v1=-0.176 vs v2_edge_cap=-0.860; segunda mitad v1=-0.103 vs
v2_edge_cap=-0.475) y en la mayoria de competiciones (laliga,
bundesliga, serie_a, ligue_1: v1 gana claramente en top-20; premier_league
es la unica razonablemente pareja). Por mercado es mas mixto (algunos,
como `double_chance_x2`/`under_2_5`, favorecen a v2_edge_cap en algun
corte), pero el patron agregado es consistente: real, no un artefacto de
muestra.

### 16.2 Mientras tanto, en produccion: el caso de siempre, sin resolver

El usuario mando una captura real del dashboard (2026-11-17): "Destacados
de la jornada" / "Mejores oportunidades" mostrando Getafe-Atletico
"Victoria local" cuota 5.50 (modelo 33.5%, edge 16.5%), Nice-PSG
"Victoria local" cuota 6.00 (modelo 28.3%, edge 12.8%) y Elversberg-Freiburg
"Victoria local" cuota 3.40 (modelo 44.7%, edge 16.4%) como protagonistas
-- el MISMO patron que motivo TODA esta investigacion desde la seccion 1,
pese a los techos de EV ya activos (secciones 12-15). Feedback directo del
usuario: "simplemente quiero que las mejores predicciones no sean las que
mas edge tienen... quiero que actue con sentido comun... dejate de tocar
mil mierdas y empieza a arreglarlo ya".

Diagnostico (sin tocar mas curvas): `/predictions/top-signals` (la unica
llamada que alimenta tanto "Destacados" como la rejilla completa del
dashboard, ver `DashboardBoard.tsx`) llama a `rank_signals_v2` con
`edge_reliability_curve` -- el pipeline correcto. El problema nunca fue
el WIRING: es que `evaluate_quality_gate` (el filtro DURO que decide QUE
entra, antes de cualquier scoring) ya tenia exactamente el mecanismo
necesario (`max_signal_odds`, excluye cuotas por encima de un maximo) --
pero su valor por defecto (`6.0`) se eligio el 2026-09-18 SIN la
evidencia real que se reunio despues. Con ese valor, cuota 5.50 y 6.00
simplemente pasan el filtro, y por mucho que el scoring las penalice
despues, siguen compitiendo por el top-N -- exactamente el escenario que
el propio comentario de `max_signal_odds` ya advertia ("si un dia hay
pocas senhales candidatas, un tiro muy largo podria colarse igual").

Los techos de EV (secciones 12-15) intentaron resolver esto VIA SCORING
(penalizar sin excluir), y la seccion 15.10 acaba de demostrar con
evidencia real out-of-sample que, en la practica, no penalizan lo
bastante: `v1` sigue ganando a `v2_edge_cap` en casi todos los cortes.

### 16.3 El fix: bajar `max_signal_odds` de 6.0 a 4.0 (mismo mecanismo, evidencia real)

Nunca una formula nueva ni pesos inventados ("no quiero una formula
arbitraria con pesos inventados", pedido original, seccion 7): se ajusta
el valor de un filtro que YA EXISTIA, a donde la evidencia real
(`debug_odds_vs_hitrate.py`, n=1133, seccion 13.2) ya mostraba que hacia
falta:

| cuota | hit rate real | ROI real |
|---|---|---|
| 1-2 | 68.2% | -2.5% |
| 2-3 | 38.7% | -10.3% |
| 3-4 | 28.4% | -2.8% |
| **4-6** | **13.9%** (modelo decia ~21.7%) | **-34.3%** |
| **6+** | **3.6%** (modelo decia ~15.8%) | **-77.9%** |

El ROI historico es ~vig normal hasta cuota 4 y se desploma justo
despues -- exactamente donde ya estaba puesto `LONGSHOT_ODDS_THRESHOLD`
(seccion 15) para otro mecanismo. `max_signal_odds` pasa de `6.0` a
`4.0` (`backend/app/config/settings.py`): Getafe (5.50) y Nice-PSG (6.00)
quedan excluidos de TODO ranking de senhales (`/top-signals`, `/best`,
dashboard) desde el filtro duro, nunca solo penalizados en el scoring;
Elversberg (3.40) sigue pasando, correctamente, porque la evidencia real
en ese rango de cuota es ~vig normal, no catastrofica.

Efecto colateral deliberado: esto tambien hace que los techos de EV
(edge/cola, secciones 12/15) dejen de tener casi nada que corregir en el
rango de cuota donde mas fallaban en la practica (>=4), porque esas
senhales ya no llegan al scoring. Se mantienen activos (edge) o
construidos-pero-inactivos (cola) para el rango 1.45-4.0 restante, sin
cambios.

Tests actualizados/anhadidos (`test_ranking.py`):
`test_max_signal_odds_default_excludes_the_real_getafe_style_longshot`
(regresion directa del caso real, cuota 5.50 excluida por defecto; cuota
3.90 sigue pasando) y las 3 pruebas existentes que fijaban el longshot a
cuota 5.5 para comprobar el EFECTO del scoring (no del filtro duro) se
actualizaron para pasar `Settings(max_signal_odds=10.0)` explicito, sin
cambiar lo que cada una verifica. Texto de `frontend/app/top-signals/page.tsx`
actualizado (decia "cuota entre 1.45 y 6.0"). Suite completa: 446/446 en
verde.

### 16.4 Limitaciones que siguen existiendo

- `4.0` sigue siendo un umbral, no una funcion continua de la evidencia
  real -- una cuota 4.1 con un modelo excepcionalmente bien respaldado
  tampoco competira nunca, aunque en teoria pudiera ser una senhal
  solida. Es la misma limitacion que ya tenia `max_signal_odds` con
  `6.0`, ahora con un valor con evidencia real detras en vez de uno
  elegido a ojo.
- No resuelve el hallazgo mas amplio de la seccion 15.10/16.1
  (`v1 > v2_edge_cap` dentro del rango 1.45-4.0 que SIGUE compitiendo) --
  sigue pendiente decidir que hacer con eso, pero ya no es lo que
  produce el sintoma visible en el dashboard.
- Ajustable (`max_signal_odds` sigue siendo un valor de `Settings`, no
  hardcodeado) si el usuario, con mas historico, quiere moverlo.

## 17. Fase 16 (sesion 2026-11, continuacion): arranque de "Selecciones"

### 17.1 Pedido del usuario

Nueva seccion para partidos de SELECCIONES nacionales, "igual que con los
clubes", empezando ya (con un ojo puesto en anhadir Segunda Division de
las 5 ligas mas adelante si esto funciona bien). Pedido explicito: nunca
adivinar que ofrece la API de memoria, comprobarlo con datos reales
primero.

### 17.2 Reconocimiento real contra API-Football (seccion 16 del script)

`scripts/debug_api_football_national_teams.py` (ver commit anterior) se
corrio de verdad contra `/leagues?country=World` (181 competiciones
reales devueltas). Hallazgo clave: de TODAS las competiciones de
selecciones, la UNICA con cobertura de cuotas real (`coverage.odds=True`)
que corresponde a un combinado "top" bien conocido es **UEFA Nations
League** (id=5) -- Mundial, Eurocopa y TODOS sus clasificatorios salieron
con `coverage.odds=False`. `Friendlies` (id=10) tambien tiene
`coverage.odds=True`, pero mezcla selecciones absolutas con sub-21/sub-17
bajo el mismo `league.id` sin forma de separarlos por el nombre del
equipo salvo heuristicas fragiles -- se deja fuera por ahora.

UEFA Nations League, ademas: tiene liga/clasificacion propia (League
A/B/C/D, estructura mucho mas parecida a una liga de club que un torneo
de eliminatoria suelto), partidos reales en el momento exacto de
comprobarlo (Denmark vs Norway, Wales vs Portugal, jornada 6 de "League
A"/"League B"), y 4 ediciones completas desde 2018 (2018, 2020, 2022,
2024, mas la 2026 en curso) de historial real para entrenar.

### 17.3 Verificacion tecnica antes de prometer "igual que con los clubes"

- **Jornada actual** (`services/round_service.py`): depende de
  `Match.matchday` numerico. Las rondas de fase de grupos de Nations
  League ("League A - 6", "League B - 6") SI terminan en un numero, asi
  que `parse_matchday` (regex generica ya existente, `fixtures_provider.py`)
  las reconoce sin cambios. Las rondas de eliminatoria final
  ("Quarter-finals", "Play-In Final") NO tienen numero -> `matchday=None`
  -> esos partidos concretos caen al fallback por fecha ya existente
  (`is_fallback=True`, documentado en el propio modulo) en vez de romper
  nada -- comportamiento ya preparado para esto, sin tocar codigo.
- **Ingesta historica**: a diferencia de las 5 ligas de club (mirror
  `football-data.co.uk`/`openfootball`, `cli.py::update`), NO existe un
  mirror asi para selecciones -- la UNICA fuente real es API-Football
  (`ApiFootballMatchProvider`, que ya implementa `DataProvider` de forma
  generica). `data_service.ingest_matches` ya es agnostica al proveedor,
  asi que no hizo falta tocarla.
- **Cuotas/fixtures en vivo**: `update-fixtures`/`update-odds` ya reciben
  `--competition` como string libre y usan `LEAGUE_IDS`/`COMPETITIONS`
  por codigo -- funcionan para `uefa_nations_league` sin ningun cambio,
  en cuanto el codigo este registrado.
- **Etiquetado de temporada**: API-Football numera cada EDICION de
  selecciones con un unico anho (nunca "YYYY/YY" como club). Para que la
  edicion EN CURSO comparta la misma fila de `Season` que
  `update-fixtures`/`update-odds` generarian en vivo (y no la duplique),
  el nuevo comando historico convierte cada anho de edicion a traves de
  la MISMA `season_label_from_date` que ya usa el resto del sistema
  (`dt.date(year, 9, 1)` -> "2024/25"), nunca un anho suelto.

### 17.4 Lo que se anhadio

- `normalization/competitions.py`: `"uefa_nations_league": ("UEFA Nations
  League", "Europe")`.
- `ingestion/api_football/client.py`: `LEAGUE_IDS["uefa_nations_league"]
  = 5` (ID real confirmado, nunca de memoria) y
  `INTERNATIONAL_COMPETITION_EDITIONS = {"uefa_nations_league": [2018,
  2020, 2022, 2024, 2026]}` (bienal desde 2018, nunca anual).
- `cli.py::update-international` (nuevo comando): recorre TODAS las
  ediciones conocidas de una competicion de selecciones en una sola
  ejecucion (el volumen es pequenho, no hace falta la logica de "solo
  reingerir la temporada en curso" de `update()`), reutilizando
  `ingest_matches`/`ApiFootballMatchProvider` sin cambios. Mismo patron
  de recuperacion ante fallo que `update()`/`update-fixtures`
  (`db.rollback()` entre ediciones, un fallo no tumba las demas).
- Tests reales (`test_cli_update_international.py`, 3 nuevos): las 5
  ediciones se ingieren con el `Season.label` correcto, un codigo de
  competicion desconocido falla limpio (`typer.Exit`), y un fallo en UNA
  edicion no impide que las demas se ingieran. `test_league_ids_cover_all_five_mvp_leagues`
  se corrigio para comprobar SUBCONJUNTO (no igualdad exacta): anhadir
  una competicion nueva a `LEAGUE_IDS` ya no rompe esa prueba por
  diseño. Suite completa: 452/452.
- `TeamLogo.tsx`: el recorte circular del escudo (`rounded-full`) se
  quito (`rounded-lg`) -- pedido aparte del usuario, pero relevante aqui:
  las banderas/escudos de selecciones tienden a ser MAS rectangulares
  que los de club, asi que el recorte circular las habria cortado aun
  peor.

### 17.5 Pendiente (siguiente paso real, pedido al usuario)

Backend ya registrado, pero SIN datos reales todavia. Orden real para
tener Nations League funcionando de punta a punta:

```
docker compose exec backend football-edge update-international --competition uefa_nations_league
docker compose exec backend football-edge update-fixtures --competition uefa_nations_league
docker compose exec backend football-edge update-odds --competition uefa_nations_league
docker compose exec backend football-edge train --competition uefa_nations_league
docker compose exec backend football-edge predict-upcoming --competition uefa_nations_league
```

Tras esto, `uefa_nations_league` deberia aparecer solo en el selector de
competiciones de la app (`/competitions` lista lo que haya en BD con
`enabled=True`, nunca una lista hardcodeada en el frontend -- verificado,
no hizo falta tocar nada ahi).

### 17.6 Limitaciones honestas que siguen pendientes

- **Entrenar con pocos partidos por edicion** (League A tiene ~6
  partidos de fase de grupos por equipo y edicion): el feature
  engineering actual (medias moviles de "ultimos N partidos") esta
  pensado para el ritmo semanal de una liga de club -- para selecciones,
  "ultimos 5 partidos" de un equipo puede abarcar 2-3 anhos y varias
  generaciones de plantilla/seleccionador. No se ha tocado todavia; a
  vigilar con `ensemble_calibration_report` una vez haya predicciones
  liquidadas reales.
- **Friendlies** queda fuera por ahora (mezcla selecciones absolutas con
  categorias inferiores bajo el mismo `league.id`) -- si mas adelante
  interesa, habria que filtrar por nombre de equipo (heuristica fragil,
  nunca garantizada) o buscar si API-Football separa esto de otra forma.
- **Segunda Division de las 5 ligas** (mencionado por el usuario como
  posible siguiente paso si esto va bien): mismo patron que esta fase
  (confirmar IDs/cobertura real de la API antes de nada), pero esas SI
  tendrian mirror historico tipo football-data.co.uk/openfootball
  (verificar cobertura real antes de asumirlo) -- mas parecido al flujo
  de las 5 ligas actuales que a esta seccion.
- Seccion de frontend "Selecciones" (nav, filtros, dashboard) todavia
  NO se ha construido -- pendiente de que haya datos reales fluyendo
  primero (seccion 17.5).

### 17.7 Bug real al ejecutar 17.5: `Competition.code` desbordaba en Postgres

El usuario corrio la secuencia de la seccion 17.5 contra su Postgres real
y las 5 ediciones fallaron: `StringDataRightTruncation: value too long
for type character varying(16)`. Causa: `Competition.code` se definio
`String(16)` pensando solo en codigos cortos de club ("laliga",
"premier_league", 15 caracteres como maximo); `"uefa_nations_league"`
tiene 20. La suite de tests local NUNCA detecto esto porque corre contra
SQLite (ver `conftest.py`), que no impone la longitud declarada de un
`VARCHAR` -- la misma cadena de 20 caracteres se inserta sin queja ahi,
solo Postgres la rechaza de verdad.

Fix de dos partes:
- `db/models/core.py`: `Competition.code` pasa de `String(16)` a
  `String(64)` (margen real para codigos de selecciones mas largos
  todavia, p.ej. clasificatorios).
- `db/database.py::_widen_varchar_column_if_needed` (nuevo, llamado desde
  `_sync_missing_columns`, que ya corre en cada `init_db()`): ensancha
  SOLO en Postgres (SQLite nunca lo necesita) una columna `VARCHAR`
  existente cuya longitud en la BD real sea menor que la que el modelo
  ahora declara -- nunca la estrecha (perderia datos). Util no solo para
  este bug: cualquier columna de texto que en el futuro necesite mas
  espacio se autocorrige en el Postgres del usuario la proxima vez que
  arranque el backend, sin migracion manual.

6 tests nuevos (`test_database_widen_varchar_column.py`) con dialectos
reales de SQLAlchemy (`sqlalchemy.dialects.postgresql`/`sqlite`, sin
necesitar un Postgres real, no disponible en este entorno): ensancha
cuando hace falta, nunca estrecha, no hace nada si la longitud ya
coincide, no hace nada si la columna reflejada no tiene longitud (p.ej.
`TEXT`), y nunca toca SQLite. Suite completa: 458/458.

**El usuario no necesita hacer nada especial**: la proxima vez que
reconstruya/reinicie el backend (`docker compose up --build` o
equivalente), `init_db()` ensancha la columna sola. Despues, repetir la
secuencia de la seccion 17.5 tal cual -- esta vez debería completarse sin
el error de truncamiento.

### 17.8 Primera ejecucion real completa: 210 predicciones generadas

El usuario corrio la secuencia completa de la seccion 17.5 tras el fix de
la 17.7 (con `git pull` + `docker compose up -d --build` de por medio).
Las 5 ediciones de Nations League se ingirieron, `update-fixtures`/
`update-odds` trajeron el calendario y cuotas reales, `train` entreno sin
reventar, y `predict-upcoming` genero **210 predicciones reales en 7
fechas** -- el pipeline completo funciona de punta a punta para una
competicion de selecciones, igual que para una de club.

Limitacion real, esperada y no bloqueante, visible en los logs
(`sklearn.impute... Skipping features without any observed values`,
repetido por cada fold de entrenamiento): `ApiFootballMatchProvider`
(`fixtures_provider.py`) solo extrae goles/goles al descanso del payload
basico de `/fixtures` -- nunca tiros/corners/tarjetas (esos campos de
`RawMatchRecord` existen porque el adapter de club
`football-data.co.uk` SI los rellena desde su CSV). Para las 5 ligas de
club esto solo afecta a la temporada EN CURSO cuando
`API_FOOTBALL_ENABLED=true` (el resto del historico sigue viniendo del
mirror con estadisticas completas); para Nations League, CUALQUIER
partido (historico y actual) viene de API-Football, asi que el 100% de
las filas tiene estas columnas vacias. `SimpleImputer` ya maneja esto con
gracia (salta la columna en vez de reventar, mismo comportamiento que ya
existia para cualquier columna sin ningun valor observado) -- el modelo
entrena igual con las features de goles, simplemente sin el beneficio de
las derivadas de tiros/corners/tarjetas para esta competicion. No se ha
tocado nada aqui: documentado para que quede claro que es una limitacion
REAL de la fuente de datos, no un bug nuevo.

**Pendiente de verificar (siguiente paso)**: confirmar que "UEFA Nations
League" aparece en el selector de competiciones de la app y que las 210
predicciones se ven correctamente en Partidos/Oportunidades -- antes de
construir la seccion de frontend dedicada "Selecciones".

### 17.9 Verificado y seccion de frontend "Selecciones" construida

El usuario confirmo con una captura real: "UEFA Nations League" aparece
en el selector de competiciones, y el Historico de jornadas muestra sus
partidos con "mejor prediccion" congelada vs resultado real -- el mismo
mecanismo que ya usan las 5 ligas de club (nada especifico de club
estaba hardcodeado en ese flujo, como ya se habia verificado en la
seccion 17.5).

Pedido explicito: una pagina DEDICADA (no solo un filtro dentro de
"Partidos"), porque los partidos de selecciones se concentran en pocos
parones al año (a diferencia de una liga de club, con partidos casi cada
semana) -- lo que el usuario quiere ver es CUANDO son los proximos, de un
vistazo, agrupados por fecha.

`frontend/app/selecciones/page.tsx` (nuevo): reutiliza `getMatchesList`
(el mismo endpoint que ya usa "Partidos", sin tocar el backend) filtrado
a las competiciones de `INTERNATIONAL_COMPETITION_CODES`
(`frontend/lib/internationalCompetitions.ts`, nuevo -- lista EXPLICITA de
codigos, igual que `normalization/competitions.py::COMPETITIONS` en el
backend, nunca inferida de `competition.country`: una futura competicion
de CLUB tambien podria tener `country="Europe"`, p.ej. la Champions
League). Agrupa los proximos partidos por fecha real
(`formatMatchDateOnly`, misma utilidad de zona horaria ya existente),
destaca el proximo partido arriba del todo, y muestra los ultimos
resultados liquidados debajo. El desglose completo de cada partido
(prediccion, comparativa modelo-vs-mercado) sigue viviendo en
`/matches/[id]` -- esta pagina nunca duplica esa logica, solo enlaza a
ella. Nuevo item de navegacion "Selecciones" en `Sidebar.tsx`, separado
de "Partidos".

`npm run build` compila limpio (ruta `/selecciones` generada como pagina
estatica) y `tsc --noEmit` sin errores.

**Pendiente**: el usuario aun no ha visto esta pagina en vivo -- pedirle
que la abra y confirme que el agrupado por fecha/el proximo partido se
ven como se espera.

### 17.10 Feedback real: falta en el menu + "solo Nations League"

El usuario reporto dos cosas con una captura real:

1. "Selecciones" no aparecia en el menu lateral -- el codigo es correcto
   (`npm run build` ya lo generaba como ruta), asi que es un contenedor
   desactualizado en su maquina (falta `git pull` + reconstruccion
   completa del frontend). Pendiente de confirmacion del usuario tras
   repetirlo.
2. "yo quiero cualquier partido de selecciones de cualquier competicion"
   -- correcto, solo UEFA Nations League estaba registrada. Pedido al
   usuario que decida el alcance (el catalogo real tiene ~180
   competiciones, la mayoria femenino/categorias inferiores/sin cuotas):
   eligio **solo absoluta masculina** -- Mundial + sus clasificatorios
   por confederacion, Eurocopa + clasificatorio, Copa America, Copa
   Africa de Naciones + clasificatorio, y CONCACAF Nations League. Deja
   fuera femenino, categorias inferiores y amistosos.

`scripts/debug_api_football_national_teams.py` ampliado (nueva seccion
"ABSOLUTA MASCULINA"): para los IDs reales ya confirmados en la seccion
17.2 (Mundial=1, sus 6 clasificatorios por confederacion, Eurocopa=4 +
clasificatorio=960, Copa America=9, Copa Africa de Naciones=6 +
clasificatorio=36, CONCACAF Nations League=536), imprime el HISTORIAL
COMPLETO de temporadas (no solo la ultima, a diferencia del resto del
script) -- necesario porque estas competiciones NO son anuales (Mundial/
Eurocopa cada 4 anhos, Copa America/Copa Africa con ciclos irregulares),
asi que `INTERNATIONAL_COMPETITION_EDITIONS` necesita anhos reales por
competicion, nunca adivinados. Si un ID no aparece en la respuesta real,
lo avisa explicitamente en vez de callarse o inventar una temporada.
Reutiliza el mismo `/leagues?country=World` ya en memoria -- ningun
request adicional. 1 test nuevo (respuesta canned con 2 ediciones reales
de un ID + un ID ausente, verifica ambos casos). Suite completa: 459/459.

**Pendiente**: pedir al usuario que ejecute

```
docker compose exec backend python scripts/debug_api_football_national_teams.py
```

de nuevo y pegue el resultado completo (incluida la seccion nueva
"ABSOLUTA MASCULINA") -- con los anhos reales de cada competicion se
registra `INTERNATIONAL_COMPETITION_EDITIONS` para todas, se ingieren, y
se anhaden a `INTERNATIONAL_COMPETITION_CODES` del frontend. Las que no
tengan `coverage.odds=True` (la mayoria, segun lo ya visto en la seccion
17.2) apareceran en el calendario de "Selecciones" pero SIN predicciones/
señales -- igual que ya pasa con cualquier partido "solo modelo" sin
cuota real en el resto de la app, nunca una excepcion nueva.

### 17.11 Bug real visto en vivo: partidos de Russia de 2022 como "proximo partido"

El usuario mando una captura real de "Selecciones" mostrando "Albania vs
Russia" con fecha de kickoff del 2 de junio (sin anho visible en el
titulo de grupo -- en realidad 2022) como "Proximo partido". Diagnostico
del propio usuario, correcto: "supongo que es porque es Russia y la
quitaron de todas las competiciones, quitemosla nosotros tambien" --
Russia esta suspendida de UEFA/FIFA desde 2022, asi que esos partidos de
Nations League NUNCA se van a jugar, pero API-Football los sigue
devolviendo con `status="scheduled"` indefinidamente (nunca pasan a
finalizado ni a cancelado en la fuente real).

Mismo mecanismo YA EXISTENTE para casas de apuestas excluidas
(`normalization/bookmakers.py::EXCLUDED_BOOKMAKERS`, pedido real anterior
del usuario con Betano), aplicado aqui a EQUIPOS:

- `normalization/teams.py::EXCLUDED_TEAMS = frozenset({"russia"})` +
  `is_excluded_team(raw_name)` (misma normalizacion que
  `is_excluded_bookmaker`: minusculas, sin acentos, espacios colapsados).
- `services/data_service.py::ingest_matches`: cualquier `RawMatchRecord`
  con un equipo excluido de local O de visitante se descarta ANTES de
  `_upsert_match` -- nunca vuelve a crearse en una ingesta nueva, de
  cualquier competicion o proveedor (no especifico de Nations League).
- `scripts/purge_excluded_team_matches.py` (nuevo, mismo patron que
  `purge_excluded_bookmaker_odds.py`): limpia lo que YA estaba guardado
  de ANTES de excluirlo -- tanto partidos `scheduled` (el sintoma visible)
  como `finished` (Russia SI jugo partidos reales de Nations League antes
  de 2022, con `Prediction`/`PredictionResult` real asociado, un caso que
  `cli.py::reset_scheduled` nunca cubre porque solo mira `scheduled`).
  Borra `PredictionResult` y `Prediction` ANTES del `Match` (sin esto,
  Postgres rechaza el borrado por la FK de `PredictionResult
  .prediction_id`, que no tiene `ON DELETE CASCADE`); `MatchOdds`/
  `MatchStatistics`/`MatchOddsHistory` se borran solos via el cascade ORM
  ya existente en `Match`. Nunca borra la fila de `Team` en si (quedaria
  inerte, sin partidos -- mas simple y seguro que tambien limpiar
  `TeamNameMapping`).

3 tests nuevos reales contra BD: la ingesta salta un equipo excluido como
local, como visitante, y con mayusculas/espacios distintos (nunca
depende de coincidencia exacta); el purgado borra partidos `scheduled` Y
`finished` (con su `Prediction`/`PredictionResult` real) de un equipo
excluido sin tocar un partido de control sin ningun equipo excluido, y es
idempotente. Suite completa: 462/462.

**Pendiente**: pedir al usuario que ejecute

```
docker compose exec backend python scripts/purge_excluded_team_matches.py
```

para limpiar los partidos de Russia ya guardados -- el filtro de ingesta
evita que vuelvan, pero no retroactivo.

## 18. Fase 17 (sesion 2026-11, continuacion): Mundial, Eurocopa y el resto de "absoluta masculina"

### 18.1 Resultado real del reconocimiento ampliado

El usuario corrio de nuevo `debug_api_football_national_teams.py` (ya con
la seccion "ABSOLUTA MASCULINA" de la seccion 17.10) y pego el historial
COMPLETO de temporadas de las 14 competiciones que eligio. Confirmado,
nunca adivinado:

| competicion | ID | ediciones reales | `coverage.odds=True` |
|---|---|---|---|
| World Cup | 1 | 2010, 2014, 2018, 2022, 2026 | ninguna |
| World Cup - Qualification Europe | 32 | 2018, 2020, 2024 | ninguna |
| World Cup - Qualification South America | 34 | 2018, 2022, 2026 | ninguna |
| World Cup - Qualification CONCACAF | 31 | 2018, 2022, 2026 | ninguna |
| World Cup - Qualification Africa | 29 | 2018, 2022, 2023 | ninguna |
| World Cup - Qualification Asia | 30 | 2018, 2022, 2026 | ninguna |
| World Cup - Qualification Oceania | 33 | 2018, 2022, 2026 | ninguna |
| World Cup - Qualification Intercontinental Play-offs | 37 | 2014, 2018, 2022, 2026 | ninguna |
| Euro Championship | 4 | 2008, 2012, 2016, 2020, 2024 | ninguna |
| Euro Championship - Qualification | 960 | **solo 2023** (unica real listada) | ninguna |
| Copa America | 9 | 2015, 2016, 2019, 2021, 2024 | ninguna |
| Africa Cup of Nations | 6 | 2015, 2017, 2019, 2021, 2023, 2025 | ninguna |
| Africa Cup of Nations - Qualification | 36 | 2021, 2023, 2025, 2027 | **solo 2027** |
| CONCACAF Nations League | 536 | 2020, 2022, 2023, 2024, 2025 | **solo 2025** |

Confirma lo ya anticipado en la seccion 17.2: NINGUNA de estas tiene
cuotas reales salvo dos casos puntuales (el clasificatorio de Copa Africa
de Naciones y la CONCACAF Nations League, y en ambos SOLO su edicion mas
reciente) -- Mundial y Eurocopa (lo que el usuario pidio por nombre en
este mensaje) aparecen en "Selecciones" con calendario y, una vez
entrenado, prediccion del modelo, pero sin edge/EV real por falta de
cuota, igual que cualquier partido "solo modelo" del resto de la app.

### 18.2 Bug real descubierto durante el reconocimiento: rate limit silencioso

El propio log de la ejecucion del usuario trae la prueba: varias llamadas
a `/fixtures` dentro del mismo reconocimiento salieron con
`errors={'rateLimit': 'Too many requests...'}` -- API-Football devuelve
HTTP 200 con el error DENTRO del JSON (ya documentado en
`client.py::_get`), pero el codigo de antes solo lo logueaba con WARNING
y devolvia `[]`, indistinguible de "de verdad no hay partidos
programados". Con 14 competiciones nuevas por ingerir (~56 llamadas a
`/fixtures` en total), este bug habria corrompido silenciosamente el
historico de varias ediciones.

Fix en `ingestion/api_football/client.py`:
- `ApiFootballRateLimitError` (nueva excepcion): se lanza especificamente
  cuando `errors` contiene la clave `rateLimit` (el UNICO tipo de error
  confirmado contra una respuesta real) -- cualquier otro tipo de error
  sigue con el comportamiento previo (loguear y devolver lo que haya),
  conservador a proposito: nunca se ha visto evidencia de que haga falta
  tratarlos igual.
- Anhadida al `retry_if_exception_type` del decorador `@retry` ya
  existente en `_get` -- un rate limit se reintenta con el MISMO backoff
  exponencial que un error de red real, en vez de fingir una respuesta
  vacia.
- `cli.py::update_international`: pausa 1 segundo entre cada llamada a
  `/fixtures` (defensivo, para no depender solo del reintento con 14
  competiciones x varias ediciones cada una en una sola ejecucion).

3 tests nuevos (`test_api_football_client_rate_limit.py`, con dialectos/
respuestas canned, `time.sleep` monkeypatcheado para no ralentizar la
suite de verdad): se agotan los 3 intentos y propaga la excepcion si el
rate limit persiste; se recupera si un reintento tiene exito antes de
agotarlos; un error que NO sea `rateLimit` nunca se reintenta (sigue
devolviendo `[]` tal cual).

### 18.3 Las 14 competiciones registradas

`normalization/competitions.py`, `ingestion/api_football/client.py`
(`LEAGUE_IDS` + `INTERNATIONAL_COMPETITION_EDITIONS`, con los anhos
reales de la tabla de 18.1, nunca adivinados) y
`frontend/lib/internationalCompetitions.ts` -- las 14 nuevas, ademas de
`uefa_nations_league` ya registrada. `cli.py::update_international` ahora
acepta `--competition` opcional: sin el, recorre TODAS las registradas en
una sola ejecucion (antes exigia repetirlo 14 veces a mano).

2 tests nuevos/actualizados (`test_cli_update_international.py`): el
comportamiento por defecto (sin `--competition`) procesa exactamente las
competiciones registradas en `INTERNATIONAL_COMPETITION_EDITIONS`, y los
tests existentes se adaptan a la pausa de 1s (monkeypatcheada). Suite
completa: 466/466.

**Pendiente**: pedir al usuario que ejecute

```
docker compose exec backend football-edge update-international
docker compose exec backend football-edge update-fixtures
docker compose exec backend football-edge update-odds
docker compose exec backend football-edge train
docker compose exec backend football-edge predict-upcoming
```

(sin `--competition`: ya recorren TODAS las registradas, club y
selecciones por igual) y pegue el resultado -- con eso Mundial, Eurocopa
y el resto deberian aparecer en "Selecciones" con calendario y
predicciones del modelo (sin edge/señal real, por la falta de cuota ya
documentada en 18.1).

### 18.4 Limitaciones honestas que siguen pendientes

- Ninguna de las 14 tiene cuotas reales salvo las dos excepciones ya
  citadas (y solo para su ultima edicion) -- sus predicciones seran
  "solo modelo", nunca apareceran en `/predictions/top-signals` ni en
  "Oportunidades" (el filtro duro `evaluate_quality_gate` exige
  `market_odds`, igual que cualquier otro partido sin mercado real).
- `euro_championship_qualification` solo tiene UNA edicion real listada
  (2023) -- muestra muy pequenha para entrenar un modelo propio de esta
  competicion especifica; a vigilar si el entrenamiento la trata
  razonablemente o si hace falta pool con otra fuente de senhal.
- La pausa de 1s entre llamadas en `update_international` es defensiva,
  no calibrada contra el limite real de la API (la respuesta de error no
  dice el numero exacto) -- si sigue rate-limitando con muchas
  competiciones de golpe, subir la pausa o anhadir un backoff mas largo
  entre COMPETICIONES (no solo entre ediciones) seria el siguiente paso.

## 19. Fase 18 (sesion 2026-11, continuacion): pulido tras ver las 14 competiciones en vivo

El usuario confirmo las 14 competiciones nuevas funcionando (capturas
reales de `/matches`) y pidio tres ajustes directos.

### 19.1 Oportunidades/Histórico: ligas primero, selecciones en su propio grupo

"lo importante son las ligas" -- con 15 competiciones de selecciones a la
vez, mezclarlas en la misma lista plana que las 5 ligas de club (ya
visible en el selector de "Oportunidades") las ahogaba. Nunca se quita
nada, solo se separa:

- `frontend/app/top-signals/page.tsx` (Oportunidades, Server Component
  con `<select>` + formulario GET): el `<select>` de Competicion ahora
  tiene dos `<optgroup>` -- "Ligas" (las 5 de club) y "Selecciones" (las
  15 nuevas) -- en vez de una lista plana.
- `frontend/components/HistoryBoard.tsx` (Histórico, Client Component con
  pills multi-seleccion): misma idea, adaptada a su propio patron -- fila
  "Ligas:" (como ya existia) y una fila nueva separada "Selecciones:"
  (solo se muestra si hay alguna con partidos liquidados), misma
  mecanica de toggle multi-seleccion para ambas.
- Ambos reutilizan `isInternationalCompetition` (ya existente en
  `frontend/lib/internationalCompetitions.ts`, construido en la seccion
  17) para decidir el grupo -- nunca una lista duplicada.

`/matches` (Partidos) NO se toco -- el usuario no lo pidio ahi, y esa
pagina es explicitamente para ver CUALQUIER partido, selecciones
incluidas, sin distincion.

### 19.2 "Selecciones": partidos de 2018 como "proximo partido"

Bug real reportado con captura: al ingerir el historial COMPLETO de las
nuevas competiciones (incluidas ediciones de 2018 en clasificatorios),
algunos partidos quedaron con `status="scheduled"` atascado en la fuente
real aunque su fecha de kickoff sea de hace anhos (misma clase de
problema que el bug de Russia de la seccion 17.11, pero generico: no
hace falta que sea un equipo excluido, cualquier partido viejo con
estado mal reportado por la API cae en el mismo caso).

`/matches` (Partidos) ya evita esto por una razon distinta: su filtro
`dateFrom = currentSeasonStartDate()` (pensado para ligas de club)
excluye de paso cualquier fecha anterior a la temporada actual. La pagina
nueva "Selecciones" no tenia ese filtro. Fix (`frontend/app/selecciones
/page.tsx`): nueva funcion `todayDateOnly()` (fecha de HOY, sin concepto
de "temporada" -- las selecciones no lo tienen limpio) pasada como
`dateFrom` a la consulta de partidos "scheduled" -- nunca se fia solo del
`status` que devuelve la fuente. La consulta de "ultimos resultados"
(`finished`) no lleva este filtro, a proposito (sigue mostrando
resultados reales recientes sin restriccion de fecha).

### 19.3 Verificacion

`npx tsc --noEmit` y `npm run build` limpios (`/selecciones` sigue
generandose como pagina estatica, 2.27 kB).

## 20. Mismo filtro de competicion (desplegable Ligas/Selecciones) en toda la app

Pedido real del usuario: "ponme el mismo filtro de oportunidades en
todas las secciones de la app un menu desplegable, primero las ligas y
luego selecciones". Esto amplia -- y sustituye -- la decision de la
seccion 19.1 de no tocar `/matches`: ahi se dijo explicitamente que esa
pagina no entraba porque el usuario no lo habia pedido "ahi"; en este
mensaje si lo pide, para TODAS las secciones sin excepcion.

### 20.1 Alcance

Grep de `competitionCode|competition_code|getCompetitions|Liga:|Ligas:|
Competición:|optgroup` sobre `frontend/app` y `frontend/components`
encontro 6 archivos con filtro de competicion propio. `/top-signals`
(Oportunidades) ya tenia el patron de referencia (seccion 19.1): un unico
`<select>` con dos `<optgroup>`, "Ligas" primero y "Selecciones" despues
(solo si `internationalCompetitions.length > 0`), usando
`isInternationalCompetition()` de `frontend/lib/internationalCompetitions.ts`
para la division -- nunca inferida de `competition.country`. Los otros 5
se convirtieron al mismo patron exacto:

- `frontend/app/matches/page.tsx` (Partidos): de fila de pill-buttons de
  seleccion unica (todas las competiciones mezcladas) a un `<select>`
  controlado con el mismo split de optgroup.
- `frontend/components/DashboardBoard.tsx` (Dashboard, filtro "Liga:"):
  misma conversion; el filtro "Confianza:" (no es de competicion) se deja
  tal cual, pills.
- `frontend/components/HistoryBoard.tsx` (Historico): este SI cambia de
  comportamiento, no solo de estilo -- antes era multi-seleccion
  (`string[]`, pills en dos filas separadas "Ligas:"/"Selecciones:",
  seccion 19.1) y por defecto no mostraba nada hasta elegir. Ahora es
  seleccion unica (`string | null`) igual que Oportunidades: "Todas" por
  defecto muestra la ultima jornada de cada competicion, una liga
  concreta filtra a solo esa. Decision deliberada: "el mismo filtro de
  oportunidades" se interpreta literalmente -- un desplegable de
  seleccion unica, no una lista con casillas.
- `frontend/components/ModelPerformanceBoard.tsx` (Estadisticas): mismo
  cambio multi->unico que Historico, con el mismo split de optgroup
  (aqui por `competition_code` directamente, ya que `PerformanceSegment`
  no trae nombre legible de competicion -- la opcion muestra el codigo,
  igual que antes mostraba el codigo en el boton).
- `frontend/app/selecciones/page.tsx` (filtro propio de esa pagina): pills
  -> `<select>`, pero SIN optgroup -- todas las opciones de esa pagina ya
  son de selecciones (filtradas a `INTERNATIONAL_COMPETITION_CODES` antes
  de llegar aqui), asi que dividir en dos grupos no aportaria nada.

`frontend/components/StatsOverview.tsx` aparecio en el grep inicial pero
NO tiene filtro propio: recibe siempre el total de `segments` sin
filtrar (el filtro de `ModelPerformanceBoard` es local a ese componente,
nunca se propaga hacia arriba) -- se descarto tras leerlo.

### 20.2 Verificacion

`npx tsc --noEmit` limpio. `npm run build` limpio, las 16 rutas se
generan igual que antes (`/matches` y `/selecciones` siguen estaticas).

## 21. "Rendimiento del modelo": sistema de evaluación y aprendizaje continuo

Pedido real del usuario: transformar el Histórico de jornadas en el
centro de evaluación histórica del motor predictivo -- "no quiero un
simple historial de marcadores, quiero un sistema de evaluación y
aprendizaje continuo".

### 21.1 Auditoría previa (antes de tocar código)

La mayor parte de lo pedido YA EXISTÍA, construido en sesiones
anteriores de este mismo proyecto, y se reutilizó sin duplicar nada:

- Captura prepartido inmutable: `Prediction` ya guardaba probabilidad
  original/calibrada, incertidumbre, EV (normal y ajustado a riesgo),
  edge, `signal_score` CONGELADO, versión de modelo, y todas las
  candidatas (no solo la mejor) -- ver `db/models/modeling.py`.
- Liquidación separada e inmutable: `PredictionResult`, creada por
  `services/evaluation_service.py::settle_finished_predictions`, NUNCA
  sobrescribe `Prediction`.
- Evaluación real (no backtest sintético): Brier/log loss/ECE/ROI/
  calibración por segmento (competición/mercado/versión), con desglose
  por bucket de probabilidad y de edge -- `real_performance_report`.
- Infraestructura walk-forward completa (`backtesting/engine.py`,
  `ranking_comparison.py`).

Lo que NO existía, y es lo que se construyó en esta sesión:

1. **Bug real confirmado**: partidos con marcador ya visible aparecían
   "sin resolver" en el Histórico porque `cli.py::refresh()` liquidaba
   predicciones (`evaluate`, paso 2) ANTES de traer los fixtures reales
   de la temporada en curso (`update_fixtures`, paso 3) -- un partido
   recién finalizado no se liquidaba hasta el SIGUIENTE `refresh`. Fix:
   reordenar `evaluate` a justo antes de `train` (pasos 1-7: historial →
   fixtures → cuotas goles → cuotas corners → evaluar → entrenar →
   predecir).
2. Estados de partido incompletos: solo "scheduled"/"finished", nunca
   "postponed"/"cancelled" -- un aplazamiento quedaba indistinguible de
   un partido normal por jugar, para siempre.
3. Estados de liquidación incompletos: solo booleano (acertada/fallada),
   sin "void" para partidos cancelados -- se liquidarían como "lost",
   penalizando al modelo por algo ajeno a su predicción.
4. Sin gate de promoción de modelo: cada `train` se usaba en producción
   de inmediato, sin comparar nunca con la versión anterior.

### 21.2 Tablas nuevas o modificadas

- `matches.status`: ahora `scheduled|finished|postponed|cancelled`
  (antes solo los dos primeros). Deliberadamente SIN un estado "live"
  aparte: fragmentaría los muchos filtros `status == "scheduled"`
  existentes sin que nada lo necesite hoy (sin apuestas en vivo).
- `prediction_results.settlement_status` (nueva, nullable, mismo patrón
  aditivo que `competitions.tier`): `won|lost|void|half_won|half_lost`.
  Solo `won`/`lost`/`void` los produce el pipeline hoy; `half_won`/
  `half_lost` quedan soportados por el esquema pero sin ningún mercado
  real que los necesite (ver limitaciones).
- `model_versions.is_active` (nueva, nullable): decide
  `services/promotion_service.py`, nunca "la más reciente" a ciegas.

### 21.3 Flujo automático: predicción → liquidación

Sin cambios en la CAPTURA (ya era correcta e inmutable). Lo nuevo es el
ciclo de vida completo del PARTIDO y de su LIQUIDACIÓN:

- `ingestion/api_football/fixtures_provider.py::_match_status`: mapea
  cada estado real de API-Football a uno de los 4 (FT/AET/PEN→finished,
  CANC/ABD/AWD/WO→cancelled, PST/SUSP/INT→postponed, resto→scheduled).
- `RawMatchRecord.status` (nuevo campo, `None` para fuentes que no lo
  distinguen) viaja hasta `data_service.py::_upsert_match`, que lo
  prefiere sobre la inferencia antigua (goles→finished) cuando está
  presente.
- `evaluation_service.py::settle_finished_predictions` (sin cambios de
  comportamiento, solo ahora también fija `settlement_status`) +
  `void_cancelled_predictions` (nueva): partidos "cancelled" con
  predicciones sin liquidar se anulan ("void", stake devuelto), nunca se
  cuentan como "lost". "postponed" se deja intacto a propósito (puede
  reprogramarse bajo el mismo id y jugarse más tarde -- anularlo
  prematuramente violaría la inmutabilidad).
- Todas las agregaciones reales (`real_performance_report`,
  `market_track_records`, las 3 curvas de fiabilidad) EXCLUYEN
  explícitamente las liquidaciones "void" -- nunca se mezclan con
  ganancias/pérdidas reales. `real_performance_report` reporta `n_void`
  aparte por segmento.

### 21.4 Aprendizaje controlado: gate de promoción de modelo

`services/promotion_service.py::evaluate_and_promote`, llamado desde
`cli.py::train` tras cada entrenamiento:

- Sin versión anterior de esa competición → se activa directamente.
- Con versión anterior → comparación walk-forward REAL: ambas versiones
  (candidata y activa) se evalúan sobre los MISMOS partidos reales,
  estrictamente POSTERIORES al `training_end` del candidato (nunca
  vistos por él durante el entrenamiento; la versión anterior, al
  entrenarse antes en el tiempo, tampoco pudo haberlos usado). Métrica
  de decisión: Brier score (nunca accuracy, regla fija del proyecto).
  Menos de 20 partidos fuera de muestra → no se promociona (ante la
  duda, se queda la versión ya en producción).
- El candidato NUNCA se borra, gane o pierda la comparación -- solo
  queda `is_active=False` si pierde.
- `prediction_service.py::generate_predictions_for_competition` ahora
  elige la versión ACTIVA (`is_active.isnot(False)`, trata `None` como
  activa para no romper despliegues con historial previo a este cambio),
  nunca simplemente la más reciente.

### 21.5 Nuevo frontend: "Rendimiento del modelo"

Sustituye "Histórico de jornadas" + "Estadísticas" (dos páginas
separadas que medían lo mismo desde ángulos distintos) por una única
página (`/model`) con 4 pestañas (`ModelPerformanceTabs.tsx`, estado
local, sin recargar red al cambiar de pestaña):

- **Resumen global**: KPIs ya existentes (`StatsOverview`) +
  `PerformanceEvolutionChart` (nuevo): beneficio teórico acumulado
  (stake=1, mejor predicción de cada partido) ordenado por fecha,
  excluyendo "void" de la curva.
- **Jornadas**: `HistoryBoard.tsx` extendido -- con "Todas" las ligas
  (por defecto) se sigue viendo la última jornada de cada una, igual que
  antes; al elegir UNA liga concreta aparecen selectores reales de
  Temporada y Jornada (histórico profundo de esa liga, `getHistory(code,
  500)`, agrupado client-side por `seasonLabelFor()`, misma regla de
  corte en julio que el backend). Columna EV añadida. Colores de estado
  ampliados: gris para pendiente Y anulada (texto distinto), amarillo
  para media ganancia/pérdida.
- **Competiciones y mercados**: `ModelPerformanceBoard.tsx` (sin
  cambios de fondo, ya desglosaba por competición y mercado).
- **Versiones del modelo** (nueva): `ModelVersionsBoard.tsx` -- lista de
  `ModelVersion` por competición, con ventana de entrenamiento,
  calibración, predicciones liquidadas y Brier real desde que esa
  versión entró en producción (unido por `model_version_id` con
  `/models/performance`), y si está ACTIVA o fue rechazada por no
  mejorar fuera de muestra.

`/history` queda como una redirección a `/model?tab=jornadas` (nunca se
borra el enlace, por si algo externo lo apuntaba); `/history/[id]`
(detalle de partido, sección 21.6) sigue viviendo donde estaba.

### 21.6 Detalle de partido ("qué sabía el sistema antes del partido")

`/history/[id]` ya mostraba TODAS las candidatas (no solo la mejor,
marcada con ★), la justificación por factor ("¿por qué predijo esto?"),
probabilidad del modelo, cuota, edge, CLV, resultado real y acierto/
fallo -- se añadieron las dos columnas que faltaban del pedido:
**Probabilidad calibrada** y **EV**, más el mismo tratamiento de colores
de estado (ANULADA/media ganancia-pérdida) que "Jornadas".

### 21.7 Verificación

Backend: **497 tests pasan** (466 ya existentes + 31 nuevos: estados de
partido, `_upsert_match` respetando `record.status`, liquidación "void"
y su exclusión de todas las métricas reales, orden de `refresh()`, y el
gate de promoción completo -- primera versión se activa sola, candidato
peor nunca sustituye a la activa, candidato sin suficiente fuera de
muestra no se promociona). Frontend: `npx tsc --noEmit` y `npm run
build` limpios, 16 rutas generadas correctamente.

### 21.8 Limitaciones conocidas (pedidas explícitamente en la entrega)

- **Tarjetas como mercado**: sigue sin existir (decisión confirmada con
  el usuario en esta misma sesión). Ninguna casa agregada por The Odds
  API cotiza tarjetas para estas ligas -- generar predicciones sin
  cuota real violaría el principio de este sistema de nunca mostrar
  "oportunidades" sin mercado real detrás. Córners sí tiene mercado real
  y sigue implementado.
- **`half_won`/`half_lost`**: el esquema de liquidación los soporta
  (`settlement_status`), pero ningún mercado de este sistema (1X2,
  over/under, doble oportunidad, BTTS, corners) produce hoy un
  resultado parcial -- no hay líneas de hándicap asiático ni mercados
  con "push". Quedan listos para el día que se añada un mercado que sí
  los necesite.
- **Estado "live"**: no se distingue de "scheduled" a propósito (ver
  21.3) -- no hay nada en este sistema (sin apuestas en vivo) que
  consuma ese detalle todavía.
- **Gate de promoción**: la comparación walk-forward usa Brier score
  agregado sobre los mercados centrales (1X2/over-under/BTTS/doble
  oportunidad) con probabilidad SIN calibrar (misma base para ambas
  versiones, para no mezclar "mejor modelo" con "mejor calibración
  disponible en cada una"); corners no entra en la comparación de
  promoción todavía.
